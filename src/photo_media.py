"""Photo d'actualite reprise d'un article source (option images.photos_medias), avec credit du media.

Ordre : la photo donnee par le flux RSS, sinon l'image de partage de la page (og:image).
Les petites images, logos et images par defaut sont ecartees. Rien de bon : (None, "") et le
montage revient au portrait libre ou a la carte seule.

Choix de l'article (24/11/2026, la photo montrait souvent quelqu'un d'autre) :
- seuls les articles qui parlent vraiment du fait lu sont gardes (2 mots significatifs en commun
  avec le titre et le texte de la breve) : un article hors sujet glisse dans le groupe est ignore ;
- si l'info tourne autour d'une personnalite, la photo ne vient que d'un article qui la nomme dans
  son titre ; sinon rien, et le montage prend son portrait libre (forcement la bonne personne) ;
- les articles les plus proches du sujet passent en premier, puis les plus recents.
"""
from __future__ import annotations

import hashlib
import html
import io
import re
from pathlib import Path

import requests
from PIL import Image, ImageOps

from . import cluster

CACHE = Path("cache/medias")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
LARGEUR_MIN = 640
INTERDITS = ("logo", "default", "placeholder", "avatar", "icon", "favicon", "sprite", "blank", "pixel")
PAGES_MAX = 4


def _og_image(lien: str) -> str:
    """Image de partage (og:image, sinon twitter:image) de la page de l'article."""
    try:
        r = requests.get(lien, headers=UA, timeout=15)
        r.raise_for_status()
    except Exception:
        return ""
    page = r.text[:400_000]
    for prop in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        for motif in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']+)',
                      rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(prop)}["\']'):
            m = re.search(motif, page, re.I)
            if m:
                return requests.compat.urljoin(lien, html.unescape(m.group(1)))
    return ""


def _telecharger(url: str) -> Image.Image | None:
    if not url or any(x in url.lower() for x in INTERDITS):
        return None
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert("RGB")
    except Exception:
        return None
    w, h = img.size
    if w < LARGEUR_MIN or h < 360 or not 0.9 <= w / h <= 2.4:  # trop petite, ou bandeau / format vertical
        return None
    if img.convert("L").resize((32, 32)).getextrema()[1] - img.convert("L").resize((32, 32)).getextrema()[0] < 40:
        return None  # image quasi unie (fond par defaut)
    return img


origine = ""  # article d'ou vient la derniere photo trouvee (montre sur Telegram pour verifier)


def _nu(texte: str) -> str:
    return cluster.sans_accents(texte or "").lower()


def _nom_cle(nom: str) -> str:
    """« Volodymyr Zelensky » -> « zelensky » ; nom tres court : le nom complet."""
    morceaux = (nom or "").split()
    cle = _nu(morceaux[-1]) if morceaux else ""
    return cle if len(cle) >= 3 else _nu(nom)


def _mots(texte: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", _nu(texte)).strip() + " "


def _nomme(titre: str, noms: list) -> bool:
    """Le titre nomme-t-il l'une de ces personnes ? (nom de famille, sans accents ni ponctuation)"""
    t = _mots(titre)
    return any(c.strip() and c in t for c in (_mots(_nom_cle(n)) for n in noms if n))


def candidats(articles: list, sujet: dict | None) -> list:
    """Articles dont la photo peut illustrer l'info, du plus sur au moins sur."""
    if not sujet:
        return sorted(articles, key=lambda a: getattr(a, "date", ""), reverse=True)
    ref = cluster.jetons(f"{sujet.get('titre', '')} {sujet.get('texte', '')}")
    notes = []
    for a in articles:
        n = len(cluster.jetons(f"{a.titre} {getattr(a, 'resume', '')[:350]}") & ref)
        if n >= 2:
            notes.append((min(n, 6), getattr(a, "date", ""), a))
    if sujet.get("personne"):
        noms = [sujet["personne"], sujet.get("avec")]
        notes = [x for x in notes if _nomme(x[2].titre, noms)]
    notes.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [a for _, _, a in notes]


def chercher(articles: list, sujet: dict | None = None) -> tuple[Path | None, str]:
    """Renvoie (chemin de la photo, credit « Photo : <media> ») ou (None, '').
    sujet : la breve (titre, texte, personne, avec) pour ne garder que les articles qui lui correspondent."""
    global origine
    origine = ""
    CACHE.mkdir(parents=True, exist_ok=True)
    recents = candidats(articles, sujet)
    if sujet and not recents:
        print("  - pas de photo média sûre pour cette info (aucun article qui nomme la personnalité ou "
              "colle au texte)")
    pages = 0
    for a in recents:
        essais = [getattr(a, "image", "")]
        img = _telecharger(essais[0])
        if img is None and getattr(a, "lien", "") and pages < PAGES_MAX:
            pages += 1
            img = _telecharger(_og_image(a.lien))
        elif img is not None and img.size[0] < 900 and getattr(a, "lien", "") and pages < PAGES_MAX:
            pages += 1  # miniature du flux : la page en a souvent une plus grande
            plus = _telecharger(_og_image(a.lien))
            if plus is not None and plus.size[0] > img.size[0]:
                img = plus
        if img is None:
            continue
        img.thumbnail((1600, 1600))
        chemin = CACHE / (hashlib.sha1((a.lien or a.titre).encode()).hexdigest()[:16] + ".jpg")
        img.save(chemin, quality=90)
        print(f"  + photo de {a.source} ({img.size[0]}x{img.size[1]}) : {a.titre[:70]}")
        origine = f"photo {a.source}, article « {a.titre[:90]} »"
        return chemin, f"Photo : {a.source}"
    return None, ""
