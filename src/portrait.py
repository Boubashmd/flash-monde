"""Portrait d'une personnalite publique, depuis Wikimedia Commons (licences libres uniquement).

On cherche la photo principale de sa page Wikipedia, on verifie qu'elle est heberge sur Commons
sous une licence libre qui autorise la reutilisation commerciale (domaine public, CC0, CC BY,
CC BY-SA), puis on la telecharge avec son credit (auteur + licence), qui sera affiche a l'ecran.
Toute photo non libre ou de statut incertain est refusee : la video utilise alors la carte.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

CACHE = Path("cache/portraits")
UA = {"User-Agent": "FlashMonde/1.0 (https://github.com/Boubashmd/flash-monde)"}
LICENCES_OK = re.compile(r"^(cc0|public domain|pd|ogl|cc[ -]by(-sa)?([ -]\d(\.\d)?)?)", re.I)
INTERDIT = re.compile(r"\b(nc|nd|fair use|non-free)\b", re.I)


def _api(hote: str, **params) -> dict:
    params.update(format="json", formatversion=2)
    r = requests.get(f"https://{hote}/w/api.php", params=params, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json()


def _image_de_page(nom: str) -> str | None:
    for langue in ("fr", "en"):
        d = _api(f"{langue}.wikipedia.org", action="query", titles=nom, prop="pageimages",
                 piprop="name", redirects=1)
        for page in d.get("query", {}).get("pages", []):
            if page.get("pageimage"):
                return page["pageimage"]
    return None


def _propre(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def chercher(nom: str) -> tuple[Path | None, str]:
    """Renvoie (chemin de la photo, credit a afficher) ou (None, '') si rien de libre."""
    cle = re.sub(r"[^a-z0-9]+", "-", nom.lower()).strip("-")
    dossier = CACHE / cle
    meta = dossier / "credit.json"
    if meta.exists():
        info = json.loads(meta.read_text(encoding="utf-8"))
        photo = dossier / "photo.jpg"
        return (photo, info["credit"]) if info.get("ok") and photo.exists() else (None, "")
    dossier.mkdir(parents=True, exist_ok=True)

    def refuser(raison: str):
        print(f"  - pas de portrait libre pour {nom} ({raison}) : carte à la place")
        meta.write_text(json.dumps({"ok": False, "raison": raison}, ensure_ascii=False), encoding="utf-8")
        return None, ""

    try:
        fichier = _image_de_page(nom)
        if not fichier:
            return refuser("pas de photo sur Wikipédia")
        d = _api("commons.wikimedia.org", action="query", titles=f"File:{fichier}", prop="imageinfo",
                 iiprop="url|extmetadata", iiurlwidth=1200)
        page = d["query"]["pages"][0]
        if page.get("missing") or not page.get("imageinfo"):
            return refuser("photo hors Commons, statut non libre")
        info = page["imageinfo"][0]
        em = info.get("extmetadata", {})
        licence = _propre(em.get("LicenseShortName", {}).get("value", ""))
        if not LICENCES_OK.match(licence) or INTERDIT.search(licence):
            return refuser(f"licence « {licence or 'inconnue'} »")
        auteur = _propre(em.get("Artist", {}).get("value", "")) or "auteur inconnu"
        contenu = requests.get(info.get("thumburl") or info["url"], headers=UA, timeout=60).content
        (dossier / "photo.jpg").write_bytes(contenu)
        credit = f"Photo : {auteur[:60]} / Wikimedia Commons, {licence}"
        meta.write_text(json.dumps({"ok": True, "credit": credit, "fichier": fichier}, ensure_ascii=False),
                        encoding="utf-8")
        return dossier / "photo.jpg", credit
    except Exception as err:  # reseau, format inattendu : on ne bloque jamais l'edition
        print(f"  - portrait indisponible pour {nom} ({str(err)[:60]})")
        return None, ""
