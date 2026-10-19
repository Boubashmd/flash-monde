"""Decisions en attente sur Telegram : sujets a valider et publications YouTube.

Plus de delai de 30 minutes : les boutons restent valables 24 h (duree pendant laquelle Telegram garde
les clics). Chaque clic est note dans data/attente.json par la premiere tache qui lit Telegram ; la
« Veille video » voit la decision et lance la suite (python -m src.main --reprendre).
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

F_ATTENTE = Path("data/attente.json")
DUREE_H = 24


def lire() -> list:
    try:
        return json.loads(F_ATTENTE.read_text(encoding="utf-8")) if F_ATTENTE.exists() else []
    except ValueError:
        return []


def ecrire(liste: list) -> None:
    F_ATTENTE.parent.mkdir(exist_ok=True)
    F_ATTENTE.write_text(json.dumps(liste, ensure_ascii=False, indent=1, default=_serial), encoding="utf-8")


def _serial(o):
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o))


def ajouter(rec: dict) -> None:
    rec.setdefault("cree", datetime.now(timezone.utc).isoformat())
    rec.setdefault("decision", None)
    ecrire([r for r in lire() if r["jeton"] != rec["jeton"]] + [rec])


def trouver(jeton: str) -> dict | None:
    return next((r for r in lire() if r["jeton"] == jeton), None)


def modifier(jeton: str, **champs) -> dict | None:
    liste = lire()
    for r in liste:
        if r["jeton"] == jeton:
            r.update(champs)
            ecrire(liste)
            return r
    return None


def retirer(jeton: str) -> None:
    ecrire([r for r in lire() if r["jeton"] != jeton])


def perime(rec: dict) -> bool:
    return datetime.now(timezone.utc) - datetime.fromisoformat(rec["cree"]) > timedelta(hours=DUREE_H)


def telecharger_artefact(nom: str, fichiers: list, dossier: Path) -> list:
    """Recupere des fichiers d'un artefact GitHub (video gardee 7 jours par l'edition)."""
    depot, jeton = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not depot or not jeton:
        raise RuntimeError("accès aux artefacts GitHub impossible (GITHUB_TOKEN absent)")
    gh = {"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"https://api.github.com/repos/{depot}/actions/artifacts", params={"name": nom},
                     headers=gh, timeout=30)
    r.raise_for_status()
    arts = [a for a in r.json().get("artifacts", []) if not a.get("expired")]
    if not arts:
        raise RuntimeError(f"vidéo introuvable (artefact {nom} expiré ou absent)")
    z = requests.get(arts[0]["archive_download_url"], headers=gh, timeout=300)
    z.raise_for_status()
    dossier.mkdir(parents=True, exist_ok=True)
    sorties = []
    with zipfile.ZipFile(io.BytesIO(z.content)) as archive:
        for f in fichiers:
            cible = dossier / Path(f).name
            cible.write_bytes(archive.read(f))
            sorties.append(cible)
    return sorties
