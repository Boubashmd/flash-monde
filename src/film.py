"""Montage anime de l'edition : une camera continue sur le globe, des textes animes,
la voix et un habillage sonore discret.

Deroulement :
  - ouverture : la camera plonge vers le pays de la premiere info, le titre apparait et
    la voix demarre aussitot (ni sommaire, ni nom d'edition) ;
  - pendant chaque info : pays rempli par son drapeau, etiquette, portrait eventuel,
    sous-titres mot a mot ; la camera avance tres lentement ;
  - entre deux infos : une pause, puis la camera recule, fait tourner le globe et plonge
    vers le pays suivant (trajectoire "zoom et panoramique" de van Wijk et Nuij) ;
  - fin : recul sur le globe entier et nom de la chaine, sans voix.

Chaque image est calculee a partir de l'instant t : aucun collage d'images fixes, donc
aucune coupure. Les images sont fabriquees en parallele et envoyees directement a FFmpeg.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from multiprocessing.pool import ThreadPool
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .globe import Camera, Globe, lonlat, vecteur

cv2.setNumThreads(1)  # le parallelisme se fait par images entieres

POLICES = Path(__file__).resolve().parent.parent / "assets" / "fonts"
MARGE_G, MARGE_D = 70, 130      # a droite : boutons de TikTok et YouTube
BLANC = (255, 255, 255)
LOGO = Path(__file__).resolve().parent.parent / "assets" / "logo" / "C_globe_titre.png"


# ------------------------------------------------------------------ donnees d'entree

@dataclass
class Plan:
    """Une info de l'edition, prete a etre montee."""
    titre: str
    texte: str
    date: str                       # "15 octobre 2026", affichee mais jamais lue
    sources: list
    credit: str
    cible: str | None               # code ISO3, "UE", ou None (vue du monde)
    secondaires: list = field(default_factory=list)
    iso2: str | None = None         # drapeau
    nom_pays: str = ""              # etiquette sur la carte
    photo: Path | None = None       # portrait libre de droits
    audio: Path | None = None
    duree: float = 0.0              # duree du fichier audio
    mots: list = field(default_factory=list)  # [(mot, debut, fin)] en secondes
    chiffre: dict | None = None     # {"nombre": "80", "legende": "drones abattus"}
    lien: dict | None = None        # {"de": "RUS", "vers": "UKR", "type": "action" | "echange"}


# ------------------------------------------------------------------ courbes d'animation

def rampe(t: float, a: float, b: float) -> float:
    if b <= a:
        return 1.0 if t >= b else 0.0
    return min(1.0, max(0.0, (t - a) / (b - a)))


def lisse(q: float) -> float:          # demarre et s'arrete en douceur
    return q * q * q * (q * (q * 6 - 15) + 10)


def sortie(q: float) -> float:         # rapide puis freine
    return 1 - (1 - q) ** 3


def rebond(q: float) -> float:         # depasse un peu puis se pose
    c1 = 1.4
    c3 = c1 + 1
    return 1 + c3 * (q - 1) ** 3 + c1 * (q - 1) ** 2


# ------------------------------------------------------------------ trajectoire de camera

def _slerp(a: np.ndarray, b: np.ndarray, u: float, d: float) -> np.ndarray:
    if d < 1e-6:
        return a
    return (math.sin((1 - u) * d) * a + math.sin(u * d) * b) / math.sin(d)


class Trajet:
    """Mouvement de camera de a vers b : recul, rotation du globe, puis zoom.

    La forme suit la trajectoire optimale de van Wijk et Nuij (2003), avec un recul
    minimum pour que chaque changement de pays se lise comme un vrai deplacement, puis
    elle est reparametree pour que la vitesse percue a l'ecran soit reguliere.
    """

    def __init__(self, a: Camera, b: Camera, mode: str = "inout", duree: float | None = None,
                 rho: float = 1.6, recul: float = 2.3):
        self.a, self.b, self.mode = a, b, mode
        self.ca, self.cb = vecteur(a.lon, a.lat), vecteur(b.lon, b.lat)
        d = math.acos(max(-1.0, min(1.0, float(np.dot(self.ca, self.cb)))))
        self.d = d
        W0, W1 = 2 * a.w, 2 * b.w
        s = np.linspace(0.0, 1.0, 801)
        if d < 0.004:  # meme endroit : simple respiration
            u = s.copy()
            W = np.exp(np.log(W0) * (1 - s) + np.log(W1) * s)
            pic_voulu = 1.45 * max(W0, W1) if max(W0, W1) < 1.6 else 0.0
        else:
            r2, r4 = rho * rho, rho ** 4
            b0 = (W1 * W1 - W0 * W0 + r4 * d * d) / (2 * W0 * r2 * d)
            b1 = (W1 * W1 - W0 * W0 - r4 * d * d) / (2 * W1 * r2 * d)
            r0 = math.log(math.sqrt(b0 * b0 + 1) - b0)
            r1 = math.log(math.sqrt(b1 * b1 + 1) - b1)
            S = (r1 - r0) / rho
            ss = s * S
            u = W0 / (r2 * d) * (math.cosh(r0) * np.tanh(rho * ss + r0) - math.sinh(r0))
            W = W0 * math.cosh(r0) / np.cosh(rho * ss + r0)
            u = (u - u[0]) / (u[-1] - u[0])
            # recul minimum entre deux pays proches (sinon simple glissement de carte)
            pic_voulu = recul * max(W0, W1) if max(W0, W1) < 0.9 else 0.0
        if mode == "inout" and W.max() < pic_voulu:
            W = W * (1 + (pic_voulu / W.max() - 1) * np.sin(np.pi * s))
        Wc, Wm = 1.5, 2.3  # on ne recule jamais au point de voir le globe minuscule
        W = np.where(W <= Wc, W, Wc + (Wm - Wc) * np.tanh((W - Wc) / (Wm - Wc)))
        dl = np.diff(np.log(W))
        du = np.diff(u)
        Wmil = (W[1:] + W[:-1]) / 2
        dp = np.sqrt((d * du / Wmil) ** 2 + 0.5 * dl ** 2) + 1e-7
        self.P = np.concatenate([[0.0], np.cumsum(dp)])
        self.s, self.u, self.W = s, u, W
        self.duree = duree if duree else min(3.4, max(2.0, 1.6 + 0.75 * float(self.P[-1])))

    def _avance(self, q: float) -> float:
        return lisse(q) if self.mode == "inout" else sortie(q)

    def __call__(self, q: float) -> Camera:
        q = min(1.0, max(0.0, q))
        p = self._avance(q) * self.P[-1]
        si = float(np.interp(p, self.P, self.s))
        u = float(np.interp(si, self.s, self.u))
        W = float(np.interp(si, self.s, self.W))
        lon, lat = lonlat(_slerp(self.ca, self.cb, u, self.d))
        if self.d < 1e-6:
            lon, lat = self.a.lon, self.a.lat
        fy = self.a.fy + (self.b.fy - self.a.fy) * lisse(q)
        return Camera(lon, lat, W / 2, fy)

    def intensite(self, q: float) -> float:
        """Vitesse relative du mouvement (0 a 1), pour caler le souffle sonore."""
        q = min(1.0, max(0.0, q))
        if self.mode == "inout":
            return 16 * q * q * (1 - q) * (1 - q)
        return (1 - q) ** 2


