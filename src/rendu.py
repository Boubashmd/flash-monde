"""Montage de la video verticale (1080x1920) avec Pillow et FFmpeg.

Chaque segment (intro, une info, conclusion) est compose de :
  - un fond (photo libre ou degrade) anime par un zoom lent ;
  - un calque fixe (bandeau, titre, sources) ;
  - des sous-titres qui se succedent au rythme de la voix.
Les segments sont ensuite assembles bout a bout.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

POLICES = Path(__file__).resolve().parent.parent / "assets" / "fonts"
MARGE_G, MARGE_D = 70, 130      # la droite est plus large : boutons des applis
NUIT = (11, 16, 32)


def police(style: str, taille: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(POLICES / f"Poppins-{style}.ttf"), taille)


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lignes(texte: str, f: ImageFont.FreeTypeFont, largeur: int) -> list[str]:
    lignes, courante = [], ""
    for mot in texte.split():
        essai = f"{courante} {mot}".strip()
        if f.getlength(essai) <= largeur or not courante:
            courante = essai
        else:
            lignes.append(courante)
            courante = mot
    return lignes + ([courante] if courante else [])


# ---------------------------------------------------------------- fonds

def fond(photo: Path | None, sortie: Path, L: int, H: int, variante: int = 0) -> Path:
    """Photo recadree plein ecran, ou degrade si aucune photo."""
    if photo:
        img = ImageOps.fit(Image.open(photo).convert("RGB"), (L, H), Image.LANCZOS)
    else:
        teintes = [(28, 52, 110), (16, 78, 96), (70, 38, 104), (96, 48, 40)]
        haut = teintes[variante % len(teintes)]
        masque = Image.linear_gradient("L").resize((L, H))
        img = Image.composite(Image.new("RGB", (L, H), NUIT), Image.new("RGB", (L, H), haut), masque)
        halo = Image.new("L", (L, H), 0)
        ImageDraw.Draw(halo).ellipse((L * 0.35, -H * 0.1, L * 1.25, H * 0.4), fill=70)
        halo = halo.filter(ImageFilter.GaussianBlur(160))
        img = Image.composite(Image.new("RGB", (L, H), (255, 255, 255)), img, halo)
    img.save(sortie, quality=92)
    return sortie


# ---------------------------------------------------------------- calques

def _voile(L: int, H: int, force: int = 120) -> Image.Image:
    """Assombrit le fond pour que le texte reste lisible sur n'importe quelle photo."""
    calque = Image.new("RGBA", (L, H), (*NUIT, force))
    bas = Image.linear_gradient("L").resize((L, H))                 # 0 en haut, 255 en bas
    haut = ImageOps.flip(bas)
    renfort = Image.eval(Image.blend(bas, haut, 0.35), lambda v: int(v * 0.45))
    calque.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (L, H), NUIT).split(), renfort)))
    return calque


def _bandeau(d: ImageDraw.ImageDraw, nom: str, sous: str, accent, y: int = 170) -> None:
    f = police("Bold", 40)
    w = f.getlength(nom.upper())
    d.rounded_rectangle((MARGE_G, y, MARGE_G + w + 48, y + 70), 14, fill=accent)
    d.text((MARGE_G + 24, y + 35), nom.upper(), font=f, fill=NUIT, anchor="lm")
    d.text((MARGE_G, y + 96), sous, font=police("Medium", 32), fill=(255, 255, 255, 215))


def _demo(d: ImageDraw.ImageDraw, L: int) -> None:
    f = police("Bold", 30)
    w = f.getlength("DÉMO")
    d.rounded_rectangle((L - MARGE_D - w - 36, 176, L - MARGE_D, 234), 10, outline=(255, 255, 255, 230), width=3)
    d.text((L - MARGE_D - 18 - w, 205), "DÉMO", font=f, fill=(255, 255, 255, 230), anchor="lm")


def calque_info(sortie, L, H, *, nom, sous, accent, rang, total, pays, titre, sources, credit, demo) -> Path:
    img = _voile(L, H)
    d = ImageDraw.Draw(img)
    _bandeau(d, nom, sous, accent)
    if demo:
        _demo(d, L)
    large = L - MARGE_G - MARGE_D
    d.text((MARGE_G, 470), f"{rang}/{total}  ·  {pays.upper()}", font=police("Bold", 36), fill=accent)
    for taille in (84, 72, 62):
        f = police("Bold", taille)
        lignes = _lignes(titre, f, large)
        if len(lignes) <= 4:
            break
    y = 540
    d.rectangle((MARGE_G, y + 14, MARGE_G + 10, y + len(lignes) * int(taille * 1.16) - 6), fill=accent)
    for ligne in lignes:
        d.text((MARGE_G + 36, y), ligne, font=f, fill="white")
        y += int(taille * 1.16)
    fs = police("Regular", 32)
    y = 1470
    for ligne in _lignes("Sources : " + ", ".join(sources), fs, large)[:2]:
        d.text((MARGE_G, y), ligne, font=fs, fill=(255, 255, 255, 220))
        y += 44
    if credit:
        d.text((MARGE_G, y + 6), credit, font=police("Regular", 26), fill=(255, 255, 255, 150))
    img.save(sortie)
    return sortie


