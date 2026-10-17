"""Mode video : une video envoyee au bot Telegram devient une video Flash Monde.

Envoie la video au bot (le fichier, pas un lien) avec une legende :
    autorisee            -> « Images : X · avec autorisation » (le nom de l'auteur n'est jamais affiche)
    autorisee tiktok     -> « Images : TikTok · avec autorisation » (aussi : instagram, facebook, telegram, youtube)
    officielle Kremlin   -> « Images : Kremlin · vidéo officielle »
Tout autre mot de la legende sert d'indice sur le sujet (ex. « autorisee frappe sur Kharkiv »).
Sans « autorisee » ni « officielle », la video est refusee.

Etapes : telechargement, transcription des paroles (faster-whisper), verification que l'evenement
est rapporte par au moins 2 medias de nos flux, texte de contexte et traduction par Claude,
voix, montage vertical, validation sur Telegram, livraison (et bouton YouTube).
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import cluster, collect, hashtags, montage_video, pays, redaction, telegram, voix

RESEAUX = {"x": "X", "twitter": "X", "tiktok": "TikTok", "instagram": "Instagram", "insta": "Instagram",
           "facebook": "Facebook", "fb": "Facebook", "telegram": "Telegram", "youtube": "YouTube",
           "snapchat": "Snapchat", "threads": "Threads", "bluesky": "Bluesky"}
TAILLE_MAX = 20 * 1024 * 1024          # limite de Telegram pour les bots
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]

CONSIGNES = """Tu es secrétaire de rédaction de Flash Monde, flash d'information vidéo en français (politique, géopolitique, diplomatie, grands faits d'ordre public).
On t'envoie une vidéo d'actualité filmée sur place : l'indice donné par le rédacteur, la transcription de ses paroles (langue d'origine, avec des numéros de segments) et une liste d'articles récents de médias.

1. Identifie l'événement montré. Retiens les numéros des articles qui rapportent CE MÊME événement (même lieu, même fait, même jour).
   "confirme" vaut true seulement si au moins 2 articles de médias DIFFÉRENTS le rapportent. Sinon false, et laisse titre et texte vides.
2. Si confirmé, écris :
   - "titre" : style d'agence, 60 caractères maximum, sujet + verbe + complément, au présent, sans point final.
   - "texte" : 2 phrases courtes lues à voix haute (220 caractères maximum) qui donnent le contexte. UNIQUEMENT des faits présents dans au moins deux des articles retenus, reformulés. Ton neutre ; attribue les affirmations de chaque camp (« selon Kiev »). Écris ce qui se prononce (« pour cent », pas « % »). Ne décris pas l'image et ne dis pas « cette vidéo ».
   - "pays" : codes ISO 3166-1 alpha-3, le principal en premier.
   - "hashtags" : 2 hashtags thématiques français sans accent (jamais de pays ni de personne).
3. "traduction" : pour chaque segment de paroles utile, sa traduction française courte et fidèle, entre guillemets français. Si les paroles sont déjà en français, recopie-les en corrigeant seulement les fautes évidentes. Ignore les segments vides, les bruits et les répétitions. Ne traduis jamais un juron ni une insulte ni des propos haineux : mets « [propos inaudibles] ».
   "politique" : true si l'événement relève de la politique, d'un conflit, de la diplomatie, d'une manifestation, ou d'un fait divers marquant d'ordre public ou de sécurité (émeutes, pillages, violences urbaines, attaque, fusillade, attentat, prise d'otages). false pour le sport, la culture, l'économie, un accident ou un fait divers ordinaire.
   Fait divers : jamais le nom d'un suspect, d'une victime ou d'un mineur ; présomption d'innocence (« un suspect », « selon le parquet ») ; aucun détail macabre.

Réponds uniquement avec un objet JSON :
{"confirme": true, "articles": [3, 7], "politique": true, "titre": "...", "texte": "...", "pays": ["UKR"], "hashtags": ["#Guerre", "#Drones"], "traduction": [{"segment": 0, "fr": "« ... »"}]}"""


def _sans_accents(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


def lire_legende(legende: str) -> dict | None:
    """Type d'autorisation, credit a afficher et indice. None si l'autorisation n'est pas indiquee."""
    brut = (legende or "").strip()
    mots = brut.split()
    nus = [_sans_accents(m).strip(".,:;!") for m in mots]
    for k, m in enumerate(nus):
        if m.startswith("autoris") or m == "accord":
            reseau = next((RESEAUX[n] for n in nus[k + 1:k + 3] if n in RESEAUX), None) or \
                     next((RESEAUX[n] for n in nus if n in RESEAUX), "X")
            reste = [w for w, n in zip(mots, nus) if not (n.startswith("autoris") or n in RESEAUX or n == "accord")]
            return {"type": "autorisee", "credit": f"Images : {reseau} · avec autorisation",
                    "reseau": reseau, "indice": " ".join(reste)}
        if m.startswith("officiel"):
            apres = " ".join(mots[k + 1:])
            if "," in apres:                        # « officielle Maison-Blanche, discours de Trump »
                source, indice = apres.split(",", 1)
            else:                                   # « officielle Kremlin rencontre Poutine Xi » : 1er mot
                source, indice = (apres.split(" ", 1) + [""])[:2]
            source = source.strip(" .:;") or "source officielle"
            indice = " ".join(mots[:k]) + " " + indice
            return {"type": "officielle", "credit": f"Images : {source} · vidéo officielle",
                    "reseau": source, "indice": indice.strip()}
    return None