# ------------------------------------------------------------------ calques (sprites)

def police(style: str, taille: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(POLICES / f"Poppins-{style}.ttf"), taille)


class Sprite:
    """Image RGBA en alpha premultiplie, placee au sous-pixel pres (pas de tremblement)."""

    def __init__(self, img: Image.Image):
        arr = np.asarray(img.convert("RGBA"), np.float32)
        a = arr[..., 3:4] / 255.0
        self.pm = np.ascontiguousarray(np.concatenate([arr[..., :3] * a, a], axis=2))
        self.h, self.w = arr.shape[:2]


def coller(img: np.ndarray, sp: Sprite, x: float, y: float, alpha: float = 1.0,
           echelle: float = 1.0, ancre=(0.0, 0.0)) -> None:
    if alpha <= 0.004 or sp is None or echelle <= 0.01:
        return
    H, L = img.shape[:2]
    sw, sh = sp.w * echelle, sp.h * echelle
    gauche, haut = x - ancre[0] * sw, y - ancre[1] * sh
    ix, iy = math.floor(gauche), math.floor(haut)
    fx, fy = gauche - ix, haut - iy
    if abs(echelle - 1) < 1e-4 and fx < 0.02 and fy < 0.02:
        src = sp.pm
    else:
        bw, bh = int(math.ceil(sw + fx)) + 1, int(math.ceil(sh + fy)) + 1
        M = np.float32([[echelle, 0, fx + 0.5 * echelle - 0.5], [0, echelle, fy + 0.5 * echelle - 0.5]])
        src = cv2.warpAffine(sp.pm, M, (bw, bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                             borderValue=(0, 0, 0, 0))
    bh, bw = src.shape[:2]
    x0, y0, x1, y1 = max(ix, 0), max(iy, 0), min(ix + bw, L), min(iy + bh, H)
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - iy:y1 - iy, x0 - ix:x1 - ix]
    zone = img[y0:y1, x0:x1].astype(np.float32)
    zone *= 1 - s[..., 3:4] * alpha
    zone += s[..., :3] * alpha
    np.clip(zone, 0, 255, out=zone)
    img[y0:y1, x0:x1] = zone.astype(np.uint8)


def _ombre(img: Image.Image, rayon: int, force: float, decalage=(0, 4)) -> Image.Image:
    """Ajoute une ombre douce noire sous le contenu d'une image RGBA."""
    a = img.getchannel("A").filter(ImageFilter.GaussianBlur(rayon)).point(lambda v: int(v * force))
    ombre = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ombre.putalpha(a)
    fond = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fond.alpha_composite(ombre, decalage)
    fond.alpha_composite(img)
    return fond


def _logo(taille: int, rayon: int | None = None) -> Image.Image | None:
    """Logo officiel en carre arrondi, avec un fin contour clair ; None si le fichier manque."""
    try:
        img = Image.open(LOGO).convert("RGB").resize((taille, taille), Image.LANCZOS)
    except Exception:
        return None
    rayon = rayon if rayon is not None else int(taille * 0.16)
    masque = Image.new("L", (taille * 4, taille * 4), 0)
    ImageDraw.Draw(masque).rounded_rectangle((0, 0, taille * 4 - 1, taille * 4 - 1), rayon * 4, fill=255)
    masque = masque.resize((taille, taille), Image.LANCZOS)   # bords lisses
    out = Image.new("RGBA", (taille, taille), (0, 0, 0, 0))
    out.paste(img, (0, 0), masque)
    bord = max(2, taille // 160)
    ImageDraw.Draw(out).rounded_rectangle((0, 0, taille - 1, taille - 1), rayon, outline=(255, 255, 255, 70),
                                          width=bord)
    return out


def _texte(texte: str, f: ImageFont.FreeTypeFont, couleur=BLANC, ombre: float = 0.6,
           contour: int = 0, espacement: int = 0) -> Sprite:
    marge = 30
    w = int(f.getlength(texte) + espacement * len(texte)) + 2 * marge
    asc, desc = f.getmetrics()
    h = asc + desc + 2 * marge
    img = Image.new("RGBA", (w, h), (*couleur[:3], 0))
    d = ImageDraw.Draw(img)
    if espacement:
        x = marge
        for ch in texte:
            d.text((x, marge), ch, font=f, fill=couleur, stroke_width=contour, stroke_fill=(0, 0, 0))
            x += f.getlength(ch) + espacement
    else:
        d.text((marge, marge), texte, font=f, fill=couleur, stroke_width=contour, stroke_fill=(0, 0, 0))
    if ombre:
        img = _ombre(img, 8, ombre, (0, 3))
    return Sprite(img)


def _lignes(texte: str, f: ImageFont.FreeTypeFont, largeur: float) -> list:
    lignes, courante = [], ""
    for mot in texte.split():
        essai = f"{courante} {mot}".strip()
        if f.getlength(essai) <= largeur or not courante:
            courante = essai
        else:
            lignes.append(courante)
            courante = mot
    return lignes + ([courante] if courante else [])


def _etiquette(nom: str) -> Sprite:
    f = police("Bold", 46)
    tw = f.getlength(nom)
    pad_x, haut_boite, pointe = 28, 80, 20
    m = 24
    w, h = int(tw + 2 * pad_x) + 2 * m, haut_boite + pointe + 2 * m
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((m, m, w - m, m + haut_boite), 12, fill=(10, 10, 12, 255))
    cx = w / 2
    d.polygon([(cx - 17, m + haut_boite - 1), (cx + 17, m + haut_boite - 1), (cx, m + haut_boite + pointe)],
              fill=(10, 10, 12, 255))
    d.text((cx, m + haut_boite / 2 + 1), nom, font=f, fill=BLANC, anchor="mm")
    return Sprite(_ombre(img, 10, 0.55, (0, 5)))


def _carte_portrait(photo: Path, w: int = 560, h: int = 400) -> Sprite | None:
    try:
        ph = ImageOps.fit(Image.open(photo).convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.28))
    except Exception:
        return None
    b, m = 8, 34
    img = Image.new("RGBA", (w + 2 * b + 2 * m, h + 2 * b + 2 * m), (255, 255, 255, 0))
    ImageDraw.Draw(img).rectangle((m, m, m + w + 2 * b - 1, m + h + 2 * b - 1), fill=(255, 255, 255, 255))
    img.paste(ph, (m + b, m + b))
    # lisere jaune Flash Monde en bas de la photo, le meme sur tous les portraits
    ImageDraw.Draw(img).rectangle((m + b, m + b + h - 7, m + b + w - 1, m + b + h - 1), fill=(255, 212, 0, 255))
    return Sprite(_ombre(img, 16, 0.6, (0, 10)))


