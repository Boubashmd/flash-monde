"""Cartes : le pays concerne en surbrillance, sur un globe zoome autour de lui.

Frontieres : Natural Earth (domaine public), simplifiees dans assets/carte/pays.json.
Projection orthographique : on voit la Terre comme depuis l'espace, centree sur le pays.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

DONNEES = Path(__file__).resolve().parent.parent / "assets" / "carte" / "pays.json"

UE = ["AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA", "DEU", "GRC", "HUN", "IRL",
      "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE"]

OCEAN_HAUT, OCEAN_BAS = (9, 18, 36), (14, 32, 58)
TERRE, FRONTIERE = (34, 47, 68), (62, 80, 108)
ESPACE = (5, 8, 16)


@lru_cache(maxsize=1)
def _pays() -> list[dict]:
    return json.loads(DONNEES.read_text(encoding="utf-8"))


def _par_iso() -> dict[str, dict]:
    d = {}
    for p in _pays():
        d.setdefault(p["iso"], p)
        d.setdefault(p["adm"], p)
    return d


def connu(iso: str) -> bool:
    return iso in _par_iso() or iso in ("UE", "MONDE")


def nom(iso: str) -> str:
    p = _par_iso().get(iso)
    return p["nom"] if p else iso


def _anneaux(p: dict):
    if p["type"] == "Polygon":
        yield from p["c"]
    else:
        for poly in p["c"]:
            yield from poly


def _cadrage(cibles: list[str]) -> tuple[float, float, float]:
    """(longitude, latitude, demi-angle de vue en degres), centre sur le pays principal (le premier)."""
    idx = _par_iso()
    pts = [idx[c] for c in cibles if c in idx]
    if not pts:
        return 15.0, 25.0, 90.0  # le monde
    p0 = pts[0]
    lon, lat = p0["cx"], p0["cy"]
    etendue = p0["ext"]
    # les autres pays cites elargissent le cadre s'ils sont proches (ex. Ukraine + Russie)
    for p in pts[1:]:
        dlon = ((p["cx"] - lon + 180) % 360) - 180
        dist = math.hypot(dlon * math.cos(math.radians(lat)), p["cy"] - lat)
        if dist < 30:
            etendue = max(etendue, dist * 0.6)
    # on montre la region autour : au moins 8 degres (petits pays), au plus 70
    return lon, lat, max(8.0, min(70.0, etendue * 2.6 + 5))


def rendre_monde(cibles: list[str], sortie: Path, L: int, H: int, accent=(255, 212, 0)) -> Path:
    """Globe entier tourne vers les pays du jour, tous en surbrillance (introduction et conclusion)."""
    idx = _par_iso()
    pts = [idx[c] for c in cibles if c in idx]
    lon = 15.0
    if pts:  # moyenne circulaire des longitudes
        sx = sum(math.cos(math.radians(p["cx"])) for p in pts)
        sy = sum(math.sin(math.radians(p["cx"])) for p in pts)
        lon = math.degrees(math.atan2(sy, sx))
    return rendre(cibles, sortie, L, H, accent, vue=(lon, 25.0, 90.0), secondaires=False)


def _projeter(lon, lat, lon0, lat0, R, cx, cy):
    l, p = math.radians(lon - lon0), math.radians(lat)
    p0 = math.radians(lat0)
    cosc = math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(l)
    x = math.cos(p) * math.sin(l)
    y = math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l)
    if cosc < 0:  # face cachee : on ramene le point sur l'horizon
        n = math.hypot(x, y) or 1
        x, y = x / n, y / n
    return cx + R * x, cy - R * y, cosc


def rendre(cibles: list[str], sortie: Path, L: int, H: int, accent=(255, 212, 0), centre_y: float = 0.5,
           vue: tuple | None = None, secondaires: bool = True) -> Path:
    """Dessine la carte et l'enregistre en JPEG. cibles : codes ISO3, 'UE' ou 'MONDE'."""
    # le premier code est le pays principal ; "UE" se developpe en ses 27 membres
    premier = next((c for c in cibles if c != "MONDE"), None)
    monde = "MONDE" in cibles
    liste = []
    for c in cibles:
        if c == "UE":
            liste += UE
        elif c != "MONDE":
            liste.append(c)
    if vue is None and premier == "UE":
        vue = (12.0, 50.0, 24.0)  # cadrage Europe
    lon0, lat0, angle = vue or _cadrage([] if monde else [c for c in [premier] + liste if c and c != "UE"])
    principal = set(UE) if premier == "UE" else ({premier} if premier else set())
    if not secondaires:
        principal = set(liste)
    cibles = liste
    attenue = tuple(int(a * 0.55 + t * 0.45) for a, t in zip(accent, TERRE))
    k = 2  # dessin en double resolution, puis reduction : bords lisses
    W, Ht = L * k, H * k
    cx, cy = W / 2, Ht * centre_y
    R = (W * 0.5) / math.sin(math.radians(min(angle, 89.0)))

    img = Image.new("RGB", (W, Ht), ESPACE)
    d = ImageDraw.Draw(img)
    # ocean : disque du globe (deborde de l'ecran quand on zoome)
    disque = Image.new("L", (W, Ht), 0)
    ImageDraw.Draw(disque).ellipse((cx - R, cy - R, cx + R, cy + R), fill=255)
    degrade = Image.linear_gradient("L").resize((W, Ht))
    ocean = Image.composite(Image.new("RGB", (W, Ht), OCEAN_BAS), Image.new("RGB", (W, Ht), OCEAN_HAUT), degrade)
    img.paste(ocean, (0, 0), disque)

    # meridiens et paralleles discrets
    trame = Image.new("RGBA", (W, Ht), (0, 0, 0, 0))
    dt = ImageDraw.Draw(trame)
    for lat in range(-75, 90, 15):
        pts = [_projeter(lo, lat, lon0, lat0, R, cx, cy) for lo in range(-180, 181, 3)]
        seg = []
        for x, y, c in pts + [(0, 0, -1)]:
            if c >= 0:
                seg.append((x, y))
            else:
                if len(seg) > 1:
                    dt.line(seg, fill=(255, 255, 255, 18), width=k)
                seg = []
    for lo in range(-180, 180, 15):
        pts = [_projeter(lo, la, lon0, lat0, R, cx, cy) for la in range(-90, 91, 3)]
        seg = []
        for x, y, c in pts + [(0, 0, -1)]:
            if c >= 0:
                seg.append((x, y))
            else:
                if len(seg) > 1:
                    dt.line(seg, fill=(255, 255, 255, 18), width=k)
                seg = []
    img.paste(trame, (0, 0), trame)

    cset = set(cibles)
    surbrillance = Image.new("L", (W, Ht), 0)
    ds = ImageDraw.Draw(surbrillance)
    for p in _pays():
        cible = p["iso"] in cset or p["adm"] in cset
        for anneau in _anneaux(p):
            proj = [_projeter(lo, la, lon0, lat0, R, cx, cy) for lo, la in anneau]
            if len(proj) < 3 or sum(c >= 0 for _, _, c in proj) < len(proj) * 0.9:
                continue
            poly = [(x, y) for x, y, _ in proj]
            fond = TERRE
            if cible:
                fond = accent if (p["iso"] in principal or p["adm"] in principal) else attenue
            d.polygon(poly, fill=fond, outline=FRONTIERE)
            if cible and fond == accent:
                ds.polygon(poly, fill=255)

    # halo lumineux autour des pays cibles
    if cset:
        halo = surbrillance.filter(ImageFilter.GaussianBlur(18 * k))
        halo = halo.point(lambda v: min(255, int(v * 1.6)))
        couleur = Image.new("RGB", (W, Ht), accent)
        img = Image.composite(couleur, img, halo.point(lambda v: int(v * 0.55)))
        d = ImageDraw.Draw(img)
        # on redessine les cibles nettes par-dessus le halo
        for p in _pays():
            if p["iso"] in principal or p["adm"] in principal:
                for anneau in _anneaux(p):
                    proj = [_projeter(lo, la, lon0, lat0, R, cx, cy) for lo, la in anneau]
                    if len(proj) < 3 or sum(c >= 0 for _, _, c in proj) < len(proj) * 0.9:
                        continue
                    d.polygon([(x, y) for x, y, _ in proj], fill=accent, outline=(255, 255, 255))

    img = img.resize((L, H), Image.LANCZOS)
    img.save(sortie, quality=92)
    return sortie
