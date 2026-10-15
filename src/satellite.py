"""Carte satellite facon "journal geopolitique" : vue de la Terre zoomee sur le pays,
pays rempli par son drapeau avec un contour blanc, etiquette a son nom, portrait en encart.

Sources libres :
- image satellite : NASA Blue Marble (domaine public), assets/carte/terre.jpg
- frontieres : Natural Earth (domaine public), assets/carte/pays.json
- drapeaux : flag-icons (licence MIT), assets/drapeaux/
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import carte

ASSETS = Path(__file__).resolve().parent.parent / "assets"
POLICE = ASSETS / "fonts" / "Poppins-Bold.ttf"


@lru_cache(maxsize=1)
def _texture() -> np.ndarray:
    return np.asarray(Image.open(ASSETS / "carte" / "terre.jpg").convert("RGB"), dtype=np.float32)


def _fond(lon0, lat0, angle, L, H, cy) -> Image.Image:
    """Projection orthographique de la texture (echantillonnage bilineaire)."""
    tex = _texture()
    th, tw, _ = tex.shape
    R = (L * 0.5) / math.sin(math.radians(min(angle, 89.0)))
    xs = (np.arange(L) - L / 2) / R
    ys = (cy - np.arange(H)) / R
    x, y = np.meshgrid(xs, ys)
    rho = np.sqrt(x * x + y * y)
    dans = rho < 1
    c = np.arcsin(np.clip(rho, 0, 1))
    p0, l0 = math.radians(lat0), math.radians(lon0)
    with np.errstate(invalid="ignore", divide="ignore"):
        lat = np.arcsin(np.clip(np.cos(c) * math.sin(p0) + np.where(rho > 0, y * np.sin(c) * math.cos(p0) / rho, 0), -1, 1))
        lon = l0 + np.arctan2(x * np.sin(c), rho * np.cos(c) * math.cos(p0) - y * np.sin(c) * math.sin(p0))
    u = ((np.degrees(lon) + 180) % 360) / 360 * tw - 0.5
    v = (90 - np.degrees(lat)) / 180 * th - 0.5
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    fu, fv = (u - u0)[..., None], (v - v0)[..., None]
    u0m, u1m = u0 % tw, (u0 + 1) % tw
    v0c, v1c = np.clip(v0, 0, th - 1), np.clip(v0 + 1, 0, th - 1)
    img = (tex[v0c, u0m] * (1 - fu) * (1 - fv) + tex[v0c, u1m] * fu * (1 - fv)
           + tex[v1c, u0m] * (1 - fu) * fv + tex[v1c, u1m] * fu * fv)
    espace = np.array([5, 8, 16], dtype=np.float32)
    img = np.where(dans[..., None], img, espace)
    out = Image.fromarray(img.clip(0, 255).astype(np.uint8))
    return out.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))


def _drapeau(iso2: str | None) -> Image.Image | None:
    if not iso2:
        return None
    f = ASSETS / "drapeaux" / f"{iso2.lower()}.png"
    return Image.open(f).convert("RGB") if f.exists() else None


def _masque(codes: set[str], lon0, lat0, R, cx, cy, L, H, k=2) -> Image.Image:
    m = Image.new("L", (L * k, H * k), 0)
    d = ImageDraw.Draw(m)
    for p in carte._pays():
        if p["iso"] in codes or p["adm"] in codes:
            for anneau in carte._anneaux(p):
                proj = [carte._projeter(lo, la, lon0, lat0, R * k, cx * k, cy * k) for lo, la in anneau]
                if len(proj) < 3 or sum(c >= 0 for _, _, c in proj) < len(proj) * 0.9:
                    continue
                d.polygon([(x, y) for x, y, _ in proj], fill=255)
    return m.resize((L, H), Image.LANCZOS)


def rendre(cibles: list[str], sortie: Path, L: int, H: int, centre_y: float = 0.55,
           portrait: Path | None = None, vue: tuple | None = None) -> Path:
    idx = carte._par_iso()
    premier = cibles[0] if cibles else None
    ue = premier == "UE"
    codes_principal = set(carte.UE) if ue else ({premier} if premier in idx else set())
    secondaires = {c for c in cibles[1:] if c in idx and c not in codes_principal}
    if vue is None:
        if ue:
            vue = (10.0, 50.0, 17.0)
        elif codes_principal:
            p = idx[premier]
            # le pays occupe l'essentiel de la largeur, comme sur les chaines geopolitiques
            vue = (p["cx"], p["cy"], max(2.6, min(55.0, p["ext"] * 1.7 + 1.0)))
        else:
            vue = (15.0, 25.0, 90.0)
    lon0, lat0, angle = vue
    cy = H * centre_y
    img = _fond(lon0, lat0, angle, L, H, cy)
    R = (L * 0.5) / math.sin(math.radians(min(angle, 89.0)))

    # pays secondaires : simple contour blanc
    if secondaires:
        ms = _masque(secondaires, lon0, lat0, R, L / 2, cy, L, H)
        contour = ms.filter(ImageFilter.MaxFilter(5))
        bord = Image.eval(Image.fromarray(np.asarray(contour, np.int16).__sub__(np.asarray(ms, np.int16)).clip(0, 255).astype(np.uint8)), lambda v: v)
        img.paste((255, 255, 255), (0, 0), bord)

    if codes_principal:
        m = _masque(codes_principal, lon0, lat0, R, L / 2, cy, L, H)
        boite = m.getbbox()
        if boite:
            # ombre portee
            ombre = m.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.7))
            img.paste((0, 0, 0), (10, 14), ombre)
            # drapeau etire sur la forme du pays
            dr = _drapeau("eu" if ue else idx[premier].get("iso2"))
            if dr:
                bw, bh = boite[2] - boite[0], boite[3] - boite[1]
                calque = Image.new("RGB", (L, H))
                calque.paste(dr.resize((max(1, bw), max(1, bh)), Image.LANCZOS), boite[:2])
                img.paste(calque, (0, 0), m)
            else:
                img.paste((255, 212, 0), (0, 0), m)
            # contour blanc
            epais = m.filter(ImageFilter.MaxFilter(7))
            bord = Image.fromarray((np.asarray(epais, np.int16) - np.asarray(m, np.int16)).clip(0, 255).astype(np.uint8))
            img.paste((255, 255, 255), (0, 0), bord)
            # etiquette
            nom = "Union européenne" if ue else _nom_court(premier)
            p = idx.get(premier) if not ue else None
            if p:
                ex, ey, _ = carte._projeter(p["cx"], p["cy"], lon0, lat0, R, L / 2, cy)
            else:
                ex, ey = (boite[0] + boite[2]) / 2, (boite[1] + boite[3]) / 2
            _etiquette(img, nom, ex, ey)

    if portrait:
        _encart(img, portrait, L, H)
    img.save(sortie, quality=92)
    return sortie


def _nom_court(iso: str) -> str:
    from .pays import nom_iso
    return nom_iso(iso)


def _etiquette(img: Image.Image, texte: str, x: float, y: float) -> None:
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(str(POLICE), 50)
    w = f.getlength(texte)
    x = min(max(x, w / 2 + 30), img.width - w / 2 - 30)
    x0, y0 = x - w / 2 - 26, y - 92
    d.rounded_rectangle((x0, y0, x0 + w + 52, y0 + 76), 8, fill=(0, 0, 0))
    d.polygon([(x - 14, y0 + 75), (x + 14, y0 + 75), (x, y0 + 94)], fill=(0, 0, 0))
    d.text((x, y0 + 38), texte, font=f, fill="white", anchor="mm")


def _encart(img: Image.Image, photo: Path, L: int, H: int) -> None:
    """Photo encadree de blanc, sous le titre."""
    w, h = int(L * 0.62), int(L * 0.62 * 0.62)
    ph = ImageOps.fit(Image.open(photo).convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.25))
    x, y = (L - w) // 2, 840
    ombre = Image.new("L", img.size, 0)
    ImageDraw.Draw(ombre).rectangle((x + 10, y + 14, x + w + 10, y + h + 14), fill=170)
    img.paste((0, 0, 0), (0, 0), ombre.filter(ImageFilter.GaussianBlur(12)))
    ImageDraw.Draw(img).rectangle((x - 6, y - 6, x + w + 5, y + h + 5), fill="white")
    img.paste(ph, (x, y))


def rendre_monde(cibles: list[str], sortie: Path, L: int, H: int) -> Path:
    """Globe entier pour l'introduction, pays du jour remplis par leur drapeau."""
    idx = carte._par_iso()
    pts = [idx[c] for c in cibles if c in idx]
    lon = 15.0
    if pts:
        lon = math.degrees(math.atan2(sum(math.sin(math.radians(p["cx"])) for p in pts),
                                      sum(math.cos(math.radians(p["cx"])) for p in pts)))
    vue = (lon, 25.0, 90.0)
    cy = H * 0.5
    img = _fond(*vue, L, H, cy)
    R = (L * 0.5) / math.sin(math.radians(89.0))
    for c in dict.fromkeys(cibles):
        codes = set(carte.UE) if c == "UE" else ({c} if c in idx else set())
        if not codes:
            continue
        m = _masque(codes, vue[0], vue[1], R, L / 2, cy, L, H)
        b = m.getbbox()
        dr = _drapeau("eu" if c == "UE" else idx[c].get("iso2"))
        if b and dr:
            calque = Image.new("RGB", (L, H))
            calque.paste(dr.resize((max(1, b[2] - b[0]), max(1, b[3] - b[1]))), b[:2])
            img.paste(calque, (0, 0), m)
    img.save(sortie, quality=92)
    return sortie
