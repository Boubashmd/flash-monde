"""Synthese vocale : ElevenLabs (payant), Edge (gratuit) ou silence (test).

Chaque fichier audio est accompagne des horaires de ses mots, pour des sous-titres
synchronises mot a mot :
- ElevenLabs : horaires exacts renvoyes par l'API (endpoint "with-timestamps") ;
- Edge : evenements "WordBoundary" ;
- sinon : estimation d'apres la longueur des mots et la ponctuation.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import unicodedata
from pathlib import Path

import requests

API = "https://api.elevenlabs.io/v1/text-to-speech"


def duree(fichier: Path) -> float:
    """Duree en secondes, lue avec ffmpeg seul (pas besoin de ffprobe)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(fichier)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise RuntimeError(f"durée illisible pour {fichier.name}")
    h, mn, sec = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(sec)


# ------------------------------------------------------------------ horaires des mots

def _mots(texte: str) -> list:
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", texte)]


def _nu(mot: str) -> str:
    """Mot sans accents ni ponctuation, pour comparer deux decoupages."""
    m = unicodedata.normalize("NFD", mot.lower())
    return "".join(c for c in m if c.isalnum())


def estimer_mots(texte: str, duree_audio: float, debut: float = 0.12, marge_fin: float = 0.15) -> list:
    """Horaires approximatifs, quand le fournisseur de voix n'en donne pas."""
    mots = _mots(texte)
    if not mots:
        return []
    poids = [len(m) + (7 if m[-1] in ".!?…" else 4 if m[-1] in ",;:" else 1.5) for m, _, _ in mots]
    dispo = max(0.5, duree_audio - debut - marge_fin)
    t, out = debut, []
    total = sum(poids)
    for (m, _, _), p in zip(mots, poids):
        d = dispo * p / total
        out.append((m, round(t, 3), round(t + d * 0.9, 3)))
        t += d
    return out


def _depuis_alignement(texte: str, al: dict) -> list | None:
    """Horaires des mots a partir de l'alignement caractere par caractere d'ElevenLabs."""
    try:
        car = al["characters"]
        debuts = al["character_start_times_seconds"]
        fins = al["character_end_times_seconds"]
    except (KeyError, TypeError):
        return None
    lu = "".join(car)
    if lu == texte:
        return [(m, float(debuts[a]), float(fins[b - 1])) for m, a, b in _mots(texte)]
    # texte legerement normalise par l'API : on retrouve chaque mot dans l'ordre
    out, curseur = [], 0
    for m, _, _ in _mots(texte):
        k = lu.find(m, curseur)
        if k < 0:
            return None
        out.append((m, float(debuts[k]), float(fins[k + len(m) - 1])))
        curseur = k + len(m)
    return out


def _recaler(texte: str, reperes: list) -> list | None:
    """Associe les mots reperes par Edge (sans ponctuation, parfois coupes : "L'" + "armée")
    aux mots du texte. Renvoie None en cas de desaccord : les horaires seront estimes."""
    jetons = [(_nu(r[0]), r[1], r[2]) for r in reperes if _nu(r[0])]
    out, j, fin = [], 0, 0.0
    for m, _, _ in _mots(texte):
        cible = _nu(m)
        if not cible:  # signe isole (tiret...) : meme horaire que le mot precedent
            out.append((m, fin, fin))
            continue
        lu, t0, t1 = "", None, fin
        while j < len(jetons) and len(lu) < len(cible):
            n, a, b = jetons[j]
            t0 = a if t0 is None else t0
            lu, t1, j = lu + n, b, j + 1
        if lu != cible:
            return None
        out.append((m, t0, t1))
        fin = t1
    return out


# ------------------------------------------------------------------ fournisseurs

def _reglages(cfg: dict) -> dict:
    r = cfg.get("reglages") or {}
    s = {
        "stability": float(r.get("stabilite", 0.45)),
        "similarity_boost": float(r.get("similarite", 0.75)),
        "style": float(r.get("style", 0.1)),
        "use_speaker_boost": True,
    }
    vitesse = float(r.get("vitesse", 1.0))
    if abs(vitesse - 1.0) > 0.01:
        s["speed"] = vitesse
    return s


def _elevenlabs(texte: str, sortie: Path, cfg: dict, precedent: str | None) -> list | None:
    voix = cfg["elevenlabs_voice_id"]
    entetes = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    corps = {
        "text": texte,
        "model_id": cfg.get("elevenlabs_modele", "eleven_multilingual_v2"),
        "voice_settings": _reglages(cfg),
    }
    if precedent:  # meme ton d'une info a l'autre
        corps["previous_text"] = precedent
    r = requests.post(f"{API}/{voix}/with-timestamps", params={"output_format": "mp3_44100_128"},
                      headers=entetes, json=corps, timeout=120)
    if r.status_code < 400:
        d = r.json()
        sortie.write_bytes(base64.b64decode(d["audio_base64"]))
        al = d.get("alignment") or d.get("normalized_alignment")
        return _depuis_alignement(texte, al) if al else None
    print(f"  ! ElevenLabs avec horaires : {r.status_code} {r.text[:90]} ; essai sans horaires")
    r = requests.post(f"{API}/{voix}", headers={**entetes, "accept": "audio/mpeg"}, json=corps, timeout=120)
    r.raise_for_status()
    sortie.write_bytes(r.content)
    return None


def _edge(texte: str, sortie: Path, cfg: dict) -> list | None:
    import edge_tts

    reperes = []

    async def lancer():
        try:
            comm = edge_tts.Communicate(texte, cfg.get("edge_voix", "fr-FR-HenriNeural"), boundary="WordBoundary")
        except TypeError:  # anciennes versions : mots reperes par defaut
            comm = edge_tts.Communicate(texte, cfg.get("edge_voix", "fr-FR-HenriNeural"))
        with open(sortie, "wb") as f:
            async for bloc in comm.stream():
                if bloc["type"] == "audio":
                    f.write(bloc["data"])
                elif bloc["type"] == "WordBoundary":
                    t0 = bloc["offset"] / 1e7
                    reperes.append((bloc["text"], t0, t0 + bloc["duration"] / 1e7))

    asyncio.run(lancer())
    return _recaler(texte, reperes)


def _silence(texte: str, sortie: Path) -> None:
    secondes = max(2.0, len(texte) / 15)  # environ 15 caracteres par seconde a l'oral
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
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


def dire(texte: str, sortie: Path, cfg: dict, fournisseur: str, precedent: str | None = None) -> tuple:
    """Cree le fichier audio. Renvoie (fournisseur reellement utilise, horaires des mots)."""
    try:
        if fournisseur == "elevenlabs":
            mots = _elevenlabs(texte, sortie, cfg, precedent)
        elif fournisseur == "edge":
            mots = _edge(texte, sortie, cfg)
        else:
            _silence(texte, sortie)
            mots = None
        utilise = fournisseur
    except Exception as err:
        print(f"  ! voix {fournisseur} en échec ({str(err)[:80]}) : segment sans voix")
        _silence(texte, sortie)
        mots, utilise = None, "silence"
    if not mots:
        mots = estimer_mots(texte, duree(sortie))
    return utilise, mots
