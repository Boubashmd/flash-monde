"""Photos d'illustration libres de droits, choisies selon le pays.

Fournisseurs : Pixabay (cle gratuite) ou Pexels.
Les photos sont telechargees une fois dans cache/images/<pays>/ et reutilisees :
on n'appelle pas l'API a chaque edition, et on n'affiche jamais une image hebergee ailleurs.
On n'utilise jamais les images des medias sources.
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

import requests

CACHE = Path("cache/images")


def _dossier(pays: str) -> Path:
    import unicodedata

    brut = unicodedata.normalize("NFD", pays.lower()).encode("ascii", "ignore").decode()
    return CACHE / (re.sub(r"[^a-z0-9]+", "-", brut).strip("-") or "monde")


def _resultats_pixabay(requete: str, combien: int) -> list[tuple[str, str, str]]:
    r = requests.get(
        "https://pixabay.com/api/",
        params={
            "key": os.environ["PIXABAY_API_KEY"], "q": requete[:100], "image_type": "photo",
            "orientation": "vertical", "per_page": max(3, combien), "safesearch": "true",
        },
        timeout=30,
    )
    r.raise_for_status()
    return [(str(h["id"]), h["largeImageURL"], h.get("user", "")) for h in r.json().get("hits", [])]


def _resultats_pexels(requete: str, combien: int) -> list[tuple[str, str, str]]:
    r = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": os.environ["PEXELS_API_KEY"]},
        params={"query": requete, "orientation": "portrait", "per_page": combien, "size": "large"},
        timeout=30,
    )
    r.raise_for_status()
    return [
        (str(p["id"]), p["src"].get("portrait") or p["src"]["large2x"], p.get("photographer", ""))
        for p in r.json().get("photos", [])
    ]


FOURNISSEURS = {
    "pixabay": ("PIXABAY_API_KEY", _resultats_pixabay, "Pixabay"),
    "pexels": ("PEXELS_API_KEY", _resultats_pexels, "Pexels"),
}


def _telecharger(pays: str, requete: str, combien: int, fournisseur: str) -> None:
    _, chercher, nom = FOURNISSEURS[fournisseur]
    resultats = chercher(requete, combien)[:combien]
    d = _dossier(pays)
    d.mkdir(parents=True, exist_ok=True)
    credits_f = d / "credits.json"
    credits = json.loads(credits_f.read_text(encoding="utf-8")) if credits_f.exists() else {}
    for ident, url, auteur in resultats:
        fichier = d / f"{fournisseur}-{ident}.jpg"
        fichier.write_bytes(requests.get(url, timeout=60).content)
        credits[fichier.name] = f"{auteur} / {nom}" if auteur else nom
    credits_f.write_text(json.dumps(credits, ensure_ascii=False, indent=1), encoding="utf-8")


def choisir(pays: str, requete: str, cfg: dict) -> tuple[Path | None, str]:
    """Renvoie (chemin de la photo ou None, credit a afficher)."""
    fournisseur = cfg.get("fournisseur")
    if fournisseur not in FOURNISSEURS:
        return None, ""
    d = _dossier(pays)
    photos = sorted(d.glob("*.jpg")) if d.exists() else []
    if not photos:
        cle = FOURNISSEURS[fournisseur][0]
        if not os.getenv(cle):
            return None, ""
        try:
            _telecharger(pays, requete, cfg.get("par_pays", 6), fournisseur)
        except Exception as err:
            msg = str(err).split(" for url")[0]  # ne jamais afficher la cle contenue dans l'URL
            print(f"  ! photo indisponible pour {pays} ({msg[:80]})")
            return None, ""
        photos = sorted(d.glob("*.jpg"))
    if not photos:
        return None, ""
    photo = random.choice(photos)
    credits = {}
    if (d / "credits.json").exists():
        credits = json.loads((d / "credits.json").read_text(encoding="utf-8"))
    return photo, "Photo : " + credits.get(photo.name, fournisseur.capitalize())