# ------------------------------------------------------------------ montage

class Montage:
    def __init__(self, plans: list, globe: Globe, accent=(255, 212, 0), demo: bool = False,
                 marque: str = "", fin_marque: bool = True, fps: int = 30):
        self.plans, self.globe, self.accent, self.fps = plans, globe, tuple(accent), fps
        self.L, self.H = globe.L, globe.H
        self.demo, self.marque, self.fin_marque = demo, marque, fin_marque
        self._cache_st: dict = {}
        self._preparer_textes()
        self._calculer_temps()
        self._vignette = self._creer_vignette()
        self._vignette_inv = 1 - self._vignette
        self._noir = np.zeros((self.H, self.L, 3), np.uint8)
        self.globe_demi = globe.reduit(2)
        self._groupes_dates = self._grouper_dates()

    # ---- cadrages
    def cadrage(self, p: Plan) -> Camera:
        fy = 0.68 if p.photo else 0.56
        geo = self.globe.geo
        if not p.cible or (p.cible != "UE" and p.cible not in geo.index):
            return Camera(12.0, 18.0, 1.05, 0.5)
        if p.cible == "UE":
            return Camera(11.0, 50.5, math.sin(math.radians(17.0)) * (1.08 if p.photo else 1.0), fy)
        pays = geo.index[p.cible]
        e = pays.ext
        a = min(58.0, max(5.0, 3.5 + 1.9 * e / (1 + e / 40)))
        w = math.sin(math.radians(a)) * (1.1 if p.photo else 1.0)
        cam = Camera(pays.cx, pays.cy, w, fy)
        # un pays voisin cite dans l'info doit rester dans le cadre
        for code in p.secondaires:
            q = geo.index.get(code)
            if not q:
                continue
            v = vecteur(q.cx, q.cy)
            if float(np.dot(v, vecteur(pays.cx, pays.cy))) < math.cos(math.radians(30)):
                continue
            x, y, z = self.globe.projeter(v[None, :], Camera(pays.cx, pays.cy, 0.5, fy))
            R = self.globe.rayon(Camera(0, 0, 0.5, fy))
            dx, dy = (x[0] - self.L / 2) / R, (self.H * fy - y[0]) / R
            besoin = abs(dx) / 0.8
            if dy > 0:
                besoin = max(besoin, dy / (fy * self.H * 2 / self.L * 0.8))
            else:
                besoin = max(besoin, -dy / ((1 - fy) * self.H * 2 / self.L * 0.75))
            cam.w = max(cam.w, min(besoin + 0.04, math.sin(math.radians(45))))
        if p.lien:
            cam = self._cadrer_lien(p, cam)
        return cam

    def centre(self, code: str):
        """(lon, lat, etendue) du centre d'un pays, ou de l'Union europeenne."""
        if code == "UE":
            return 10.0, 50.0, 12.0
        q = self.globe.geo.index.get(code)
        return (q.cx, q.cy, q.ext) if q else None

    def points_lien(self, p: Plan):
        """Depart et arrivee de la fleche. Pour un grand pays, on part de sa frontiere tournee
        vers l'autre (pas de son centre, qui peut etre tres loin)."""
        cle = ("lien", id(p))
        if cle in self._cache_st:
            return self._cache_st[cle]
        a, b = self.centre(p.lien["de"]), self.centre(p.lien["vers"])
        res = None
        if a and b:
            pa, pb = (a[0], a[1], a[2]), (b[0], b[1], b[2])
            if a[2] > 6 and p.lien["de"] != "UE":
                q = self.globe.point_vers(p.lien["de"], (b[0], b[1]))
                pa = (q[0], q[1], 3.0) if q else pa
            if b[2] > 12 and p.lien["vers"] != "UE":
                q = self.globe.point_vers(p.lien["vers"], (pa[0], pa[1]), 0.45)
                pb = (q[0], q[1], 4.0) if q else pb
            res = (pa, pb)
        self._cache_st[cle] = res
        return res

    def _cadrer_lien(self, p: Plan, cam: Camera) -> Camera:
        """Les deux pays relies par la fleche doivent tenir dans le cadre."""
        pts = self.points_lien(p)
        if not pts:
            p.lien = None
            return cam
        a, b = pts
        va, vb = vecteur(a[0], a[1]), vecteur(b[0], b[1])
        d = math.acos(max(-1.0, min(1.0, float(np.dot(va, vb)))))
        if d > math.radians(140):  # aux antipodes : pas de fleche lisible
            p.lien = None
            return cam
        lon, lat = lonlat(va + vb) if d > 1e-6 else (a[0], a[1])
        if d > math.radians(40):  # loin : vue centree a mi-latitude, pour ne pas basculer vers le pole
            lat = max(-40.0, min(45.0, (a[1] + b[1]) / 2))
        vue = Camera(lon, lat, 1.0, cam.fy)
        R = self.globe.rayon(vue)
        besoin = cam.w
        for (lo, la, ext), v in ((a, va), (b, vb)):
            x, y, z = self.globe.projeter(v[None, :], vue)
            dx, dy = (x[0] - self.L / 2) / R, (self.H * cam.fy - y[0]) / R
            marge = 0.9 * math.sin(math.radians(min(ext, 15))) + 0.04
            besoin = max(besoin, (abs(dx) + marge) / 0.78)
            if dy > 0:
                besoin = max(besoin, (dy + marge + 0.05) / (cam.fy * self.H * 2 / self.L * 0.7))
            else:
                besoin = max(besoin, (-dy + marge) / ((1 - cam.fy) * self.H * 2 / self.L * 0.7))
        return Camera(lon, lat, min(1.08, besoin), cam.fy)

    # ---- chronologie
    def _calculer_temps(self):
        n = len(self.plans)
        self.cibles = [self.cadrage(p) for p in self.plans]
        self.segments = []  # (debut, fin, fonction t -> Camera)
        self.souffles = []  # (debut, trajet)
        c0 = self.cibles[0]
        depart_cam = Camera(c0.lon - 32.0, max(-60.0, min(60.0, c0.lat - 12.0)),
                            min(1.05, max(0.62, 3.2 * c0.w)), 0.5)
        ouverture = Trajet(depart_cam, c0, mode="out", duree=2.5)
        self.segments.append((0.0, ouverture.duree, self._suivre(ouverture, 0.0)))
        self.souffles.append((0.0, ouverture))
        self.arrivee, self.depart, self.voix, self.fin_parole = [], [], [], []
        self.duree_entree, self.duree_sortie = [], []
        t_arrivee = ouverture.duree
        t_voix = 0.55
        self.duree_entree.append(ouverture.duree)
        for i, p in enumerate(self.plans):
            parole = (p.mots[-1][2] if p.mots else p.duree) if p.duree else 2.0
            fin_parole = t_voix + parole
            depart = max(fin_parole + 0.7, t_arrivee + 1.2)
            self.arrivee.append(t_arrivee)
            self.voix.append(t_voix)
            self.fin_parole.append(fin_parole)
            self.depart.append(depart)
            tenue = self._tenue(self.cibles[i], t_arrivee, depart, monde=not p.cible)
            self.segments.append((t_arrivee, depart, tenue))
            fin_tenue = tenue(depart)
            if i + 1 < n:
                tr = Trajet(fin_tenue, self.cibles[i + 1])
            else:
                fin = Camera(self.cibles[i].lon, 16.0, 1.08, 0.47)
                tr = Trajet(fin_tenue, fin, duree=2.3)
            self.segments.append((depart, depart + tr.duree, self._suivre(tr, depart)))
            self.souffles.append((depart, tr))
            self.duree_sortie.append(tr.duree)
            t_arrivee = depart + tr.duree
            if i + 1 < n:
                self.duree_entree.append(tr.duree)
                t_voix = t_arrivee - 0.15
        # fin : le globe tourne encore un peu, nom de la chaine
        fin_cam = self.segments[-1][2](t_arrivee)
        self.debut_fin = self.depart[-1]
        reste = 1.1 if self.fin_marque else 0.4
        self.segments.append((t_arrivee, t_arrivee + reste, self._tenue(fin_cam, t_arrivee, t_arrivee + reste, monde=True)))
        self.total = t_arrivee + reste
        self._debuts = [s[0] for s in self.segments]

    @staticmethod
    def _suivre(tr: Trajet, t0: float):
        return lambda t: tr((t - t0) / tr.duree)

    @staticmethod
    def _tenue(cam: Camera, t0: float, t1: float, monde: bool = False):
        """Pendant la lecture : la camera avance tres lentement (le globe tourne s'il est entier)."""
        def f(t):
            q = lisse(rampe(t, t0, t1))
            if monde:  # le globe tourne doucement sur lui-meme
                return Camera(cam.lon + 1.2 * (t1 - t0) * q, cam.lat, cam.w, cam.fy)
            dlon = math.degrees(0.045 * cam.w * q) / max(0.35, math.cos(math.radians(cam.lat)))
            return Camera(cam.lon + dlon, cam.lat, cam.w * (1 - 0.07 * q), cam.fy)
        return f

    def camera(self, t: float) -> Camera:
        i = max(0, bisect_right(self._debuts, t) - 1)
        return self.segments[i][2](min(t, self.segments[i][1]))

    # ---- textes
    def _preparer_textes(self):
        L = self.L
        larg = L - MARGE_G - MARGE_D - 36
        self.titres, self.dates, self.sources, self.credits = [], [], [], []
        self.etiquettes, self.portraits, self.titre_bas = [], [], []
        f_date = police("Medium", 34)
        for p in self.plans:
            for taille in (82, 74, 66, 60):
                f = police("Bold", taille)
                lignes = _lignes(p.titre, f, larg)
                if len(lignes) <= 3 or taille == 60:
                    break
            pas = int(taille * 1.13)
            self.titres.append(([_texte(l, f, ombre=0.75) for l in lignes], pas))
            self.titre_bas.append(262 + len(lignes) * pas)
            self.dates.append(_texte(p.date.upper(), f_date, (255, 255, 255), ombre=0.7, espacement=3))
            fs = police("Regular", 30)
            src = _lignes("Sources : " + ", ".join(p.sources), fs, L - MARGE_G - MARGE_D)[:2]
            self.sources.append([_texte(l, fs, (235, 235, 235), ombre=0.8) for l in src])
            fc = police("Regular", 23)
            cr = _lignes(p.credit, fc, L - MARGE_G - MARGE_D)[:2] if p.credit else []
            self.credits.append([_texte(l, fc, (205, 205, 205), ombre=0.8) for l in cr])
            self.etiquettes.append(_etiquette(p.nom_pays) if p.cible and p.nom_pays else None)
            self.portraits.append(_carte_portrait(p.photo) if p.photo else None)
        self.marque_sp = _texte(self.marque.upper(), police("Bold", 92), ombre=0.8) if self.marque else None
        logo = _logo(420) if self.marque else None
        if logo:  # carte de fin : le logo officiel de la chaine
            marge = 40
            fond = Image.new("RGBA", (logo.width + 2 * marge, logo.height + 2 * marge), (0, 0, 0, 0))
            fond.alpha_composite(logo, (marge, marge))
            self.logo_sp = Sprite(_ombre(fond, 22, 0.75, (0, 12)))
        else:
            self.logo_sp = None
        self.demo_sp = _texte("DÉMO", police("Bold", 30), ombre=0.6) if self.demo else None
        self.f_st = police("Bold", 64)

    def _creer_vignette(self) -> np.ndarray:
        H, L = self.H, self.L
        y = np.arange(H, dtype=np.float32)
        haut = 0.62 * np.clip(1 - y / 780.0, 0, 1) ** 1.6
        bas = 0.70 * np.clip((y - 1180.0) / (H - 1180.0), 0, 1) ** 1.5
        f = 1 - np.maximum(haut, bas)
        return np.ascontiguousarray(np.repeat(f[:, None], L, axis=1))

    # ---- sous-titres mot a mot
    def _blocs(self, i: int) -> list:
        """Decoupe les mots de l'info en petits blocs d'une ligne, avec leurs horaires."""
        cle = ("blocs", i)
        if cle in self._cache_st:
            return self._cache_st[cle]
        p = self.plans[i]
        mots = p.mots or []
        larg = self.L - MARGE_G - MARGE_D - 20
        blocs, courant = [], []
        for k, (m, _, _) in enumerate(mots):
            essai = " ".join(mots[j][0] for j in courant + [k])
            if courant and (self.f_st.getlength(essai) > larg or len(courant) >= 5):
                blocs.append(courant)
                courant = []
            courant.append(k)
            if m[-1] in ".!?" or (m[-1] in ",;:" and len(courant) >= 2):
                blocs.append(courant)
                courant = []
        if courant:
            blocs.append(courant)
        t0 = self.voix[i]
        res = []
        for b, idx in enumerate(blocs):
            debut = t0 + mots[idx[0]][1] - 0.06
            fin = t0 + mots[blocs[b + 1][0]][1] - 0.06 if b + 1 < len(blocs) else t0 + mots[idx[-1]][2] + 0.45
            res.append((debut, fin, idx))
        self._cache_st[cle] = res
        return res

    def _sprite_st(self, i: int, idx: tuple, actif: int) -> Sprite:
        cle = ("st", i, idx, actif)
        sp = self._cache_st.get(cle)
        if sp is None:
            mots = [self.plans[i].mots[k][0] for k in idx]
            f = self.f_st
            esp = f.getlength(" ")
            largeurs = [f.getlength(m) for m in mots]
            m = 30
            w = int(sum(largeurs) + esp * (len(mots) - 1)) + 2 * m
            asc, desc = f.getmetrics()
            h = asc + desc + 2 * m
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            x = m
            for k, (mot, lw) in zip(idx, zip(mots, largeurs)):
                couleur = self.accent if k == actif else BLANC
                d.text((x, m), mot, font=f, fill=couleur, stroke_width=7, stroke_fill=(0, 0, 0))
                x += lw + esp
            sp = Sprite(_ombre(img, 10, 0.5, (0, 5)))
            if len(self._cache_st) > 40:  # chaque calque pese ~2 Mo
                self._cache_st = {k: v for k, v in self._cache_st.items() if k[0] in ("blocs", "chiffre", "lien")}
            self._cache_st[cle] = sp
        return sp

    # ---- une image
    def _carte(self, t: float, cam: Camera, demi: bool = False):
        """Globe, frontieres et pays mis en avant a l'instant t."""
        g = self.globe_demi if demi else self.globe
        img = g.fond(cam)
        g.dessiner_frontieres(img, cam, 0.30)
        boites = {}
        for i, p in enumerate(self.plans):
            if not p.cible:
                continue
            v = self._visibilite(i, t)
            if v <= 0:
                continue
            for code in p.secondaires:
                g.dessiner_contour(img, cam, code, v * 0.9)
            boites[i] = g.dessiner_pays(img, cam, p.cible, v, p.iso2 or ("eu" if p.cible == "UE" else None), relief=v)
        for i, p in enumerate(self.plans):  # fleche d'un pays vers l'autre
            if not p.lien:
                continue
            v = self._visibilite(i, t)
            if v <= 0:
                continue
            t_arc = (1.3 if i == 0 else self.arrivee[i]) + 0.3
            progres = lisse(rampe(t, t_arc, t_arc + 1.5))
            fin = t_arc + 1.5
            onde = ((t - fin) % 1.8) / 1.8 if t > fin else None
            a, b = self.points_lien(p)
            g.dessiner_arc(img, cam, a[:2], b[:2], progres, v, fleche=p.lien["type"] == "action",
                           couleur=self.accent, pulsation=onde)
        return img, boites

    def _nb_poses(self, t: float, cam: Camera) -> int:
        """Flou de mouvement : plus la camera va vite, plus on fusionne de poses par image."""
        suiv = self.camera(t + 1.0 / self.fps)
        ang = math.acos(max(-1.0, min(1.0, float(np.dot(vecteur(cam.lon, cam.lat), vecteur(suiv.lon, suiv.lat))))))
        depl = ang * self.L / (2 * cam.w) + abs(math.log(suiv.w / cam.w)) * 1000 + abs(suiv.fy - cam.fy) * self.H
        return int(min(5, max(1, math.ceil(0.5 * depl / 6.0))))  # obturateur a 180 degres

    def image(self, t: float) -> np.ndarray:
        cam = self.camera(t)
        poses = self._nb_poses(t, cam)
        if poses <= 1:
            img, boites = self._carte(t, cam)
        else:
            # mouvement rapide : les poses sont calculees en demi-resolution (le flou efface la difference)
            demi = poses >= 3
            somme = None
            for k in range(poses):
                tk = t + (k / (poses - 1) - 0.5) * 0.5 / self.fps
                im, b = self._carte(tk, self.camera(tk), demi)
                somme = im.astype(np.float32) if somme is None else somme + im
                if k == poses // 2:
                    boites = b
            img = (somme * (1.0 / poses) + 0.5).astype(np.uint8)
            if demi:
                img = cv2.resize(img, (self.L, self.H), interpolation=cv2.INTER_LINEAR)
                boites = {i: (tuple(v * 2 for v in b) if b else None) for i, b in boites.items()}
        img[:] = cv2.blendLinear(img, self._noir, self._vignette, self._vignette_inv)
        n = len(self.plans)
        for i in range(n):
            self._etiquette(img, cam, i, t, boites.get(i))
        for i in range(n):
            self._portrait(img, i, t)
            self._titre(img, i, t)
            self._sous_titres(img, i, t)
        for i in range(n):
            self._chiffre(img, i, t)
        self._date(img, t)
        self._fin(img, t)
        if self.demo_sp:
            coller(img, self.demo_sp, self.L - MARGE_D + 20, 150, 0.9, ancre=(1.0, 0.0))
        return img

    def _visibilite(self, i: int, t: float) -> float:
        if i == 0:
            entree = rampe(t, 0.55, 1.6)
        else:
            D = self.duree_entree[i]
            entree = rampe(t, self.arrivee[i] - 0.45 * D, self.arrivee[i] - 0.05 * D)
        sortie_ = rampe(t, self.depart[i], self.depart[i] + 0.35 * self.duree_sortie[i])
        return lisse(entree) * (1 - lisse(sortie_))

    def _etiquette(self, img, cam, i, t, boite=None):
        sp = self.etiquettes[i]
        if sp is None:
            return
        t_in = 1.45 if i == 0 else self.arrivee[i] - 0.35
        q_in = rampe(t, t_in, t_in + 0.5)
        q_out = rampe(t, self.depart[i], self.depart[i] + 0.25)
        a = sortie(q_in) * (1 - q_out)
        infos = self._infos_chiffre(i) if not self.portraits[i] else None
        if infos:  # le chiffre s'affiche au meme endroit : l'etiquette s'efface le temps qu'il passe
            debut, fin = infos[4], infos[5]
            a *= 1 - sortie(rampe(t, debut - 0.1, debut + 0.25)) * (1 - lisse(rampe(t, fin - 0.35, fin)))
        if a <= 0:
            return
        pos = self.globe.ancre(cam, self.plans[i].cible)
        if not pos:
            return
        x, y = pos
        if boite:  # au-dessus du pays, pour ne pas cacher son drapeau
            plancher = self.titre_bas[i] + (568 if self.portraits[i] else 120)
            haut = max(boite[1] - 4, plancher)
            if haut < y + 40:
                y = haut
        x = min(max(x, sp.w / 2 - 10), self.L - sp.w / 2 + 10)
        e = 0.6 + 0.4 * rebond(q_in) if q_in < 1 else 1.0
        coller(img, sp, x, y + 22, a, e, ancre=(0.5, 1.0))

    def _portrait(self, img, i, t):
        sp = self.portraits[i]
        if sp is None:
            return
        t_in = 0.95 if i == 0 else self.arrivee[i] - 0.2
        q_in = rampe(t, t_in, t_in + 0.55)
        q_out = rampe(t, self.depart[i] - 0.05, self.depart[i] + 0.3)
        a = sortie(q_in) * (1 - lisse(q_out))
        if a <= 0:
            return
        e = (0.86 + 0.14 * rebond(q_in) if q_in < 1 else 1.0) * (1 - 0.06 * lisse(q_out))
        y = self.titre_bas[i] + 18
        coller(img, sp, self.L / 2, y, a, e, ancre=(0.5, 0.0))

    def _titre(self, img, i, t):
        t_in, t_out = self._t_titre(i)
        if t < t_in or t > t_out + 0.45:
            return
        q_out = lisse(rampe(t, t_out, t_out + 0.38))
        lignes, pas = self.titres[i]
        x = MARGE_G + 34
        y = 262
        # barre d'accent qui grandit avec le titre
        q_barre = sortie(rampe(t, t_in, t_in + 0.5))
        h_barre = (len(lignes) * pas - 22) * q_barre
        if h_barre > 1 and q_out < 1:
            a = 1 - q_out
            y0, y1 = int(y + 14), int(y + 14 + h_barre)
            zone = img[y0:y1, MARGE_G:MARGE_G + 10]
            zone[:] = (zone * (1 - a) + np.array(self.accent) * a).astype(np.uint8)
        for k, sp in enumerate(lignes):
            q = sortie(rampe(t, t_in + 0.07 * k, t_in + 0.07 * k + 0.5))
            a = q * (1 - q_out)
            dy = (1 - q) * 38 - q_out * 16
            coller(img, sp, x - 30, y + k * pas - 30 + dy, a)
        # sources et credit, en bas
        q_src = sortie(rampe(t, t_in + 0.25, t_in + 0.65)) * (1 - q_out)
        y_s = 1598
        for sp in self.sources[i]:
            coller(img, sp, MARGE_G - 30, y_s - 30, q_src)
            y_s += 40
        for sp in self.credits[i]:
            coller(img, sp, MARGE_G - 30, y_s - 26, q_src * 0.9)
            y_s += 32

    # ---- chiffre marquant, compte anime au moment ou la voix le prononce
    def _infos_chiffre(self, i: int):
        cle = ("chiffre", i)
        if cle in self._cache_st:
            return self._cache_st[cle]
        p = self.plans[i]
        res = None
        m = re.match(r"^(\d{1,3}(?: \d{3})+|\d+)(?:,(\d+))?(.*)$", p.chiffre["nombre"]) if p.chiffre else None
        if m:
            entier = int(m.group(1).replace(" ", ""))
            dec = m.group(2) or ""
            valeur = entier + (int(dec) / 10 ** len(dec) if dec else 0)
            debut = self.voix[i] + 1.0
            mots = [x[0] for x in p.mots]
            texte = " ".join(mots)
            k = texte.find(p.chiffre["nombre"])
            if k >= 0 and p.mots:  # le mot ou commence le nombre
                indice = texte[:k].count(" ")
                debut = self.voix[i] + p.mots[min(indice, len(p.mots) - 1)][1] - 0.12
            fin = min(debut + 3.4, self.depart[i])
            res = (valeur, len(dec), " " in m.group(1) or entier >= 10000, m.group(3), debut, fin)
        self._cache_st[cle] = res
        return res

    @staticmethod
    def _formater(v: float, dec: int, milliers: bool, reste: str) -> str:
        if dec:
            txt = f"{v:.{dec}f}".replace(".", ",")
        else:
            txt = f"{int(round(v)):,}".replace(",", " ") if milliers else str(int(round(v)))
        return txt + reste

    def _sprite_chiffre(self, i: int, nombre: str) -> Sprite:
        cle = ("sprite_chiffre", i, nombre)
        sp = self._cache_st.get(cle)
        if sp is None:
            c = self.plans[i].chiffre
            f_l = police("Bold", 46)
            taille = 150
            while taille > 90 and police("Bold", taille).getlength(c["nombre"]) > 760:
                taille -= 10  # « 6,5 milliards » : on reduit pour tenir dans l'ecran
            f_n = police("Bold", taille)
            legende = c["legende"].upper()
            larg = max(f_n.getlength(c["nombre"]), f_n.getlength(nombre), f_l.getlength(legende))
            px, m = 48, 30
            w, h = int(larg + 2 * px), 300
            img = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((m, m, m + w, m + h), 28, fill=(10, 12, 18, 215))
            d.rounded_rectangle((m, m, m + w, m + 10), 5, fill=(*self.accent, 255))
            d.text((m + w / 2, m + 120), nombre, font=f_n, fill=(*self.accent, 255), anchor="mm")
            d.text((m + w / 2, m + 240), legende, font=f_l, fill=BLANC, anchor="mm")
            sp = Sprite(_ombre(img, 14, 0.6, (0, 8)))
            if len(self._cache_st) > 40:  # chaque calque pese quelques Mo
                self._cache_st = {k: v for k, v in self._cache_st.items() if k[0] in ("blocs", "chiffre", "lien")}
            self._cache_st[cle] = sp
        return sp

    def _chiffre(self, img, i, t):
        infos = self._infos_chiffre(i)
        if not infos:
            return
        valeur, dec, milliers, reste, debut, fin = infos
        if t < debut or t > fin:
            return
        q_in = rampe(t, debut, debut + 0.45)
        q_out = rampe(t, fin - 0.35, fin)
        a = sortie(q_in) * (1 - lisse(q_out))
        compte = sortie(rampe(t, debut + 0.05, debut + 1.1))
        sp = self._sprite_chiffre(i, self._formater(valeur * compte, dec, milliers, reste))
        e = (0.8 + 0.2 * rebond(q_in) if q_in < 1 else 1.0) * (1 - 0.05 * lisse(q_out))
        if self.portraits[i]:  # a cheval sur le bas du portrait
            coller(img, sp, self.L / 2, self.titre_bas[i] + 468, a, e, ancre=(0.5, 0.5))
        else:
            coller(img, sp, self.L / 2, self.titre_bas[i] + 40, a, e, ancre=(0.5, 0.0))

    def _t_titre(self, i: int) -> tuple:
        # le premier titre est deja presque entier sur la toute premiere image (vignette, accroche)
        return (-0.45 if i == 0 else self.arrivee[i] - 0.55), self.depart[i]

    def _grouper_dates(self) -> list:
        """Infos consecutives de meme date : la date reste affichee sans clignoter."""
        groupes = []
        for i, p in enumerate(self.plans):
            if groupes and self.plans[groupes[-1][-1]].date == p.date:
                groupes[-1].append(i)
            else:
                groupes.append([i])
        return groupes

    def _date(self, img, t):
        for g in self._groupes_dates:
            t_in, t_out = self._t_titre(g[0])[0], self._t_titre(g[-1])[1]
            a = sortie(rampe(t, t_in, t_in + 0.4)) * (1 - lisse(rampe(t, t_out, t_out + 0.38)))
            if a > 0:
                coller(img, self.dates[g[0]], MARGE_G - 30, 196 - 30, a)

    def _sous_titres(self, img, i, t):
        if t < self.voix[i] - 0.2 or t > self.fin_parole[i] + 1.0 or not self.plans[i].mots:
            return
        for debut, fin, idx in self._blocs(i):
            if debut <= t < fin:
                tv = t - self.voix[i]
                mots = self.plans[i].mots
                actif = idx[0]
                for k in idx:
                    if mots[k][1] - 0.03 <= tv:
                        actif = k
                sp = self._sprite_st(i, tuple(idx), actif)
                q = sortie(rampe(t, debut, debut + 0.13))
                dernier = idx[-1] == len(mots) - 1
                a = q * (1 - (lisse(rampe(t, fin - 0.25, fin)) if dernier else 0))
                e = 0.92 + 0.08 * q
                coller(img, sp, (MARGE_G + self.L - MARGE_D) / 2, 1468, a, e, ancre=(0.5, 0.5))
                return

    def _fin(self, img, t):
        if not self.marque_sp:
            return
        t0 = self.debut_fin + 1.0
        q = sortie(rampe(t, t0, t0 + 0.7))
        if q <= 0:
            return
        if self.logo_sp:  # logo officiel, qui apparait en grandissant legerement
            e = 0.9 + 0.1 * (rebond(rampe(t, t0, t0 + 0.9)) if t < t0 + 0.9 else 1.0)
            coller(img, self.logo_sp, self.L / 2, self.H * 0.48 + (1 - q) * 30, q, e, ancre=(0.5, 0.5))
            return
        coller(img, self.marque_sp, self.L / 2, 300 + (1 - q) * 30, q, ancre=(0.5, 0.0))
        w = 200 * q
        y = int(300 + self.marque_sp.h - 22)
        x0 = int(self.L / 2 - w / 2)
        if w > 2:
            img[y:y + 10, x0:x0 + int(w)] = self.accent


