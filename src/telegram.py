"""Telegram : validation des sujets avant fabrication, puis envoi de la video."""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

import requests

from . import attente


def configure() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def _url(methode: str) -> str:
    return f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{methode}"


def _api(methode: str, **kw):
    r = requests.post(_url(methode), timeout=300, **kw)
    r.raise_for_status()
    return r.json()


def _appel(methode: str, donnees: dict, timeout: int = 60, silencieux: bool = False):
    """Appel JSON. En mode silencieux, une erreur est ignoree (ex. : message deja a jour)."""
    try:
        r = requests.post(_url(methode), json=donnees, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        if silencieux:
            return None
        raise


def message(texte: str) -> None:
    _api("sendMessage", data={
        "chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": texte[:4000],
        "disable_web_page_preview": "true",
    })


def photo(fichier: Path, legende: str = "") -> None:
    with open(fichier, "rb") as f:
        _api("sendPhoto", data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": legende[:1000]},
             files={"photo": f})


def video(fichier: Path, legende: str) -> None:
    with open(fichier, "rb") as f:
        _api("sendVideo", data={
            "chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": legende[:1000],
            "supports_streaming": "true",
        }, files={"video": f})


# ---------------------------------------------------------------- videos envoyees au bot
# Une video envoyee au bot (depuis X, etc.) ne doit jamais etre perdue : chaque lecture des
# messages Telegram la met de cote dans data/videos_attente.json, traitee ensuite par « --videos ».

F_ATTENTE = Path("data/videos_attente.json")
TYPES = ["message", "callback_query"]


def _lire_attente() -> list:
    try:
        return json.loads(F_ATTENTE.read_text(encoding="utf-8")) if F_ATTENTE.exists() else []
    except ValueError:
        return []


def _ecrire_attente(liste: list) -> None:
    F_ATTENTE.parent.mkdir(exist_ok=True)
    F_ATTENTE.write_text(json.dumps(liste, ensure_ascii=False, indent=1), encoding="utf-8")


def _mettre_de_cote(updates: list) -> int:
    """Garde les videos envoyees dans notre conversation. Renvoie le nombre de nouvelles."""
    chat = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    attente = _lire_attente()
    connus = {v["message_id"] for v in attente}
    nouvelles = 0
    for u in updates:
        if u.get("callback_query"):
            _noter_clic(u["callback_query"])
            continue
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != chat:
            continue
        fichier = m.get("video") or m.get("animation")
        doc = m.get("document") or {}
        if not fichier and str(doc.get("mime_type", "")).startswith("video/"):
            fichier = doc
        if not fichier or m["message_id"] in connus:
            continue
        attente.append({"message_id": m["message_id"], "file_id": fichier["file_id"],
                        "taille": fichier.get("file_size", 0), "duree": fichier.get("duration", 0),
                        "legende": m.get("caption", ""), "date": m.get("date", 0)})
        connus.add(m["message_id"])
        nouvelles += 1
    if nouvelles:
        _ecrire_attente(attente)
    return nouvelles


# ---------------------------------------------------------------- decisions sans limite de temps
# Un clic est note dans data/attente.json des qu'une tache lit Telegram (edition, video, reprise).

def _noter_clic(cq: dict) -> None:
    chat = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if str((cq.get("message") or {}).get("chat", {}).get("id")) != chat:
        return
    donnee = cq.get("data", "")
    jeton, _, action = donnee.partition(":")
    rec = attente.trouver(jeton)
    repondre = lambda t: _appel("answerCallbackQuery", {"callback_query_id": cq["id"], "text": t}, silencieux=True)
    if rec is None:
        repondre("Bouton expiré")
        return
    if rec.get("decision"):
        repondre("Déjà pris en compte")
        return
    morceaux = action.split(":")
    if rec["type"] == "edition" and morceaux[0] in ("k", "x"):
        i = morceaux[1]
        etat = rec.get("etat", {})
        etat[i] = morceaux[0] == "k"
        attente.modifier(jeton, etat=etat)
        _appel("editMessageReplyMarkup", {"chat_id": chat, "message_id": rec["messages"][i],
                                          "reply_markup": _clavier(jeton, int(i), etat[i])}, silencieux=True)
        repondre("Gardé" if etat[i] else "Écarté")
    elif rec["type"] == "edition" and morceaux[0] in ("go", "stop"):
        attente.modifier(jeton, decision=morceaux[0])
        _appel("editMessageReplyMarkup", {"chat_id": chat, "message_id": rec["id_fin"],
                                          "reply_markup": {"inline_keyboard": []}}, silencieux=True)
        if morceaux[0] == "go":
            n = sum(1 for v in rec.get("etat", {}).values() if v)
            repondre("C'est parti")
            _appel("sendMessage", {"chat_id": chat, "text": f"✅ Noté : vidéo avec {n} sujet(s), prête dans "
                                   "10 minutes environ." if n else "Aucun sujet gardé : édition annulée."},
                   silencieux=True)
        else:
            repondre("Édition annulée")
            _appel("sendMessage", {"chat_id": chat, "text": "Édition annulée."}, silencieux=True)
    elif rec["type"] == "youtube":
        libelle = dict(rec.get("choix", [])).get(morceaux[0], morceaux[0])
        attente.modifier(jeton, decision=morceaux[0])
        repondre(libelle)
        _appel("editMessageText", {"chat_id": chat, "message_id": rec["id_question"],
                                   "text": f"{rec['question']}\n→ {libelle}"}, silencieux=True)


def proposer_sujets(sujets: list, entete: str, extra: dict) -> str:
    """Envoie les sujets avec leurs boutons et les met en attente (sans limite de temps). Renvoie le jeton."""
    chat = str(os.environ["TELEGRAM_CHAT_ID"])
    jeton = secrets.token_hex(3)
    _appel("sendMessage", {"chat_id": chat, "text": entete})
    messages = {}
    for i, s in enumerate(sujets):
        suite = "🔁 Suite · " if s.get("suite") is not None else ""
        texte = f"{i + 1}. {suite}{s['titre']}\n\n{s['texte']}\n\nSources : {', '.join(s['sources'])}"
        m = _appel("sendMessage", {"chat_id": chat, "text": texte, "reply_markup": _clavier(jeton, i, True)})
        messages[str(i)] = m["result"]["message_id"]
    fin = _appel("sendMessage", {
        "chat_id": chat,
        "text": "Tous les sujets sont gardés par défaut. Écarte ceux qui ne te conviennent pas, puis lance la "
                "vidéo, quand tu veux (dans les 24 h). Elle sera prête une dizaine de minutes après.",
        "reply_markup": {"inline_keyboard": [[
            {"text": "▶️ Lancer la vidéo", "callback_data": f"{jeton}:go"},
            {"text": "🗑 Annuler l'édition", "callback_data": f"{jeton}:stop"},
        ]]},
    })
    attente.ajouter({"type": "edition", "jeton": jeton, "messages": messages, "id_fin": fin["result"]["message_id"],
                     "etat": {str(i): True for i in range(len(sujets))}, **extra})
    return jeton


def poser_question(question: str, choix: list, extra: dict) -> str:
    """Question a boutons [(code, libelle)], valable 24 h. Renvoie le jeton."""
    chat = str(os.environ["TELEGRAM_CHAT_ID"])
    jeton = secrets.token_hex(3)
    m = _appel("sendMessage", {"chat_id": chat, "text": question, "reply_markup": {"inline_keyboard": [[
        {"text": libelle, "callback_data": f"{jeton}:{code}"} for code, libelle in choix]]}})
    attente.ajouter({"type": "youtube", "jeton": jeton, "question": question, "choix": choix,
                     "id_question": m["result"]["message_id"], **extra})
    return jeton


def attendre(jeton: str, minutes: float) -> dict | None:
    """Attend un peu une decision (si tu es la). Renvoie l'enregistrement decide, ou None (reste en attente)."""
    limite = time.time() + minutes * 60
    offset = _vider()
    while True:
        rec = attente.trouver(jeton)
        if rec is None or rec.get("decision"):
            return rec
        if time.time() >= limite:
            return None
        params = {"timeout": max(1, min(30, int(limite - time.time()))), "allowed_updates": TYPES}
        if offset is not None:
            params["offset"] = offset
        try:
            maj = _appel("getUpdates", params, timeout=45)["result"]
        except Exception:
            time.sleep(5)
            continue
        _mettre_de_cote(maj)
        if maj:
            offset = maj[-1]["update_id"] + 1


def _vider() -> int | None:
    """Lit tous les messages en attente chez Telegram (en gardant les videos) ; renvoie l'offset suivant."""
    offset = None
    for _ in range(20):
        params = {"timeout": 0, "allowed_updates": TYPES}
        if offset is not None:
            params["offset"] = offset
        maj = (_appel("getUpdates", params, silencieux=True) or {}).get("result") or []
        if not maj:
            break
        _mettre_de_cote(maj)
        offset = maj[-1]["update_id"] + 1
    if offset is not None:  # confirme la lecture aupres de Telegram
        _appel("getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": TYPES}, silencieux=True)
    return offset


def videos_recues() -> list:
    """Videos a traiter : celles deja mises de cote + celles arrivees depuis."""
    _vider()
    return _lire_attente()


def retirer(message_id: int) -> None:
    _ecrire_attente([v for v in _lire_attente() if v["message_id"] != message_id])


def telecharger(file_id: str, sortie: Path) -> Path:
    """Telecharge un fichier envoye au bot (Telegram limite les bots a 20 Mo)."""
    info = _appel("getFile", {"file_id": file_id})["result"]
    r = requests.get(f"https://api.telegram.org/file/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{info['file_path']}",
                     timeout=300)
    r.raise_for_status()
    sortie.write_bytes(r.content)
    return sortie


def repondre(message_id: int, texte: str) -> None:
    _appel("sendMessage", {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": texte[:4000],
                           "reply_to_message_id": message_id, "allow_sending_without_reply": True},
           silencieux=True)


# ---------------------------------------------------------------- validation

def _clavier(jeton: str, i: int, garde: bool) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Gardé" if garde else "Garder", "callback_data": f"{jeton}:k:{i}"},
        {"text": "Écarter" if garde else "❌ Écarté", "callback_data": f"{jeton}:x:{i}"},
    ]]}
