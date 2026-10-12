"""Synthese vocale : ElevenLabs (payant), Edge (gratuit) ou silence (test)."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path

import requests


def duree(fichier: Path) -> float:
    """Duree en secondes, lue avec ffmpeg seul (pas besoin de ffprobe)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(fichier)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise RuntimeError(f"durée illisible pour {fichier.name}")
    h, mn, sec = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(sec)


def _elevenlabs(texte: str, sortie: Path, cfg: dict) -> None:
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['elevenlabs_voice_id']}",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "accept": "audio/mpeg"},
        json={
            "text": texte,
            "model_id": cfg.get("elevenlabs_modele", "eleven_multilingual_v2"),
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=90,
    )
    r.raise_for_status()
    sortie.write_bytes(r.content)


def _edge(texte: str, sortie: Path, cfg: dict) -> None:
    import edge_tts

    asyncio.run(edge_tts.Communicate(texte, cfg.get("edge_voix", "fr-FR-HenriNeural")).save(str(sortie)))


def _silence(texte: str, sortie: Path) -> None:
    secondes = max(2.0, len(texte) / 15)  # environ 15 caracteres par seconde a l'oral
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", f"{secondes:.2f}", "-c:a", "libmp3lame", str(sortie)],
        check=True,
    )


def choisir_fournisseur(cfg: dict) -> str:
    """Determine une fois pour toutes la voix de l'edition (pas de melange de voix)."""
    f = cfg.get("fournisseur", "elevenlabs")
    if f == "elevenlabs":
        if not os.getenv("ELEVENLABS_API_KEY") or "REMPLACE" in cfg.get("elevenlabs_voice_id", "REMPLACE"):
            print("  ! ElevenLabs non configuré : passage à la voix gratuite Edge")
            f = "edge"
    if f == "edge":
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            print("  ! module edge-tts absent : vidéo sans voix")
            f = "silence"
    return f


def dire(texte: str, sortie: Path, cfg: dict, fournisseur: str) -> str:
    """Cree le fichier audio. Renvoie le fournisseur reellement utilise."""
    try:
        if fournisseur == "elevenlabs":
            _elevenlabs(texte, sortie, cfg)
        elif fournisseur == "edge":
            _edge(texte, sortie, cfg)
        else:
            _silence(texte, sortie)
        return fournisseur
    except Exception as err:
        print(f"  ! voix {fournisseur} en échec ({str(err)[:80]}) : segment sans voix")
        _silence(texte, sortie)
        return "silence"