# ------------------------------------------------------------------ fabrication de la video

_MONTAGE: Montage | None = None
_FPS = 30


def _rendre(k: int) -> bytes:
    return _MONTAGE.image(k / _FPS).tobytes()


def rendre_images(montage: Montage, sortie_mp4: Path, fps: int, travailleurs: int | None = None) -> Path:
    global _MONTAGE, _FPS
    _MONTAGE, _FPS = montage, fps
    n = int(math.ceil(montage.total * fps))
    L, H = montage.L, montage.H
    # Telegram refuse les videos de plus de 50 Mo : debit plafonne selon la duree
    debit = int(min(8000, max(2500, 44 * 8 * 1024 / max(10.0, montage.total) - 200)))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{L}x{H}",
           "-r", str(fps), "-i", "-", "-an",
           "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
           "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-maxrate", f"{debit}k",
           "-bufsize", f"{debit * 2}k", "-g", str(fps * 2),
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-movflags", "+faststart", str(sortie_mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    nb = travailleurs or max(1, (os.cpu_count() or 2))
    debut = time.time()
    fork = sys.platform.startswith("linux") and "fork" in mp.get_all_start_methods()
    pool = mp.get_context("fork").Pool(nb) if fork and nb > 1 else ThreadPool(nb)
    try:
        for k, image in enumerate(pool.imap(_rendre, range(n), chunksize=4)):
            proc.stdin.write(image)
            if k and k % (fps * 10) == 0:
                ecoule = time.time() - debut
                print(f"    {k}/{n} images ({ecoule:.0f} s, reste ~{ecoule / k * (n - k):.0f} s)")
    finally:
        pool.close()
        pool.join()
        proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("FFmpeg n'a pas pu encoder la vidéo")
    print(f"    {n} images en {time.time() - debut:.0f} s")
    return sortie_mp4