def transcrire(fichier: Path) -> tuple:
    """(langue, [(debut, fin, texte)]). Vide si pas de paroles ou si faster-whisper est absent."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  ! faster-whisper absent : pas de transcription")
        return "", []
    try:
        # le son est decode par FFmpeg (et non par le module PyAV, dont certaines versions plantent)
        brut = subprocess.run(["ffmpeg", "-v", "error", "-i", str(fichier), "-vn", "-ac", "1", "-ar", "16000",
                               "-f", "f32le", "-"], capture_output=True, check=True).stdout
        son = np.frombuffer(brut, np.float32)
        if son.size < 16000:
            return "", []
        modele = WhisperModel("small", device="cpu", compute_type="int8")
        segments, info = modele.transcribe(son, vad_filter=True, beam_size=3)
        out = [(round(s.start, 2), round(s.end, 2), s.text.strip()) for s in segments if s.text.strip()]
        return (info.language if out else ""), out
    except Exception as err:  # sans transcription, la video reste faisable (sans traduction)
        print(f"  ! transcription impossible ({str(err)[:120]}) : vidéo sans traduction")
        return "", []


def _candidats(articles: list, indice: str, paroles: str, n: int = 45) -> list:
    """Articles les plus proches de l'indice et des paroles (les plus recents si rien ne correspond)."""
    cles = cluster.jetons(indice + " " + paroles)
    notes = []
    for a in articles:
        commun = len(cles & cluster.jetons(a.titre + " " + a.resume[:200])) if cles else 0
        notes.append((commun, a.date, a))
    notes.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [a for _, _, a in notes[:n]]


def _objet_json(brut: str) -> dict:
    m = re.search(r"\{.*\}", brut, re.S)
    if not m:
        raise ValueError("réponse illisible du modèle")
    return json.loads(m.group(0))


def analyser(cfg: dict, indice: str, langue: str, segments: list) -> tuple:
    """Verification et redaction par Claude. Renvoie (resultat, articles retenus)."""
    fen = max(cfg["edition"].get("fenetre_matin_heures", 12), 18)
    articles, _ = collect.collecter(cfg["sources"], fen, cfg["edition"].get("exclure", []))
    paroles = " ".join(t for _, _, t in segments)
    choix = _candidats(articles, indice, paroles)
    liste = "\n".join(f"[{i}] ({a.source}) {a.titre} — {a.resume[:220]}" for i, a in enumerate(choix))
    seg = "\n".join(f"{i}. [{d:.1f}-{f:.1f}s] {t}" for i, (d, f, t) in enumerate(segments)) or "(pas de paroles)"
    contenu = (f"Indice du rédacteur : {indice or '(aucun)'}\nLangue des paroles : {langue or 'aucune'}\n\n"
               f"Transcription :\n{seg}\n\nArticles récents :\n{liste or '(aucun)'}")
    r = _objet_json(redaction._appeler(CONSIGNES, contenu, cfg["redaction"]["modele"]))
    retenus = [choix[i] for i in r.get("articles") or [] if isinstance(i, int) and 0 <= i < len(choix)]
    # garde-fou : on recompte nous-memes les medias differents
    if len(cluster.medias(retenus)) < cfg["edition"].get("min_sources", 2):
        r["confirme"] = False
    return r, retenus


def traiter(cfg: dict, args, publier_youtube, archiver, memoriser_vus) -> int:
    """Traite toutes les videos en attente. Renvoie 0 (les erreurs d'une video sont signalees sur Telegram)."""
    if not telegram.configure():
        print("Telegram non configuré : rien à traiter.")
        return 0
    fuseau = ZoneInfo(cfg["chaine"]["fuseau"])
    faites = set()
    while True:
        attente = [v for v in telegram.videos_recues() if v["message_id"] not in faites]
        if not attente:
            break
        for v in attente:
            faites.add(v["message_id"])
            try:
                _une_video(cfg, args, v, fuseau, publier_youtube, archiver, memoriser_vus)
            except Exception as err:
                print(f"  ! vidéo {v['message_id']} : {err}")
                telegram.repondre(v["message_id"], f"⚠️ Vidéo non traitée : {str(err)[:300]}\n"
                                                   "Renvoie-la si besoin.")
            finally:
                telegram.retirer(v["message_id"])
    if not faites:
        print("Aucune vidéo en attente.")
    return 0


