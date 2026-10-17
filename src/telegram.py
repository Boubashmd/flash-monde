"""Telegram : validation des sujets avant fabrication, puis envoi de la video."""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

import requests


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


# ---------------------------------------------------------------- validation

def _clavier(jeton: str, i: int, garde: bool) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Gardé" if garde else "Garder", "callback_data": f"{jeton}:k:{i}"},
        {"text": "Écarter" if garde else "❌ Écarté", "callback_data": f"{jeton}:x:{i}"},
    ]]}


def valider(sujets: list[dict], entete: str, delai_minutes: int) -> list[int] | None:
    """Propose les sujets sur Telegram et attend la decision.

    Renvoie la liste des numeros de sujets gardes ([] si l'edition est annulee),
    ou None si personne n'a repondu dans le delai.
    """
    chat = str(os.environ["TELEGRAM_CHAT_ID"])
    jeton = secrets.token_hex(3)  # les boutons des editions precedentes seront ignores

    # on ignore les clics anterieurs a cette edition
    r = _appel("getUpdates", {"offset": -1, "timeout": 0})
    anciens = r.get("result") if isinstance(r.get("result"), list) else []
    offset = anciens[-1]["update_id"] + 1 if anciens else None

    etat = {i: True for i in range(len(sujets))}
    messages = {}
    _appel("sendMessage", {"chat_id": chat, "text": entete})
    for i, s in enumerate(sujets):
        suite = "🔁 Suite · " if s.get("suite") is not None else ""
        texte = f"{i + 1}. {suite}{s['titre']}\n\n{s['texte']}\n\nSources : {', '.join(s['sources'])}"
        m = _appel("sendMessage", {"chat_id": chat, "text": texte, "reply_markup": _clavier(jeton, i, True)})
        messages[i] = m["result"]["message_id"]
    fin = _appel("sendMessage", {
        "chat_id": chat,
        "text": f"Tous les sujets sont gardés par défaut. Écarte ceux qui ne te conviennent pas, "
                f"puis lance la vidéo. Sans réponse sous {delai_minutes} min, l'édition est abandonnée.",
        "reply_markup": {"inline_keyboard": [[
            {"text": "▶️ Lancer la vidéo", "callback_data": f"{jeton}:go"},
            {"text": "🗑 Annuler l'édition", "callback_data": f"{jeton}:stop"},
        ]]},
    })
    id_fin = fin["result"]["message_id"]

    def clore(texte: str) -> None:
        _appel("editMessageReplyMarkup", {"chat_id": chat, "message_id": id_fin,
                                          "reply_markup": {"inline_keyboard": []}}, silencieux=True)
        _appel("sendMessage", {"chat_id": chat, "text": texte}, silencieux=True)

    limite = time.time() + delai_minutes * 60
    while time.time() < limite:
        params = {"timeout": max(1, min(30, int(limite - time.time()))), "allowed_updates": ["callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            maj = _appel("getUpdates", params, timeout=45)["result"]
        except Exception:
            time.sleep(5)  # coupure reseau passagere : on reessaie
            continue
        for u in maj:
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq or str((cq.get("message") or {}).get("chat", {}).get("id")) != chat:
                continue
            donnee = cq.get("data", "")
            repondre = lambda t: _appel("answerCallbackQuery", {"callback_query_id": cq["id"], "text": t}, silencieux=True)
            if not donnee.startswith(jeton + ":"):
                repondre("Bouton d'une ancienne édition")
                continue
            action = donnee.split(":")[1:]
            if action[0] in ("k", "x"):
                i = int(action[1])
                etat[i] = action[0] == "k"
                _appel("editMessageReplyMarkup", {"chat_id": chat, "message_id": messages[i],
                                                  "reply_markup": _clavier(jeton, i, etat[i])}, silencieux=True)
                repondre("Gardé" if etat[i] else "Écarté")
            elif action[0] == "go":
                gardes = [i for i in etat if etat[i]]
                repondre("C'est parti")
                clore(f"Fabrication lancée avec {len(gardes)} sujet(s)." if gardes else "Aucun sujet gardé : édition annulée.")
                return gardes
            elif action[0] == "stop":
                repondre("Édition annulée")
                clore("Édition annulée.")
                return []
    clore("Pas de réponse dans le délai : édition abandonnée.")
    return None


# ---------------------------------------------------------------- question a boutons

def demander(question: str, choix: list, delai_minutes: int) -> str | None:
    """Pose une question avec des boutons [(code, libelle), ...] et attend la reponse.
    Renvoie le code du bouton choisi, ou None sans reponse dans le delai."""
    chat = str(os.environ["TELEGRAM_CHAT_ID"])
    jeton = secrets.token_hex(3)
    r = _appel("getUpdates", {"offset": -1, "timeout": 0})
    anciens = r.get("result") if isinstance(r.get("result"), list) else []
    offset = anciens[-1]["update_id"] + 1 if anciens else None
    m = _appel("sendMessage", {"chat_id": chat, "text": question, "reply_markup": {"inline_keyboard": [[
        {"text": libelle, "callback_data": f"{jeton}:{code}"} for code, libelle in choix]]}})
    id_msg = m["result"]["message_id"]
    limite = time.time() + delai_minutes * 60
    while time.time() < limite:
        params = {"timeout": max(1, min(30, int(limite - time.time()))), "allowed_updates": ["callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            maj = _appel("getUpdates", params, timeout=45)["result"]
        except Exception:
            time.sleep(5)
            continue
        for u in maj:
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq or str((cq.get("message") or {}).get("chat", {}).get("id")) != chat:
                continue
            donnee = cq.get("data", "")
            if not donnee.startswith(jeton + ":"):
                _appel("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Bouton expiré"}, silencieux=True)
                continue
            code = donnee.split(":", 1)[1]
            libelle = dict(choix).get(code, code)
            _appel("answerCallbackQuery", {"callback_query_id": cq["id"], "text": libelle}, silencieux=True)
            _appel("editMessageText", {"chat_id": chat, "message_id": id_msg,
                                       "text": f"{question}\n→ {libelle}"}, silencieux=True)
            return code
    _appel("editMessageText", {"chat_id": chat, "message_id": id_msg,
                               "text": f"{question}\n→ pas de réponse dans le délai"}, silencieux=True)
    return None