# ------------------------------------------------------------------ son

SR = 48000


def _decoder(audio: Path) -> np.ndarray:
    brut = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-f", "f32le", "-ac", "1",
                           "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(brut, np.float32).copy()


def _souffle(tr: Trajet, gain: float, graine: int) -> np.ndarray:
    """Souffle d'air synthetise, dont le volume et la hauteur suivent la vitesse de la camera."""
    n = int(tr.duree * SR)
    rng = np.random.default_rng(graine)
    taille, pas = 2048, 512
    nb = n // pas + 4
    fenetre = np.hanning(taille).astype(np.float32)
    out = np.zeros(nb * pas + taille, np.float32)
    freqs = np.fft.rfftfreq(taille, 1 / SR)
    for k in range(nb):
        q = (k * pas + taille / 2) / max(1, n)
        v = tr.intensite(q) ** 1.3
        if v < 0.003:
            continue
        centre = 260 + 2300 * v      # le souffle monte avec la vitesse
        largeur = 0.55
        spectre = np.exp(-0.5 * (np.log(np.maximum(freqs, 20) / centre) / largeur) ** 2)
        spectre = spectre * (rng.standard_normal(len(freqs)) + 1j * rng.standard_normal(len(freqs)))
        trame = np.fft.irfft(spectre, taille).astype(np.float32) * fenetre * v
        out[k * pas:k * pas + taille] += trame
    out = out[:n]
    crete = np.max(np.abs(out)) or 1.0
    return out / crete * gain