def _une_video(cfg, args, v, fuseau, publier_youtube, archiver, memoriser_vus) -> None:
    mid = v["message_id"]
    lec = lire_legende(v.get("legende", ""))
    if lec is None:
        telegram.repondre(mid, "Vidéo non traitée : précise l'autorisation dans la légende, puis renvoie-la.\n"
                               "• « autorisée » (images d'un réseau social, X par défaut ; ajoute "
                               "« tiktok », « instagram »… si besoin)\n"
                               "• « officielle Kremlin » (ou Maison-Blanche, Élysée…)\n"
                               "Tu peux ajouter quelques mots sur le sujet : « autorisée frappe sur Kharkiv ».")
        return
    if v.get("taille", 0) > TAILLE_MAX:
        telegram.repondre(mid, "Vidéo trop lourde : Telegram limite les bots à 20 Mo. Envoie-la comme vidéo "
                               "(pas comme fichier), ou une version plus courte.")
        return
    ici = datetime.now(fuseau)
    dossier = Path("sorties") / f"{ici:%Y-%m-%d_%Hh%M}_video_{mid}"
    travail = dossier / "travail"
    travail.mkdir(parents=True, exist_ok=True)
    telegram.repondre(mid, "📹 Vidéo reçue : transcription et vérification en cours…")

    source = telegram.telecharger(v["file_id"], travail / "source.mp4")
    info = montage_video.infos_video(source)
    print(f"  vidéo {mid} : {info['duree']:.0f} s, {info['w']}x{info['h']}, son : {info['son']}")
    langue, segments = transcrire(source) if info["son"] else ("", [])
    print(f"  paroles : {len(segments)} segment(s), langue {langue or '-'}")

    r, retenus = analyser(cfg, lec["indice"], langue, segments)
    if not r.get("confirme"):
        telegram.repondre(mid, "❌ Événement non confirmé par au moins 2 médias de nos sources : rien n'est "
                               "publié. Tu pourras la renvoyer plus tard, quand les médias en parleront "
                               "(ajoute quelques mots sur le sujet dans la légende pour m'aider).")
        return
    if r.get("politique") is False:
        telegram.repondre(mid, "Vidéo écartée : le sujet ne relève ni de la politique ni d'un grand fait d'ordre public.")
        return
    titre = (r.get("titre") or "").strip().rstrip(".")
    texte = (r.get("texte") or "").strip()
    if not titre or not texte:
        raise RuntimeError("titre ou texte manquant dans la réponse de Claude")
    sources = sorted({a.source for a in retenus})

    traduction = []
    for t in r.get("traduction") or []:
        i = t.get("segment")
        if isinstance(i, int) and 0 <= i < len(segments) and (t.get("fr") or "").strip():
            d, f, _ = segments[i]
            traduction.append((d, f, t["fr"].strip()))

    # voix
    v_cfg = cfg["voix"]
    fournisseur = voix.choisir_fournisseur(v_cfg)
    audio = travail / "voix.mp3"
    utilise, mots = voix.dire(texte, audio, v_cfg, fournisseur)
    if fournisseur in ("azure", "elevenlabs") and utilise == "silence":
        raise RuntimeError(f"la voix a échoué ({voix.derniere_erreur})")
    duree_voix = voix.duree(audio)

    d = datetime.now(fuseau)
    date = f"{d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]} {d.year}"
    video = montage_video.monter(source, audio, duree_voix, mots, traduction, titre, date, lec["credit"],
                                 travail, dossier / "video.mp4", Path("assets/fonts"))
    image = montage_video.couverture(video, dossier / "couverture.jpg")

    sujet = {"titre": titre, "texte": texte, "sources": sources, "iso": [c.upper() for c in r.get("pays") or []],
             "hashtags": redaction.valider_hashtags(r.get("hashtags")), "personne": None,
             "liens": {s: next((a.lien for a in retenus if a.source == s), "") for s in sources},
             "sig": cluster.signature(retenus), "articles": retenus}
    sujet["pays"] = pays.nom_iso(sujet["iso"][0]) if sujet["iso"] else ""
    tags = hashtags.construire([sujet], pays.nom_iso, int(cfg.get("publication", {}).get("hashtags", 10)))
    lignes = [f"• {titre}", "", texte, "", "Sources :"]
    lignes += [f"- {s}" + (f" : {sujet['liens'][s]}" if sujet["liens"].get(s) else "") for s in sources]
    lignes += ["", lec["credit"] + ". Voix et montage générés automatiquement.", " ".join(tags)]
    legende = "\n".join(lignes)
    (dossier / "legende.txt").write_text(legende, encoding="utf-8")
    for f in travail.iterdir():
        f.unlink()
    travail.rmdir()

    # validation : rien ne part sans ton accord
    telegram.video(video, f"📹 {titre}\n{lec['credit']}\n\nÀ vérifier avant publication.")
    choix = telegram.demander("Garder cette vidéo ?", [("ok", "✅ Garder"), ("non", "🗑 Jeter")],
                              int(cfg.get("validation", {}).get("delai_minutes", 30)))
    if choix != "ok":
        telegram.message("Vidéo jetée." if choix == "non" else "Pas de réponse : vidéo jetée.")
        return
    try:
        telegram.photo(image, "Couverture, à choisir comme miniature sur TikTok")
    except Exception:
        pass
    telegram.message(legende)
    memoriser_vus([sujet])
    id_yt = publier_youtube(cfg.get("youtube") or {}, video, image, [sujet], legende, tags, False)
    archiver([sujet], id_yt, d, False)
