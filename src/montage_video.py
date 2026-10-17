"""Montage vertical d'une video recue (amateur avec autorisation, ou video officielle).

Ecran 1080x1920, aux couleurs de Flash Monde :
  - la video au centre, sur un fond fait de la meme video, agrandie et floutee ;
  - en haut : date et titre ; sous la video : le credit (« Images : X · avec autorisation ») ;
  - la voix de Flash Monde lit le contexte au debut, le son d'origine est baisse pendant ce temps ;
  - sous-titres : la voix mot a mot (mot prononce en jaune), et la traduction des paroles d'origine ;
  - fin : 3 s sur la derniere image assombrie, avec le logo officiel.
Tout est fait par FFmpeg (filtres + sous-titres ASS), sans recalculer chaque image en Python.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from .film import MARGE_D, MARGE_G, _lignes, _logo, _ombre, police

L, H = 1080, 1920
FIN = 3.0                 # carte de fin
VOIX_DEBUT = 0.4          # la voix commence apres 0,4 s
DUREE_MAX = 58.0          # partie principale, avant la carte de fin
JAUNE = (255, 212, 0)


def infos_video(fichier: Path) -> dict:
    """Duree, taille et presence d'une piste son (lu avec ffmpeg seul)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(fichier)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    duree = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    t = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    w, h = (int(t.group(1)), int(t.group(2))) if t else (1280, 720)
    rot = re.search(r"rotation of (-?\d+)", err) or re.search(r"rotate\s*:\s*(-?\d+)", err)
    if rot and abs(int(rot.group(1))) in (90, 270):
        w, h = h, w
    return {"duree": duree, "w": w, "h": h, "son": bool(re.search(r"Stream #.*Audio:", err))}


def placement(w: int, h: int) -> tuple:
    """Taille et position de la video au premier plan."""
    larg = L
    haut = round(larg * h / w)
    if haut > 1240:                       # video verticale : on la reduit pour garder la place du titre
        haut = 1240
        larg = round(haut * w / h)
    larg, haut = larg - larg % 2, haut - haut % 2
    y = 600 if haut <= 1000 else 560
    return larg, haut, (L - larg) // 2, y


# ------------------------------------------------------------------ habillage fixe (PNG transparent)

def habillage(titre: str, date: str, credit: str, y_bas_video: int, sortie: Path) -> Path:
    img = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    # degrade sombre en haut, pour la lisibilite du titre
    voile = Image.new("L", (1, H))
    for y in range(H):
        voile.putpixel((0, y), int(200 * max(0.0, 1 - y / 640) ** 1.4))
    noir = Image.new("RGBA", (L, H), (6, 10, 22, 255))
    noir.putalpha(voile.resize((L, H)))
    img.alpha_composite(noir)
    d = ImageDraw.Draw(img)
    larg = L - MARGE_G - MARGE_D - 36
    for taille in (76, 70, 64, 58, 52):
        f = police("Bold", taille)
        lignes = _lignes(titre, f, larg)
        if len(lignes) <= 3:
            break
    d.text((MARGE_G, 168), date.upper(), font=police("Medium", 34), fill=(255, 255, 255, 230))
    yy, pas = 228, int(taille * 1.12)
    d.rectangle((MARGE_G, yy + 14, MARGE_G + 10, yy + len(lignes) * pas - 8), fill=(*JAUNE, 255))
    texte = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    dt = ImageDraw.Draw(texte)
    for ligne in lignes:
        dt.text((MARGE_G + 32, yy), ligne, font=f, fill=(255, 255, 255, 255))
        yy += pas
    img.alpha_composite(_ombre(texte, 8, 0.7, (0, 3)))
    # credit sous la video, dans une pastille sombre
    fc = police("Medium", 30)
    wc = int(fc.getlength(credit)) + 36
    yc = min(H - 520, y_bas_video + 18)
    pastille = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    dp = ImageDraw.Draw(pastille)
    dp.rounded_rectangle((MARGE_G, yc, MARGE_G + wc, yc + 52), 12, fill=(0, 0, 0, 170))
    dp.text((MARGE_G + 18, yc + 26), credit, font=fc, fill=(255, 255, 255, 235), anchor="lm")
    img.alpha_composite(pastille)
    img.save(sortie)
    return sortie