def mixer(montage: Montage, sortie_brut: Path, souffles: bool = True, musique_on: bool = True,
          volume_musique: float = 1.0, graine: int = 0) -> None:
    n = int(math.ceil(montage.total * SR)) + SR
    piste = np.zeros(n, np.float32)
    energie = []
    for i, p in enumerate(montage.plans):
        if not p.audio or not Path(p.audio).exists():
            continue
        pcm = _decoder(p.audio)
        a = int(montage.voix[i] * SR)
        piste[a:a + len(pcm)] += pcm[: max(0, n - a)]
        actif = pcm[np.abs(pcm) > 0.01]
        if len(actif):
            energie.append(float(np.sqrt(np.mean(actif ** 2))))
    if souffles:
        for k, (t0, tr) in enumerate(montage.souffles):
            s = _souffle(tr, 0.10 if k == 0 else 0.13, 11 + k)
            a = int(t0 * SR)
            piste[a:a + len(s)] += s[: max(0, n - a)]
    if musique_on:
        from . import musique
        voix_rms = float(np.median(energie)) if energie else 0.08
        fond = musique.piste(montage.total, SR, graine)
        # la musique s'efface sous la voix et remonte pendant les deplacements de camera
        parole = np.zeros(len(fond), np.float32)
        for i, p in enumerate(montage.plans):
            a, b = int(montage.voix[i] * SR), int(montage.fin_parole[i] * SR)
            parole[max(0, a):max(0, min(len(fond), b))] = 1.0
        lissage = int(0.4 * SR)
        noyau = np.hanning(2 * lissage + 1).astype(np.float32)
        parole = np.convolve(parole, noyau / noyau.sum(), mode="same")
        gain = (voix_rms / 0.2) * volume_musique * (0.22 - 0.12 * parole)
        piste[:len(fond)] += fond * gain
    piste[: int(montage.total * SR)].astype(np.float32).tofile(sortie_brut)


