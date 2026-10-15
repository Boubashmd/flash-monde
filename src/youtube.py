"""Publication sur YouTube (Shorts) avec l'API YouTube Data v3.

Trois cles, creees une seule fois avec  python outils/autoriser_youtube.py  :
  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
Une video verticale de moins de 3 minutes est classee automatiquement en Short.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

JETON = "https://oauth2.googleapis.com/token"
ENVOI = "https://www.googleapis.com/upload/youtube/v3/videos"


def configure() -> bool:
    return all(os.getenv(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))


def _acces() -> str:
    r = requests.post(JETON, data={
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"autorisation YouTube refusée ({r.text[:120]}). "
                           "Relance outils/autoriser_youtube.py et mets à jour YOUTUBE_REFRESH_TOKEN.")
    return r.json()["access_token"]


def _propre(texte: str, maxi: int) -> str:
    """YouTube refuse les caracteres < et > dans les titres et descriptions."""
    texte = re.sub(r"[<>]", "", texte).strip()
    return texte if len(texte) <= maxi else texte[: maxi - 1].rstrip() + "…"


def publier(video: Path, titre: str, description: str, mots_cles: list, confidentialite: str = "public") -> str:
    """Envoie la video et renvoie son lien."""
    acces = _acces()
    meta = {
        "snippet": {
            "title": _propre(titre, 100),
            "description": _propre(description, 4900),
            "tags": [m for m in dict.fromkeys(mots_cles) if m][:20],
            "categoryId": "25",  # Actualites et politique
            "defaultLanguage": "fr",
            "defaultAudioLanguage": "fr",
        },
        "status": {
            "privacyStatus": confidentialite,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # voix de synthese : on le declare
        },
    }
    taille = video.stat().st_size

    def ouvrir(corps: dict):
        return requests.post(ENVOI, params={"uploadType": "resumable", "part": "snippet,status"},
                             headers={"Authorization": f"Bearer {acces}",
                                      "Content-Type": "application/json; charset=UTF-8",
                                      "X-Upload-Content-Type": "video/mp4",
                                      "X-Upload-Content-Length": str(taille)},
                             data=json.dumps(corps), timeout=60)

    r = ouvrir(meta)
    if r.status_code == 400 and "containsSyntheticMedia" in r.text:  # champ refuse : on l'enleve
        del meta["status"]["containsSyntheticMedia"]
        r = ouvrir(meta)
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube a refusé l'envoi : {r.status_code} {r.text[:200]}")
    adresse = r.headers["Location"]
    with open(video, "rb") as f:
        r = requests.put(adresse, data=f, headers={"Authorization": f"Bearer {acces}",
                                                  "Content-Type": "video/mp4"}, timeout=600)
    if r.status_code >= 400:
        raise RuntimeError(f"envoi de la vidéo interrompu : {r.status_code} {r.text[:200]}")
    return f"https://youtube.com/shorts/{r.json()['id']}"