def calque_intro(sortie, L, H, *, nom, edition, date, titres, accent, demo) -> Path:
    img = _voile(L, H, 150)
    d = ImageDraw.Draw(img)
    if demo:
        _demo(d, L)
    large = L - MARGE_G - MARGE_D
    d.text((MARGE_G, 300), nom.upper(), font=police("Bold", 118), fill="white")
    d.rectangle((MARGE_G, 462, MARGE_G + 180, 474), fill=accent)
    d.text((MARGE_G, 506), f"Édition {edition}", font=police("Bold", 50), fill=accent)
    d.text((MARGE_G, 574), date, font=police("Medium", 38), fill=(255, 255, 255, 220))
    f = police("Medium", 42)
    y = 730
    for i, t in enumerate(titres, 1):
        d.text((MARGE_G, y), str(i), font=police("Bold", 42), fill=accent)
        for ligne in _lignes(t, f, large - 60)[:2]:
            d.text((MARGE_G + 60, y), ligne, font=f, fill="white")
            y += 54
        y += 30
    img.save(sortie)
    return sortie


def calque_fin(sortie, L, H, *, nom, signature, accent, demo) -> Path:
    img = _voile(L, H, 170)
    d = ImageDraw.Draw(img)
    if demo:
        _demo(d, L)
    large = L - MARGE_G - MARGE_D
    d.text((MARGE_G, 620), nom.upper(), font=police("Bold", 104), fill="white")
    d.rectangle((MARGE_G, 764, MARGE_G + 180, 776), fill=accent)
    f = police("Medium", 52)
    y = 820
    for ligne in _lignes(signature, f, large):
        d.text((MARGE_G, y), ligne, font=f, fill=accent)
        y += 68
    img.save(sortie)
    return sortie


def calque_sous_titre(texte: str, sortie: Path, L: int, H: int, y_centre: int = 1250) -> Path:
    img = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = police("Medium", 56)
    large = L - MARGE_G - MARGE_D
    lignes = _lignes(texte, f, large - 60)
    pas = 74
    h = len(lignes) * pas + 44
    w = max(f.getlength(l) for l in lignes) + 64
    cx = MARGE_G + large / 2
    d.rounded_rectangle((cx - w / 2, y_centre - h / 2, cx + w / 2, y_centre + h / 2), 22, fill=(0, 0, 0, 185))
    y = y_centre - h / 2 + 22 + pas / 2
    for ligne in lignes:
        d.text((cx, y), ligne, font=f, fill="white", anchor="mm")
        y += pas
    img.save(sortie)
    return sortie


# ---------------------------------------------------------------- sous-titres

def decouper(texte: str, max_car: int = 44) -> list[str]:
    """Coupe le texte en petits blocs lisibles d'un coup d'oeil."""
    blocs, courant = [], ""
    for mot in texte.split():
        essai = f"{courant} {mot}".strip()
        if len(essai) > max_car and courant:
            blocs.append(courant)
            courant = mot
        else:
            courant = essai
        if courant[-1] in ".!?…" and len(courant) > 12:
            blocs.append(courant)
            courant = ""
    return blocs + ([courant] if courant else [])


def minuter(blocs: list[str], duree_voix: float) -> list[tuple[float, float]]:
    """Repartit la duree de la voix entre les blocs, selon leur longueur."""
    poids = [len(b) + (7 if b[-1] in ".!?…" else 3 if b[-1] in ",;:" else 0) for b in blocs]
    total = sum(poids) or 1
    t, plages = 0.0, []
    for p in poids:
        fin = t + duree_voix * p / total
        plages.append((round(t, 2), round(fin, 2)))
        t = fin
    return plages


# ---------------------------------------------------------------- ffmpeg

def segment(fond_jpg, fixe_png, sous_titres, audio, duree_voix, sortie, *, L, H, fps, zoom_avant=True) -> Path:
    """sous_titres : liste de (png, debut, fin)."""
    duree = duree_voix + 0.45
    n = int(duree * fps) + 1
    z = f"1+0.09*on/{n}" if zoom_avant else f"1.09-0.09*on/{n}"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(fond_jpg), "-i", str(fixe_png)]
    for png, _, _ in sous_titres:
        cmd += ["-i", str(png)]
    cmd += ["-i", str(audio)]
    filtres = [
        f"[0:v]scale={L * 2}:{H * 2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={n}:s={L}x{H}:fps={fps}[bg]",
        "[bg][1:v]overlay=0:0[v0]",
    ]
    for i, (_, t0, t1) in enumerate(sous_titres):
        filtres.append(f"[v{i}][{i + 2}:v]overlay=0:0:enable='between(t,{t0},{t1})'[v{i + 1}]")
    k = len(sous_titres)
    filtres.append(f"[v{k}]fade=t=in:st=0:d=0.25,format=yuv420p[v]")
    filtres.append(f"[{k + 2}:a]aresample=44100,apad[a]")
    cmd += [
        "-filter_complex", ";".join(filtres), "-map", "[v]", "-map", "[a]",
        "-t", f"{duree:.2f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k", str(sortie),
    ]
    subprocess.run(cmd, check=True)
    return sortie


def assembler(segments: list[Path], sortie: Path) -> Path:
    liste = sortie.parent / "segments.txt"
    liste.write_text("".join(f"file '{s.resolve().as_posix()}'\n" for s in segments), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(liste),
         "-c", "copy", "-movflags", "+faststart", str(sortie)],
        check=True,
    )
    liste.unlink()
    return sortie