def couverture(montage: Montage, sortie: Path, marque: str = "") -> Path:
    """Image d'apercu verticale : la carte de la premiere info et son titre en grand."""
    t = (montage.arrivee[0] + montage.depart[0]) / 2
    cam = montage.camera(t)
    img, boites = montage._carte(t, cam)
    y = np.arange(montage.H, dtype=np.float32)[:, None, None]
    voile = np.clip(1 - y / 1100.0, 0, 1) ** 1.3 * 0.78 + np.clip((y - 1500) / 420.0, 0, 1) * 0.5
    img = (img * (1 - voile)).astype(np.uint8)
    pil = Image.fromarray(img).convert("RGBA")  # pas d'etiquette : elle chevauchait le titre
    d = ImageDraw.Draw(pil)
    larg = montage.L - MARGE_G - MARGE_D - 36
    p = montage.plans[0]
    for taille in (112, 100, 90, 82):
        f = police("Bold", taille)
        lignes = _lignes(p.titre, f, larg)
        if len(lignes) <= 4:
            break
    logo = _logo(150) if marque else None
    if logo:  # petit logo dans le coin : la chaine se reconnait dans les listes
        pil.alpha_composite(_ombre(logo, 10, 0.6, (0, 5)), (MARGE_G, 70))
    elif marque:
        fm = police("Bold", 40)
        w = fm.getlength(marque.upper())
        d.rounded_rectangle((MARGE_G, 150, MARGE_G + w + 44, 214), 12, fill=(*montage.accent, 255))
        d.text((MARGE_G + 22, 182), marque.upper(), font=fm, fill=(11, 16, 32), anchor="lm")
    d.text((MARGE_G, 246), p.date.upper(), font=police("Medium", 36), fill=(255, 255, 255, 230))
    yy = 320
    pas = int(taille * 1.1)
    d.rectangle((MARGE_G, yy + 18, MARGE_G + 12, yy + len(lignes) * pas - 10), fill=(*montage.accent, 255))
    for ligne in lignes:
        d.text((MARGE_G + 36, yy), ligne, font=f, fill="white", stroke_width=2, stroke_fill=(0, 0, 0))
        yy += pas
    pil.convert("RGB").save(sortie, quality=88)
    return sortie


