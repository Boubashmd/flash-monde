"""Publication sur YouTube (Shorts) avec l'API YouTube Data v3.

Trois cles, creees une seule fois avec  python outils/autoriser_youtube.py  :
  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
Une video verticale de moins de 3 minutes est classee automatiquement en Short.
"""
from __future__ import annotations

import json
import os
import re
import time
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
        """Ouvre la session d'envoi. Sans risque de doublon : rien n'est cree tant que la video n'est pas envoyee."""
        for essai in range(4):
            try:
                r = requests.post(ENVOI, params={"uploadType": "resumable", "part": "snippet,status"},
                                  headers={"Authorization": f"Bearer {acces}",
                                           "Content-Type": "application/json; charset=UTF-8",
                                           "X-Upload-Content-Type": "video/mp4",
                                           "X-Upload-Content-Length": str(taille)},
                                  data=json.dumps(corps), timeout=120)
                if r.status_code < 500:
                    return r
            except requests.RequestException as err:  # coupure ou lenteur passagere de Google
                if essai == 3:
                    raise RuntimeError(f"YouTube ne répond pas ({err})")
            time.sleep(10 * (essai + 1))
        return r

    r = ouvrir(meta)
    if r.status_code == 400 and "containsSyntheticMedia" in r.text:  # champ refuse : on l'enleve
        del meta["status"]["containsSyntheticMedia"]
        r = ouvrir(meta)
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube a refusé l'envoi : {r.status_code} {r.text[:200]}")
    adresse = r.headers["Location"]

    # envoi par reprise : si la connexion coupe, on demande a YouTube ou il en est et on continue
    # (jamais de nouvelle video en double)
    deja = 0
    for essai in range(5):
        try:
            with open(video, "rb") as f:
                f.seek(deja)
                entetes = {"Authorization": f"Bearer {acces}", "Content-Type": "video/mp4"}
                if deja:
                    entetes["Content-Range"] = f"bytes {deja}-{taille - 1}/{taille}"
                r = requests.put(adresse, data=f, headers=entetes, timeout=900)
            if r.status_code in (200, 201):
                return r.json()["id"]
            if r.status_code < 500 and r.status_code != 308:
                raise RuntimeError(f"envoi de la vidéo refusé : {r.status_code} {r.text[:200]}")
        except requests.RequestException:
            pass
        time.sleep(10 * (essai + 1))
        try:  # ou en est l'envoi ?
            etat = requests.put(adresse, headers={"Authorization": f"Bearer {acces}",
                                                  "Content-Range": f"bytes */{taille}"}, timeout=120)
            if etat.status_code in (200, 201):
                return etat.json()["id"]
            m = re.search(r"bytes=0-(\d+)", etat.headers.get("Range", ""))
            deja = int(m.group(1)) + 1 if m else 0
        except requests.RequestException:
            pass
    raise RuntimeError("envoi de la vidéo interrompu après plusieurs essais")


def lien(video_id: str) -> str:
    return f"https://youtube.com/shorts/{video_id}"


def miniature(video_id: str, image: Path) -> bool:
    """Image de couverture (refusee tant que la chaine n'est pas validee par telephone : sans gravite)."""
    try:
        r = requests.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                          params={"videoId": video_id},
                          headers={"Authorization": f"Bearer {_acces()}", "Content-Type": "image/jpeg"},
                          data=image.read_bytes(), timeout=60)
        return r.status_code < 400
    except Exception:
        return False


def statistiques(ids: list) -> dict:
    """Vues, j'aime et commentaires de chaque video (autorisation « youtube.readonly » necessaire)."""
    acces = _acces()
    out = {}
    for k in range(0, len(ids), 50):
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "statistics,snippet", "id": ",".join(ids[k:k + 50])},
                         headers={"Authorization": f"Bearer {acces}"}, timeout=30)
        if r.status_code == 403:
            raise RuntimeError("autorisation insuffisante : relance outils/autoriser_youtube.py "
                               "puis mets à jour le secret YOUTUBE_REFRESH_TOKEN")
        r.raise_for_status()
        for v in r.json().get("items", []):
            st = v.get("statistics", {})
            out[v["id"]] = {"titre": v["snippet"]["title"], "vues": int(st.get("viewCount", 0)),
                            "jaime": int(st.get("likeCount", 0)), "commentaires": int(st.get("commentCount", 0))}
    return out