def logo_fin(sortie: Path) -> Path | None:
    logo = _logo(420)
    if logo is None:
        return None
    fond = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    fond.alpha_composite(_ombre(logo, 22, 0.75, (0, 12)), ((L - 420) // 2, int(H * 0.46 - 210)))
    fond.save(sortie)
    return sortie


# ------------------------------------------------------------------ sous-titres ASS

def _t(s: float) -> str:
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def _ass_texte(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def sous_titres(mots_voix: list, decalage: float, traduction: list, y_voix: int, y_trad: int,
                fin_principale: float, sortie: Path) -> Path:
    """mots_voix : [(mot, debut, fin)] ; traduction : [(debut, fin, texte francais)]."""
    entete = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {L}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Voix,Poppins,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,4,2,8,{MARGE_G},{MARGE_D},0,1
Style: Trad,Poppins,42,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,3,14,0,8,{MARGE_G},{MARGE_D},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lignes = []
    # voix : groupes de 3 a 5 mots, le mot prononce en jaune
    groupes, courant = [], []
    for m in mots_voix:
        courant.append(m)
        if len(courant) >= 4 or m[0][-1] in ".!?;:," and len(courant) >= 2:
            groupes.append(courant)
            courant = []
    if courant:
        groupes.append(courant)
    for g in groupes:
        for k, (mot, deb, fin) in enumerate(g):
            fin_aff = g[k + 1][1] if k + 1 < len(g) else fin + 0.15
            morceaux = []
            for j, (m2, _, _) in enumerate(g):
                m2 = _ass_texte(m2)
                morceaux.append("{\\c&H00D4FF&}" + m2 + "{\\c&HFFFFFF&}" if j == k else m2)
            lignes.append(f"Dialogue: 1,{_t(deb + decalage)},{_t(fin_aff + decalage)},Voix,,0,0,{y_voix},,"
                          + " ".join(morceaux))
    # traduction des paroles d'origine
    for deb, fin, texte in traduction:
        if deb >= fin_principale:
            continue
        lignes.append(f"Dialogue: 0,{_t(deb)},{_t(min(fin, fin_principale))},Trad,,0,0,{y_trad},,"
                      + _ass_texte(texte))
    sortie.write_text(entete + "\n".join(lignes) + "\n", encoding="utf-8")
    return sortie


# ------------------------------------------------------------------ assemblage FFmpeg

def monter(source: Path, voix_mp3: Path | None, duree_voix: float, mots_voix: list, traduction: list,
           titre: str, date: str, credit: str, travail: Path, sortie: Path, polices: Path) -> Path:
    info = infos_video(source)
    principale = min(DUREE_MAX, max(info["duree"], duree_voix + VOIX_DEBUT + 1.2))
    total = principale + FIN
    larg, haut, x, y = placement(info["w"], info["h"])
    y_trad = min(H - 470, y + haut + 90)       # traduction juste sous le credit
    y_voix = H - 380                          # voix en bas, au-dessus des boutons TikTok
    hab = habillage(titre, date, credit, y + haut, travail / "habillage.png")
    ass = sous_titres(mots_voix, VOIX_DEBUT, traduction, y_voix, y_trad, principale, travail / "sous_titres.ass")
    logo = logo_fin(travail / "logo_fin.png")

    entrees = ["-stream_loop", "-1", "-t", f"{principale:.3f}", "-i", str(source),
               "-loop", "1", "-t", f"{total:.3f}", "-i", str(hab)]
    n = 2
    i_voix = i_logo = None
    if voix_mp3:
        entrees += ["-i", str(voix_mp3)]
        i_voix, n = n, n + 1
    if logo:
        entrees += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(logo)]
        i_logo, n = n, n + 1

    ass_ok = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    fonts_ok = str(polices).replace("\\", "/").replace(":", "\\:")
    v = [
        "[0:v]fps=30,setsar=1,split=2[a][b]",
        f"[a]scale={L}:{H}:force_original_aspect_ratio=increase,crop={L}:{H},boxblur=28:3,"
        f"eq=brightness=-0.12:saturation=0.85[fond]",
        f"[b]scale={larg}:{haut}[avant]",
        f"[fond][avant]overlay={x}:{y}[c]",
        f"[c][1:v]overlay=0:0:enable='lt(t,{principale:.3f})'[d]",
        f"[d]subtitles='{ass_ok}':fontsdir='{fonts_ok}'[e]",
        f"[e]tpad=stop_mode=clone:stop_duration={FIN},"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=0x060A16@0.6:t=fill:enable='gte(t,{principale:.3f})'[f]",
    ]
    if i_logo is not None:
        v.append(f"[{i_logo}:v]format=rgba,fade=in:st={principale + 0.25:.2f}:d=0.6:alpha=1[lg]")
        v.append(f"[f][lg]overlay=0:0:enable='gte(t,{principale:.3f})',format=yuv420p[vout]")
    else:
        v.append("[f]format=yuv420p[vout]")

    a, mix = [], []
    if info["son"]:   # son d'origine : baisse pendant la voix, coupe en fondu avant la fin
        fin_voix = VOIX_DEBUT + duree_voix + 0.3 if voix_mp3 else 0
        a.append(f"[0:a]aresample=48000,volume='if(lt(t,{fin_voix:.2f}),0.22,1)':eval=frame,"
                 f"afade=t=out:st={max(0, principale - 0.6):.2f}:d=0.6,apad=whole_dur={total:.3f}[orig]")
        mix.append("[orig]")
    if i_voix is not None:
        ms = int(VOIX_DEBUT * 1000)
        a.append(f"[{i_voix}:a]aresample=48000,adelay={ms}|{ms},apad=whole_dur={total:.3f}[vx]")
        mix.append("[vx]")
    if not mix:
        a.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[aout]")
    else:
        a.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=longest,"
                 f"atrim=0:{total:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    cmd = ["ffmpeg", "-y", "-v", "error", *entrees, "-filter_complex", ";".join(v + a),
           "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "5500k", "-bufsize", "11000k",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", str(sortie)]
    subprocess.run(cmd, check=True)
    return sortie


def couverture(video: Path, sortie: Path, instant: float = 1.5) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{instant:.2f}", "-i", str(video), "-frames:v", "1",
                    "-q:v", "3", str(sortie)], check=True)
    return sortie
