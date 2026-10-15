"""Globe satellite anime : tout est recalcule a chaque image a partir de la camera.

La camera est definie par le point vise (longitude, latitude), le champ de vision w
(demi-largeur de l'ecran en rayons terrestres : 1 = le globe entier tient en largeur)
et la hauteur fy (fraction de la hauteur de l'ecran) a laquelle le point vise s'affiche.
Comme rien n'est precalcule en image fixe, la camera peut reculer, faire tourner le globe
et zoomer sans aucune coupure, au pixel pres (pas de saccade).

Sources libres :
- image satellite : NASA Blue Marble Next Generation (domaine public). La version HD
  (21600 x 10800) est telechargee une fois dans cache/terre ; sinon assets/carte/terre.jpg.
- frontieres : Natural Earth (domaine public), assets/carte/pays.json
- drapeaux : flag-icons (licence MIT), assets/drapeaux/
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

RACINE = Path(__file__).resolve().parent.parent
ASSETS = RACINE / "assets"
CACHE = RACINE / "cache" / "terre"
HD_NOM = "world.topo.bathy.200407.3x21600x10800.jpg"
HD_URL = ("https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/"
          "bmng-topography-bathymetry/july/" + HD_NOM)

UE = ["AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA", "DEU", "GRC", "HUN", "IRL",
      "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE"]
ATMOSPHERE = np.array([95, 155, 255], np.float32)
PAD = 2  # colonnes recopiees de part et d'autre de la texture (raccord a 180 degres)


@dataclass
class Camera:
    lon: float
    lat: float
    w: float          # demi-champ horizontal, en rayons terrestres
    fy: float = 0.55  # hauteur du point vise a l'ecran (0 = haut, 1 = bas)

    def __post_init__(self):  # toujours des nombres Python (pas de float64 numpy qui changerait les calculs)
        self.lon, self.lat, self.w, self.fy = float(self.lon), float(self.lat), float(self.w), float(self.fy)


def vecteur(lon: float, lat: float) -> np.ndarray:
    lo, la = math.radians(lon), math.radians(lat)
    return np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])


def lonlat(v: np.ndarray) -> tuple[float, float]:
    v = v / np.linalg.norm(v)
    return math.degrees(math.atan2(v[1], v[0])), math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))


def _repere(cam: Camera):
    lo, la = math.radians(cam.lon), math.radians(cam.lat)
    c = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    e = np.array([-math.sin(lo), math.cos(lo), 0.0])
    n = np.array([-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)])
    return c, e, n


# ------------------------------------------------------------------ texture

def _telecharger_hd() -> Path | None:
    f = CACHE / HD_NOM
    if f.exists() and f.stat().st_size > 5_000_000:
        return f
    if os.getenv("FLASH_MONDE_SANS_HD"):
        return None
    try:
        import requests

        CACHE.mkdir(parents=True, exist_ok=True)
        print("  téléchargement de l'image satellite HD de la NASA (une seule fois)...")
        tmp = f.with_suffix(".part")
        with requests.get(HD_URL, stream=True, timeout=60,
                          headers={"User-Agent": "FlashMonde/1.0"}) as r:
            r.raise_for_status()
            with open(tmp, "wb") as out:
                for bloc in r.iter_content(1 << 20):
                    out.write(bloc)
        if tmp.stat().st_size < 5_000_000:
            raise ValueError("fichier incomplet")
        tmp.rename(f)
        return f
    except Exception as err:
        print(f"  ! image satellite HD indisponible ({str(err)[:70]}) : version standard")
        return None


class Texture:
    """Pyramide de l'image (pleine resolution, 1/2, 1/4...) pour un rendu net sans scintillement."""

    def __init__(self, largeur_max: int = 21600, hd: bool = True):
        source = _telecharger_hd() if hd else None
        img = cv2.imread(str(source)) if source else None
        if source and img is None:  # fichier abime : on le retelechargera la prochaine fois
            print("  ! image satellite HD illisible : version standard")
            source.unlink(missing_ok=True)
        if img is None:
            img = cv2.imread(str(ASSETS / "carte" / "terre.jpg"))
            self.hd = False
        else:
            self.hd = True
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        while img.shape[1] > largeur_max:
            img = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA)
        self.largeur = img.shape[1]
        self.niveaux = []
        while True:
            self.niveaux.append(np.ascontiguousarray(np.concatenate([img[:, -PAD:], img, img[:, :PAD]], axis=1)))
            if img.shape[1] <= 1024:
                break
            img = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA)