def finaliser(video: Path, brut: Path, sortie_mp4: Path, normaliser: bool = True) -> Path:
    """Assemble image et son ; le son est normalise a -14 LUFS (niveau des reseaux sociaux)."""
    entree = ["-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(brut)]
    chaine = "highpass=f=60,acompressor=threshold=-18dB:ratio=2.5:attack=8:release=160:makeup=1"
    mesure = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *entree, "-af",
                             chaine + ",loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                            capture_output=True, text=True).stderr
    filtre = chaine
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", mesure, re.S)
    if m and normaliser:
        try:
            d = json.loads(m.group(0))
            if float(d["input_i"]) > -60:
                filtre += (f",loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={d['input_i']}:measured_TP={d['input_tp']}"
                           f":measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}"
                           f":offset={d['target_offset']}:linear=true")
        except (ValueError, KeyError):
            pass
    filtre += f",aresample={SR}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), *entree, "-map", "0:v", "-map", "1:a",
                    "-af", filtre, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                    "-shortest", "-movflags", "+faststart", str(sortie_mp4)], check=True)
    return sortie_mp4


def fabriquer(plans: list, sortie_mp4: Path, cfg_video: dict, travail: Path, *, demo: bool = False,
              marque: str = "", voix_reelle: bool = True, graine: int = 0) -> tuple:
    """Fabrique la video et son image de couverture. Renvoie (video, couverture)."""
    L, H, fps = cfg_video["largeur"], cfg_video["hauteur"], cfg_video["fps"]
    accent = tuple(int(cfg_video.get("couleur_accent", "#FFD400").lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    t = time.time()
    globe = Globe(L, H, int(cfg_video.get("texture_max", 21600)), hd=cfg_video.get("image_hd", True))
    print(f"  globe prêt ({'image HD' if globe.tex.hd else 'image standard'}, {time.time() - t:.0f} s)")
    montage = Montage(plans, globe, accent, demo=demo, marque=marque,
                      fin_marque=cfg_video.get("fin_marque", True), fps=fps)
    print(f"  durée : {montage.total:.1f} s")
    muet = travail / "images.mp4"
    rendre_images(montage, muet, fps, cfg_video.get("processus"))
    brut = travail / "son.f32"
    mixer(montage, brut, cfg_video.get("son_transitions", True), cfg_video.get("musique", True),
          float(cfg_video.get("volume_musique", 1.0)), graine)
    finaliser(muet, brut, sortie_mp4, normaliser=voix_reelle)
    for f in (muet, brut):
        f.unlink(missing_ok=True)
    image = couverture(montage, sortie_mp4.with_name("couverture.jpg"), marque or "Flash Monde")
    return sortie_mp4, image
