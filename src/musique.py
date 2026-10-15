"""Musique de fond, tres discrete sous la voix.

Deux possibilites :
- un fichier audio libre de droits depose dans assets/musique/ (mp3, wav, m4a, ogg) : il est
  utilise en boucle. Ne mets que des musiques dont la licence autorise l'usage sur TikTok et
  YouTube (CC0 par exemple), sinon la video peut etre bloquee ;
- sinon, une musique d'ambiance « journal » est composee par le programme a chaque edition
  (accords, basse pulsee, legere percussion). Elle n'appartient a personne : aucun risque de droits.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

DOSSIER = Path(__file__).resolve().parent.parent / "assets" / "musique"

# accords (notes MIDI) et basse, en mineur : tension sobre, ton « actualite »
PROGRESSIONS = [
    [([50, 57, 62, 65], 38), ([46, 53, 58, 62], 34), ([53, 57, 60, 65], 41), ([48, 55, 60, 64], 36)],  # Rém Sib Fa Do
    [([45, 52, 57, 60], 33), ([41, 48, 53, 57], 29), ([48, 52, 55, 60], 36), ([43, 50, 55, 59], 31)],  # Lam Fa Do Sol
    [([52, 59, 64, 67], 40), ([48, 55, 60, 64], 36), ([55, 59, 62, 67], 43), ([50, 57, 62, 66], 38)],  # Mim Do Sol Ré
]


def _hz(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def _boucle(sr: int, rng: np.random.Generator) -> np.ndarray:
    """Huit mesures (quatre accords de deux mesures) qui se repetent sans couture : on compose
    trois tours et on garde celui du milieu, dont les fondus raccordent avec ses voisins."""
    bpm = float(rng.uniform(88, 100))
    temps = 60.0 / bpm
    accord_d = 8 * temps
    prog = PROGRESSIONS[int(rng.integers(len(PROGRESSIONS)))]
    tour = int(round(accord_d * len(prog) * sr))
    n = 3 * tour
    t = np.arange(n) / sr
    nappe, basse, arpege, percu = (np.zeros(n) for _ in range(4))
    fondu = 0.6
    for k in range(3 * len(prog)):
        notes, racine = prog[k % len(prog)]
        debut = k * tour / sr / len(prog)
        # nappe : partiels doux, deux voix legerement desaccordees, fondu enchaine entre accords
        i0, i1 = max(0, int((debut - fondu) * sr)), min(n, int((debut + accord_d + fondu) * sr))
        tz = t[i0:i1]
        env = np.clip((tz - debut + fondu) / fondu, 0, 1) * np.clip((debut + accord_d + fondu - tz) / fondu, 0, 1)
        son = np.zeros(i1 - i0)
        for note in notes:
            f = _hz(note)
            for h in range(1, 8):
                for desac in (-0.0025, 0.0025):
                    son += np.sin(2 * np.pi * f * h * (1 + desac) * tz + h * 1.3) / h ** 1.3
        nappe[i0:i1] += son * env ** 2
        # basse pulsee en croches (harmoniques pour rester audible sur un telephone)
        for c in range(16):
            t0 = debut + c * temps / 2
            j0, j1 = int(t0 * sr), min(n, int((t0 + 0.45) * sr))
            u = t[j0:j1] - t0
            f = _hz(racine)
            force = (1.0, 0.5, 0.7, 0.5)[c % 4]
            onde = np.sin(2 * np.pi * f * u) + 0.6 * np.sin(4 * np.pi * f * u) + 0.3 * np.sin(6 * np.pi * f * u)
            basse[j0:j1] += force * onde * np.exp(-u / 0.2) * np.clip(u / 0.006, 0, 1)
        # arpege clair, deux octaves au-dessus : donne le mouvement « fil d'info »
        motif = [0, 2, 1, 3, 2, 1, 3, 2]
        for c in range(16):
            t0 = debut + c * temps / 2
            j0, j1 = int(t0 * sr), min(n, int((t0 + 0.5) * sr))
            u = t[j0:j1] - t0
            f = _hz(notes[motif[c % 8]] + 24)
            onde = sum(np.sin(2 * np.pi * f * h * u) / h ** 2 for h in (1, 2, 3))
            arpege[j0:j1] += onde * np.exp(-u / 0.18) * np.clip(u / 0.004, 0, 1)
        # percussion legere : grosse caisse sur 1 et 3, charleston sur les contretemps
        for c in range(8):
            t0 = debut + c * temps
            if c % 2 == 0:
                j0, j1 = int(t0 * sr), min(n, int((t0 + 0.3) * sr))
                u = t[j0:j1] - t0
                phase = 2 * np.pi * (70 * u + (160 - 70) * 0.04 * (1 - np.exp(-u / 0.04)))
                percu[j0:j1] += np.sin(phase) * np.exp(-u / 0.12)
            j0, j1 = int((t0 + temps / 2) * sr), min(n, int((t0 + temps / 2 + 0.06) * sr))
            bruit = np.diff(rng.standard_normal(j1 - j0), prepend=0.0)  # filtre passe-haut grossier
            percu[j0:j1] += 0.25 * bruit * np.exp(-(t[j0:j1] - t0 - temps / 2) / 0.018)

    def norm(x):
        return x / (np.abs(x).max() or 1)

    m = 0.55 * norm(nappe) + 0.32 * norm(basse) + 0.3 * norm(arpege) + 0.14 * norm(percu)
    # on laisse la place a la voix : on adoucit au-dessus de 4 kHz et sous 60 Hz
    spectre = np.fft.rfft(m)
    freqs = np.fft.rfftfreq(len(m), 1 / sr)
    spectre *= 1 / np.sqrt(1 + (freqs / 4000.0) ** 4) / np.sqrt(1 + (60.0 / np.maximum(freqs, 1)) ** 4)
    m = np.fft.irfft(spectre, len(m))[tour:2 * tour]
    return np.tanh(1.2 * m / (np.abs(m).max() or 1)).astype(np.float32)


def _fichier(sr: int, rng: np.random.Generator) -> np.ndarray | None:
    fichiers = sorted(f for f in DOSSIER.glob("*") if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"))
    if not fichiers:
        return None
    f = fichiers[int(rng.integers(len(fichiers)))]
    brut = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                          capture_output=True).stdout
    son = np.frombuffer(brut, np.float32).copy()
    return son if len(son) > sr * 5 else None


def piste(duree: float, sr: int = 48000, graine: int = 0) -> np.ndarray:
    """Musique de la duree voulue, normalisee (valeur efficace ~0.2), avec fondus de debut et de fin."""
    rng = np.random.default_rng(graine)
    son = _fichier(sr, rng)
    if son is None:
        son = _boucle(sr, rng)
    n = int(duree * sr)
    son = np.tile(son, n // len(son) + 1)[:n]
    rms = float(np.sqrt(np.mean(son ** 2))) or 1.0
    son = son * (0.2 / rms)
    entree, sortie = int(0.8 * sr), int(1.8 * sr)
    son[:entree] *= np.linspace(0, 1, entree) ** 2
    son[-sortie:] *= np.linspace(1, 0, sortie) ** 2
    return son.astype(np.float32)