# ------------------------------------------------------------------ geographie

class Pays:
    def __init__(self, d: dict):
        self.iso, self.adm, self.nom = d["iso"], d["adm"], d["nom"]
        self.iso2 = d.get("iso2")
        self.cx, self.cy, self.ext = d["cx"], d["cy"], d["ext"]
        polys = [d["c"]] if d["type"] == "Polygon" else d["c"]
        centre = vecteur(self.cx, self.cy)
        seuil = math.cos(math.radians(min(80.0, 1.2 * self.ext + 4.0)))
        self.polygones = []   # [[anneau exterieur, trous...], ...] en vecteurs 3D
        self.coeur = []       # polygones proches du centre : la ou s'etire le drapeau
        for poly in polys:
            anneaux = [np.array([vecteur(lo, la) for lo, la in a]) for a in poly if len(a) >= 3]
            if not anneaux:
                continue
            self.polygones.append(anneaux)
            m = anneaux[0].mean(axis=0)
            if np.dot(m / np.linalg.norm(m), centre) >= seuil:
                self.coeur.append(anneaux)
        if not self.coeur and self.polygones:  # pays en archipel lointain : on garde le plus grand
            self.coeur = [max(self.polygones, key=lambda p: len(p[0]))]


class Geo:
    def __init__(self):
        brut = json.loads((ASSETS / "carte" / "pays.json").read_text(encoding="utf-8"))
        self.pays = [Pays(d) for d in brut]
        self.index = {}
        for p in self.pays:
            self.index.setdefault(p.iso, p)
            self.index.setdefault(p.adm, p)
        chaines = self._frontieres(brut)
        self.f_pts = np.concatenate(chaines)
        tailles = np.array([len(c) for c in chaines])
        self.f_fin = np.cumsum(tailles)
        self.f_debut = self.f_fin - tailles

    @staticmethod
    def _frontieres(brut) -> list[np.ndarray]:
        """Frontieres terrestres : segments partages par deux pays differents (pas les cotes)."""
        def anneaux(d):
            for poly in ([d["c"]] if d["type"] == "Polygon" else d["c"]):
                yield from poly

        def cle(a, b):
            pa, pb = (round(a[0], 3), round(a[1], 3)), (round(b[0], 3), round(b[1], 3))
            return (pa, pb) if pa <= pb else (pb, pa)

        proprio: dict = {}
        for d in brut:
            for a in anneaux(d):
                for p, q in zip(a, a[1:]):
                    proprio.setdefault(cle(p, q), set()).add(d["adm"])
        chaines, faits = [], set()
        for d in brut:
            for a in anneaux(d):
                courante: list = []
                for p, q in zip(a, a[1:]):
                    k = cle(p, q)
                    if len(proprio.get(k, ())) >= 2 and k not in faits:
                        faits.add(k)
                        if not courante:
                            courante = [p]
                        courante.append(q)
                    elif courante:
                        chaines.append(courante)
                        courante = []
                if courante:
                    chaines.append(courante)
        return [np.array([vecteur(lo, la) for lo, la in c]) for c in chaines if len(c) >= 2]

    def codes(self, cible: str) -> list[str]:
        if cible == "UE":
            return [c for c in UE if c in self.index]
        return [cible] if cible in self.index else []


@lru_cache(maxsize=64)
def _drapeau(iso2: str | None) -> np.ndarray | None:
    if not iso2:
        return None
    f = ASSETS / "drapeaux" / f"{iso2.lower()}.png"
    if not f.exists():
        return None
    img = cv2.imread(str(f))
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if img is not None else None


# ------------------------------------------------------------------ rendu

class Globe:
    def __init__(self, L: int, H: int, largeur_max: int = 21600, hd: bool = True, tex=None, geo=None,
                 echelle: float = 1.0):
        self.L, self.H = L, H
        self.echelle = echelle  # 0.5 pour la version demi-resolution
        self.tex = tex or Texture(largeur_max, hd)
        self.geo = geo or Geo()
        self.xs = np.arange(L, dtype=np.float32) + 0.5
        self.ys = np.arange(H, dtype=np.float32) + 0.5
        self.etoiles = self._etoiles(L, H)
        self.atm = np.empty((H, L, 3), np.uint8)
        self.atm[:] = ATMOSPHERE.astype(np.uint8)

    @staticmethod
    def _etoiles(L, H) -> np.ndarray:
        rng = np.random.default_rng(7)
        ciel = np.zeros((H, L, 3), np.float32)
        ciel[:] = (4, 6, 14)
        n = int(L * H / 2600)
        ys, xs = rng.integers(0, H, n), rng.integers(0, L, n)
        lum = rng.random(n) ** 3 * 200 + 25
        ciel[ys, xs] += lum[:, None] * np.array([0.9, 0.95, 1.0])
        ciel = cv2.GaussianBlur(ciel, (0, 0), 0.7) * 1.6
        return np.clip(ciel, 0, 255).astype(np.uint8)

    def reduit(self, facteur: int = 2) -> "Globe":
        """Meme globe en plus petite resolution (poses intermediaires du flou de mouvement)."""
        g = Globe(self.L // facteur, self.H // facteur, tex=self.tex, geo=self.geo, echelle=1.0 / facteur)
        g.etoiles = cv2.resize(self.etoiles, (g.L, g.H), interpolation=cv2.INTER_AREA)
        return g

    def rayon(self, cam: Camera) -> float:
        return self.L / (2.0 * cam.w)

    # -- projection de points (frontieres, pays)
    def projeter(self, P: np.ndarray, cam: Camera, repere=None):
        c, e, n = repere or _repere(cam)
        R = self.rayon(cam)
        x, y, z = P @ e, P @ n, P @ c
        return self.L / 2 + R * x, self.H * cam.fy - R * y, z

    # -- fond satellite
    def fond(self, cam: Camera) -> np.ndarray:
        L, H = self.L, self.H
        R = self.rayon(cam)
        fx, fy = L / 2.0, H * cam.fy
        coin = math.hypot(max(fx, L - fx), max(fy, H - fy)) / R
        if coin <= 0.999:            # l'ecran est entierement dans le globe
            return self._globe(cam, 0, H, 0, L, bord=False)
        # bord du globe visible : on ne calcule que le disque et son halo, le reste est etoile
        marge = R + 0.14 * L
        y0, y1 = max(0, int(fy - marge)), min(H, int(fy + marge) + 1)
        x0, x1 = max(0, int(fx - marge)), min(L, int(fx + marge) + 1)
        img = self.etoiles.copy()
        if y1 > y0 and x1 > x0:
            img[y0:y1, x0:x1] = self._globe(cam, y0, y1, x0, x1, bord=True)
        return img

    def _globe(self, cam: Camera, y0: int, y1: int, x0: int, x1: int, bord: bool) -> np.ndarray:
        L = self.L
        R = self.rayon(cam)
        fx, fy = L / 2.0, self.H * cam.fy
        c, e, n = _repere(cam)
        x = (self.xs[x0:x1] - np.float32(fx)) / np.float32(R)
        y = (np.float32(fy) - self.ys[y0:y1]) / np.float32(R)
        rho2 = np.add.outer(y * y, x * x)
        if bord:
            rho = np.sqrt(rho2)
            k = 1.0 / np.maximum(rho, 1.0)
            X = x[None, :] * k
            Y = y[:, None] * k
            z = np.sqrt(np.maximum(1.0 - rho2, 0.0))
        else:
            X, Y = x[None, :], y[:, None]
            z = np.sqrt(1.0 - rho2)
        f = np.float32
        Wx = z * f(c[0]); Wx += X * f(e[0]); Wx += Y * f(n[0])
        Wy = z * f(c[1]); Wy += X * f(e[1]); Wy += Y * f(n[1])
        Wz = z * f(c[2]); Wz += Y * f(n[2])
        lon = np.arctan2(Wy, Wx)
        np.clip(Wz, -1.0, 1.0, out=Wz)
        lat = np.arcsin(Wz)

        # niveau de detail : pixels de texture par pixel d'ecran au point vise
        kx = self.tex.largeur / (2 * math.pi * R * max(0.2, math.cos(math.radians(cam.lat))))
        ky = self.tex.largeur / (2 * math.pi * R)
        lod = max(0.0, math.log2(math.sqrt(kx * ky)) + 0.3)
        lod = min(lod, len(self.tex.niveaux) - 1)
        n0 = int(lod)
        frac = lod - n0
        img = self._echantillon(n0, lon, lat)
        if frac > 0.02 and n0 + 1 < len(self.tex.niveaux):
            img2 = self._echantillon(n0 + 1, lon, lat)
            img = cv2.addWeighted(img, 1 - frac, img2, frac, 0)
        if lod < 0.6:  # image agrandie : on rend un peu de piqué
            flou = cv2.GaussianBlur(img, (0, 0), 1.3)
            img = cv2.addWeighted(img, 1.45, flou, -0.45, 0)

        if not bord:
            return img
        # atmosphere bleutee au bord, bord du globe lisse, halo dans l'espace
        rho = rho.astype(np.float32, copy=False)
        brume = np.clip((rho - 0.70) * np.float32(1 / 0.30), 0, 1)
        brume *= brume
        brume *= 0.40
        couv = np.clip((1.0 - rho) * R + 0.5, 0, 1)
        halo = np.exp(np.maximum(rho - 1.0, 0) * np.float32(-R / (0.03 * L)))
        halo *= 0.8
        atm = self.atm[y0:y1, x0:x1]
        img = cv2.blendLinear(img, atm, 1 - brume, brume)
        ciel = cv2.blendLinear(self.etoiles[y0:y1, x0:x1], atm, 1 - halo, halo)
        return cv2.blendLinear(img, ciel, couv, 1 - couv)

    def _echantillon(self, niveau: int, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        tex = self.tex.niveaux[niveau]
        th = tex.shape[0]
        tw = tex.shape[1] - 2 * PAD
        u = lon * np.float32(tw / (2 * math.pi))
        u += np.float32(tw / 2 - 0.5 + PAD)
        v = lat * np.float32(-th / math.pi)
        v += np.float32(th / 2 - 0.5)
        return cv2.remap(tex, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # -- frontieres terrestres, en blanc discret
    def dessiner_frontieres(self, img: np.ndarray, cam: Camera, opacite: float = 0.32) -> None:
        if opacite <= 0.01:
            return
        geo = self.geo
        x, y, z = self.projeter(geo.f_pts, cam)
        vis = z > 0.01
        d = geo.f_debut
        tous = np.logical_and.reduceat(vis, d)
        aucun = ~np.logical_or.reduceat(vis, d)
        m = 60
        ecran = ((np.maximum.reduceat(x, d) > -m) & (np.minimum.reduceat(x, d) < self.L + m)
                 & (np.maximum.reduceat(y, d) > -m) & (np.minimum.reduceat(y, d) < self.H + m))
        pts = np.round(np.stack([x, y], axis=1) * 8).astype(np.int32)
        fin = geo.f_fin
        traits = [pts[d[i]:fin[i]] for i in np.nonzero(tous & ecran)[0]]
        for i in np.nonzero(~tous & ~aucun & ecran)[0]:  # chaines a cheval sur le bord du globe
            v = vis[d[i]:fin[i]]
            debut = None
            for j, ok in enumerate(list(v) + [False]):
                if ok and debut is None:
                    debut = j
                elif not ok and debut is not None:
                    if j - debut >= 2:
                        traits.append(pts[d[i] + debut:d[i] + j])
                    debut = None
        if not traits:
            return
        R = self.rayon(cam) / self.echelle
        epais = 1 if R < 1100 else 2
        if R < 1100:
            opacite *= 0.8
        if self.echelle < 1:  # meme rendu qu'en pleine resolution une fois agrandi
            fin = epais * self.echelle
            epais = max(1, int(round(fin)))
            opacite *= min(1.0, fin / epais)
        trace = img.copy()
        cv2.polylines(trace, traits, False, (255, 255, 255), epais, cv2.LINE_AA, shift=3)
        cv2.addWeighted(img, 1 - opacite, trace, opacite, 0, dst=img)

    # -- pays en relief : drapeau, contour blanc, ombre
    def _masque(self, codes: list[str], cam: Camera, coeur_seul: bool):
        rep = _repere(cam)
        masque = np.zeros((self.H, self.L), np.uint8)
        boite = [1e9, 1e9, -1e9, -1e9]
        dessine = False
        for code in codes:
            p = self.geo.index.get(code)
            if not p:
                continue
            for poly in (p.coeur if coeur_seul else p.polygones):
                contours = []
                for i, anneau in enumerate(poly):
                    x, y, z = self.projeter(anneau, cam, rep)
                    if i == 0 and z.max() < 0:
                        break
                    derriere = z < 0
                    if derriere.any():  # ramene sur le bord du globe
                        cx, cy = x - self.L / 2, y - self.H * cam.fy
                        r = np.hypot(cx, cy) + 1e-9
                        Rr = self.rayon(cam)
                        x = np.where(derriere, self.L / 2 + cx / r * Rr, x)
                        y = np.where(derriere, self.H * cam.fy + cy / r * Rr, y)
                    contours.append(np.round(np.stack([x, y], axis=1) * 8).astype(np.int32))
                    if i == 0:
                        visibles = ~derriere
                        if visibles.any():
                            boite = [min(boite[0], x[visibles].min()), min(boite[1], y[visibles].min()),
                                     max(boite[2], x[visibles].max()), max(boite[3], y[visibles].max())]
                if contours:
                    cv2.fillPoly(masque, contours, 255, cv2.LINE_AA, shift=3)
                    dessine = True
        return (masque, boite) if dessine and boite[2] > boite[0] else (None, None)

    def dessiner_pays(self, img: np.ndarray, cam: Camera, cible: str, opacite: float, iso2: str | None,
                      relief: float = 1.0):
        """Pays principal : rempli par son drapeau, contour blanc, legere ombre portee.
        Renvoie le cadre du pays a l'ecran (x0, y0, x1, y1), pour placer son etiquette."""
        if opacite <= 0.01:
            return None
        codes = self.geo.codes(cible)
        masque, boite = self._masque(codes, cam, coeur_seul=True)
        if masque is None:
            return None
        x0, y0, x1, y1 = boite
        H, L = self.H, self.L
        k = self.echelle
        marge = int(40 * k)
        zx0, zy0 = max(0, int(x0) - marge), max(0, int(y0) - marge)
        zx1, zy1 = min(L, int(x1) + marge + 1), min(H, int(y1) + marge + 1)
        if zx1 <= zx0 or zy1 <= zy0:
            return None
        zone = (slice(zy0, zy1), slice(zx0, zx1))
        m = masque[zone]
        # ombre portee, decalee vers le bas
        dec = int(round(10 * relief * k))
        ombre = cv2.GaussianBlur(m, (0, 0), 9 * k)
        ombre = np.roll(np.roll(ombre, dec, axis=0), int(dec * 0.6), axis=1)
        _teinter(img[zone], ombre, (0, 0, 0), 0.55 * opacite)
        # drapeau etire sur le coeur du pays (deformation limitee)
        dr = _drapeau(iso2)
        if dr is not None:
            fh, fw = dr.shape[:2]
            bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
            fa, ba = fw / fh, bw / bh
            ea = min(max(ba, fa / 1.8), fa * 1.8)
            if ea >= ba:      # plus large que la boite : on deborde a gauche et a droite
                dh, dw = bh, bh * ea
            else:
                dw, dh = bw, bw / ea
            ox, oy = x0 + (bw - dw) / 2 - zx0, y0 + (bh - dh) / 2 - zy0
            M = np.array([[dw / fw, 0, ox + 0.5 * dw / fw - 0.5], [0, dh / fh, oy + 0.5 * dh / fh - 0.5]], np.float32)
            calque = cv2.warpAffine(dr, M, (zx1 - zx0, zy1 - zy0), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REPLICATE)
        else:
            calque = np.full((zy1 - zy0, zx1 - zx0, 3), (255, 212, 0), np.uint8)
        a = m.astype(np.float32) * (0.94 * opacite / 255.0)
        z = img[zone]
        z[:] = cv2.blendLinear(z, np.ascontiguousarray(calque), 1 - a, a)
        # contour blanc
        n = 7 if k >= 1 else 4
        noyau = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))
        contour = cv2.subtract(cv2.dilate(m, noyau), m)
        _teinter(z, contour, (255, 255, 255), opacite)
        return boite

    def dessiner_contour(self, img: np.ndarray, cam: Camera, cible: str, opacite: float) -> None:
        """Pays secondaire : simple liseré blanc et voile clair."""
        if opacite <= 0.01:
            return
        masque, boite = self._masque(self.geo.codes(cible), cam, coeur_seul=False)
        if masque is None:
            return
        _teinter(img, masque, (255, 255, 255), 0.12 * opacite)
        n = 5 if self.echelle >= 1 else 3
        noyau = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))
        contour = cv2.subtract(cv2.dilate(masque, noyau), masque)
        _teinter(img, contour, (255, 255, 255), 0.9 * opacite)

    def dessiner_arc(self, img: np.ndarray, cam: Camera, a: tuple, b: tuple, progres: float, opacite: float,
                     fleche: bool = True, couleur=(255, 212, 0), pulsation: float | None = None) -> None:
        """Arc lumineux de a vers b (lon, lat) qui s'eleve au-dessus du globe, trace jusqu'a `progres`."""
        if opacite <= 0.01 or progres <= 0.001:
            return
        va, vb = vecteur(*a), vecteur(*b)
        d = math.acos(max(-1.0, min(1.0, float(np.dot(va, vb)))))
        if d < 1e-4:
            return
        k = self.echelle
        s = np.linspace(0.0, min(1.0, progres), max(3, int(140 * progres) + 2))
        P = (np.sin((1 - s) * d)[:, None] * va + np.sin(s * d)[:, None] * vb) / math.sin(d)
        P *= (1 + min(0.32, 0.02 + 0.26 * d) * np.sin(np.pi * s))[:, None]  # altitude de l'arc
        x, y, z = self.projeter(P, cam)
        R = self.rayon(cam)
        cx, cy = (x - self.L / 2) / R, (self.H * cam.fy - y) / R
        vis = (z > 0) | (cx * cx + cy * cy > 1.0)  # devant le globe, ou au-dessus de son bord
        pts = np.round(np.stack([x, y], axis=1) * 8).astype(np.int32)
        morceaux, debut = [], None
        for j, ok in enumerate(list(vis) + [False]):
            if ok and debut is None:
                debut = j
            elif not ok and debut is not None:
                if j - debut >= 2:
                    morceaux.append(pts[debut:j])
                debut = None
        if not morceaux:
            return
        trait = np.zeros((self.H, self.L), np.uint8)
        cv2.polylines(trait, morceaux, False, 255, max(2, int(round(5 * k))), cv2.LINE_AA, shift=3)
        # pointe : fleche orientee dans le sens du trace, ou point d'arrivee pour un echange
        if vis[-1]:
            px, py = x[-1], y[-1]
            j = max(0, len(x) - 6)
            dx, dy = px - x[j], py - y[j]
            n = math.hypot(dx, dy) or 1.0
            dx, dy = dx / n, dy / n
            if fleche:
                t = 30 * k
                tri = np.array([[px + dx * t * 0.6, py + dy * t * 0.6],
                                [px - dx * t * 0.7 - dy * t * 0.55, py - dy * t * 0.7 + dx * t * 0.55],
                                [px - dx * t * 0.7 + dy * t * 0.55, py - dy * t * 0.7 - dx * t * 0.55]])
                cv2.fillPoly(trait, [np.round(tri * 8).astype(np.int32)], 255, cv2.LINE_AA, shift=3)
            elif progres >= 0.999:
                cv2.circle(trait, (int(px * 8), int(py * 8)), int(9 * k * 8), 255, -1, cv2.LINE_AA, shift=3)
        if vis[0]:
            cv2.circle(trait, (int(x[0] * 8), int(y[0] * 8)), int(8 * k * 8), 255, -1, cv2.LINE_AA, shift=3)
        halo = cv2.GaussianBlur(trait, (0, 0), 7 * k)
        _teinter(img, cv2.convertScaleAbs(halo, alpha=1.6), couleur, 0.9 * opacite)
        _teinter(img, trait, (255, 255, 255), opacite)
        # onde qui part du pays vise, une fois l'arc arrive
        if pulsation is not None and progres >= 0.999 and vis[-1]:
            r = (14 + 70 * pulsation) * k
            anneau = np.zeros((self.H, self.L), np.uint8)
            cv2.circle(anneau, (int(x[-1] * 8), int(y[-1] * 8)), int(r * 8), 255, max(2, int(4 * k)),
                       cv2.LINE_AA, shift=3)
            _teinter(img, anneau, couleur, opacite * (1 - pulsation) ** 1.5)

    def point_vers(self, code: str, cible: tuple, dedans: float = 0.3) -> tuple | None:
        """Point d'un pays tourne vers une cible (lon, lat) : sa frontiere la plus proche, un peu
        a l'interieur. Une fleche partant de la Russie part ainsi de l'ouest du pays, pas de Siberie."""
        p = self.geo.index.get(code)
        if not p or not p.coeur:
            return None
        vc = vecteur(*cible)
        sommets = np.concatenate([anneau for poly in p.coeur for anneau in poly[:1]])
        bord = sommets[int(np.argmax(sommets @ vc))]
        centre = vecteur(p.cx, p.cy)
        m = bord * (1 - dedans) + centre * dedans
        return lonlat(m)

    def ancre(self, cam: Camera, cible: str) -> tuple[float, float] | None:
        """Position a l'ecran du centre du pays (pour son etiquette)."""
        if cible == "UE":
            lon, lat = 10.0, 50.0
        else:
            p = self.geo.index.get(cible)
            if not p:
                return None
            lon, lat = p.cx, p.cy
        x, y, z = self.projeter(vecteur(lon, lat)[None, :], cam)
        return (float(x[0]), float(y[0])) if z[0] > 0.05 else None


def _teinter(img: np.ndarray, masque: np.ndarray, couleur, opacite: float) -> None:
    """Melange une couleur dans img selon un masque 0-255, en place."""
    if opacite <= 0.003:
        return
    x, y, w, h = cv2.boundingRect(masque)  # on ne traite que la zone utile
    if w == 0 or h == 0:
        return
    zone = img[y:y + h, x:x + w]
    a = masque[y:y + h, x:x + w].astype(np.float32) * (opacite / 255.0)
    teinte = np.empty_like(zone)
    teinte[:] = couleur
    zone[:] = cv2.blendLinear(zone, teinte, 1 - a, a)
