"""Portrait d'une personnalite publique, depuis Wikimedia Commons (licences libres uniquement).

1. On rassemble plusieurs photos candidates : l'image officielle de sa fiche Wikidata, la photo
   de sa page Wikipedia (fr puis en), et les photos de Commons qui portent son nom.
2. On ne garde que les licences libres qui autorisent la reutilisation commerciale (domaine public,
   CC0, CC BY, CC BY-SA). Toute photo non libre ou de statut incertain est refusee.
3. On note chaque photo : source officielle (Kremlin, presidence ukrainienne, Maison-Blanche,
   institutions europeennes...), photo recente, visage bien visible, net et seul a l'image.
4. La meilleure est recadree sur le visage (plan buste) et recoit le meme traitement couleur que
   toutes les autres, pour un rendu homogene. Si aucune n'est assez bonne : pas de portrait,
   la video reste sur la carte et le drapeau.
Le credit (auteur + licence) est toujours affiche a l'ecran.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageEnhance, ImageOps

try:  # present sur GitHub ; sur le Mac on s'en passe (choix plus simple, sans detection de visage)
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

CACHE = Path("cache/portraits")
VERSION = "v2"                     # changer la version refait les portraits deja en cache
UA = {"User-Agent": "FlashMonde/1.0 (https://github.com/Boubashmd/flash-monde)"}
LICENCES_OK = re.compile(r"^(cc0|public domain|pd|ogl|cc[ -]by(-sa)?([ -]\d(\.\d)?)?)", re.I)
INTERDIT = re.compile(r"\b(nc|nd|fair use|non-free)\b", re.I)
OFFICIEL = re.compile(
    r"kremlin\.ru|president\.gov\.ua|white ?house|state\.gov|defense\.gov|\.mil\b|"
    r"european (union|parliament|commission|council)|europarl|ec\.europa|consilium|"
    r"gouvernement|elysee|bundesregierung|number ?10|gov\.uk|un photo|nato|"
    r"official portrait|portrait officiel|offizielles portr", re.I)
EXCLURE = re.compile(r"signature|logo|coat of arms|blason|armoiries|flag|drapeau|map|carte|\.svg$|\.gif$|"
                     r"\.tif", re.I)
FORMAT = (1120, 800)               # meme proportion que l'encart de la video (7:5)
NB_TELECHARGES = 6                 # photos analysees au maximum
VISAGE_MIN = 110                   # hauteur minimale du visage en pixels (sinon flou une fois agrandi)


def _api(hote: str, **params) -> dict:
    params.update(format="json", formatversion=2)
    r = requests.get(f"https://{hote}/w/api.php", params=params, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json()


def _propre(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def _sans_accents(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


# ------------------------------------------------------------------ candidates

def _candidats(nom: str) -> list:
    """Noms de fichiers Commons, du plus fiable au moins fiable (sans doublon)."""
    titres = []
    # photo de la page Wikipedia + identifiant Wikidata
    qid = None
    for langue in ("fr", "en"):
        try:
            d = _api(f"{langue}.wikipedia.org", action="query", titles=nom, prop="pageimages|pageprops",
                     piprop="name", ppprop="wikibase_item", redirects=1)
        except Exception:
            continue
        for page in d.get("query", {}).get("pages", []):
            qid = qid or page.get("pageprops", {}).get("wikibase_item")
            if page.get("pageimage"):
                titres.append(page["pageimage"])
    # image officielle de la fiche Wikidata (P18), choisie par les contributeurs
    if qid:
        try:
            d = requests.get("https://www.wikidata.org/w/api.php", headers=UA, timeout=20, params={
                "action": "wbgetclaims", "entity": qid, "property": "P18", "format": "json"}).json()
            for c in d.get("claims", {}).get("P18", []):
                v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
                if v:
                    titres.insert(0, v)
        except Exception:
            pass
    # autres photos de Commons portant son nom
    nom_famille = _sans_accents(nom.split()[-1]) if nom.split() else ""
    try:
        d = _api("commons.wikimedia.org", action="query", list="search", srnamespace=6, srlimit=20,
                 srsearch=f'"{nom}" filetype:bitmap')
        for r in d.get("query", {}).get("search", []):
            t = r["title"].split(":", 1)[-1]
            if nom_famille and nom_famille in _sans_accents(t):
                titres.append(t)
    except Exception:
        pass
    vus, out = set(), []
    for t in titres:
        t = t.replace("_", " ")
        if t not in vus and not EXCLURE.search(t):
            vus.add(t)
            out.append(t)
    return out[:25]


def _infos(titres: list) -> list:
    """Licence, auteur, date, taille et lien de chaque fichier ; ne garde que le libre."""
    if not titres:
        return []
    d = _api("commons.wikimedia.org", action="query", titles="|".join(f"File:{t}" for t in titres),
             prop="imageinfo", iiprop="url|extmetadata|size|mime", iiurlwidth=1600)
    par_titre = {p["title"].split(":", 1)[-1]: p for p in d.get("query", {}).get("pages", [])}
    out = []
    for rang, t in enumerate(titres):
        page = par_titre.get(t)
        if not page or page.get("missing") or not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        if min(info.get("width", 0), info.get("height", 0)) < 400:
            continue
        em = info.get("extmetadata", {})
        licence = _propre(em.get("LicenseShortName", {}).get("value", ""))
        if not LICENCES_OK.match(licence) or INTERDIT.search(licence):
            continue
        auteur = _propre(em.get("Artist", {}).get("value", "")) or "auteur inconnu"
        texte = " ".join(_propre(em.get(k, {}).get("value", "")) for k in ("Artist", "Credit", "ImageDescription"))
        annee = None
        m = re.search(r"(19|20)\d\d", _propre(em.get("DateTimeOriginal", {}).get("value", "")))
        if m:
            annee = int(m.group(0))
        officiel = bool(OFFICIEL.search(texte))
        # note sans regarder l'image : rang (Wikidata/Wikipedia d'abord), officiel, recent
        note = max(0.0, 2.0 - 0.25 * rang) + (4.0 if officiel else 0.0)
        if annee:
            age = datetime.now().year - annee
            note += 3.0 if age <= 3 else 1.5 if age <= 6 else 0.0 if age <= 10 else -2.0
        out.append({"titre": t, "url": info.get("thumburl") or info["url"], "licence": licence,
                    "auteur": auteur, "annee": annee, "note": note, "officiel": officiel})
    return out


# ------------------------------------------------------------------ analyse de l'image

MODELE_VISAGE = Path(__file__).resolve().parent.parent / "assets" / "modeles" / "face_detection_yunet_2023mar.onnx"


def _detecteur():
    """Fonction image -> liste de visages (x, y, w, h) ; None si OpenCV est absent (cas du Mac)."""
    if cv2 is None:
        return None
    if hasattr(cv2, "FaceDetectorYN") and MODELE_VISAGE.exists():   # YuNet : fiable, visages de 3/4 compris
        try:
            yunet = cv2.FaceDetectorYN.create(str(MODELE_VISAGE), "", (320, 320), 0.75, 0.3, 50)

            def detecter(bgr):
                yunet.setInputSize((bgr.shape[1], bgr.shape[0]))
                _, faces = yunet.detect(bgr)
                return [] if faces is None else [tuple(f[:4]) for f in faces]
            return detecter
        except Exception:
            pass
    if hasattr(cv2, "CascadeClassifier"):                            # ancien detecteur, en secours
        try:
            haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            if not haar.empty():
                def detecter(bgr):
                    gris = cv2.equalizeHist(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
                    cote = int(min(gris.shape) * 0.07)
                    return [tuple(f) for f in haar.detectMultiScale(gris, scaleFactor=1.08, minNeighbors=6,
                                                                    minSize=(cote, cote))]
                return detecter
        except Exception:
            pass
    return None


def analyser(img: Image.Image, detecteur) -> dict | None:
    """Visage principal : position, taille, nettete. None si pas de visage exploitable."""
    rgb = np.asarray(img.convert("RGB"))
    gris = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gris.shape
    echelle = min(1.0, 1000 / max(h, w))  # detection sur une copie reduite (plus rapide)
    petit = cv2.resize(rgb, (int(w * echelle), int(h * echelle)), interpolation=cv2.INTER_AREA)
    faces = detecteur(np.ascontiguousarray(petit[:, :, ::-1]))
    faces = [f for f in faces if min(f[2], f[3]) >= 0.05 * min(petit.shape[:2])]
    if not faces:
        return None
    faces = sorted(((x / echelle, y / echelle, fw / echelle, fh / echelle) for x, y, fw, fh in faces),
                   key=lambda f: -f[2] * f[3])
    x, y, fw, fh = faces[0]
    seul = len(faces) == 1 or faces[1][2] * faces[1][3] < 0.35 * fw * fh
    zone = gris[max(0, int(y)):max(0, int(y + fh)), max(0, int(x)):max(0, int(x + fw))]
    nettete = float(cv2.Laplacian(zone, cv2.CV_64F).var()) if zone.size else 0.0
    return {"x": x, "y": y, "w": fw, "h": fh, "seul": seul, "nettete": nettete}


def note_visage(v: dict | None, img_h: int) -> float | None:
    if v is None or v["h"] < VISAGE_MIN:
        return None
    note = min(v["h"] / 180, 2.5)                          # visage grand = image detaillee
    note += min(v["nettete"] / 150, 2.0)                    # net
    note += 0.0 if v["seul"] else -4.0                     # photo de groupe : a eviter
    note += 1.0 if v["h"] / img_h > 0.12 else 0.0           # personne au premier plan
    return note


def recadrer(img: Image.Image, v: dict | None) -> Image.Image:
    """Plan buste centre sur le visage, au format de l'encart (7:5)."""
    W, H = img.size
    ratio = FORMAT[0] / FORMAT[1]
    if v is None:  # sans detection : on garde le haut de l'image, ou se trouve presque toujours le visage
        return ImageOps.fit(img, FORMAT, Image.LANCZOS, centering=(0.5, 0.25))
    ch = min(H, v["h"] * 2.7)
    cw = ch * ratio
    if cw > W:
        cw, ch = W, W / ratio
    cx = v["x"] + v["w"] / 2
    cy = v["y"] + v["h"] * 0.62                           # visage un peu au-dessus du centre
    x0 = min(max(0, cx - cw / 2), W - cw)
    y0 = min(max(0, cy - ch / 2), H - ch)
    return img.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize(FORMAT, Image.LANCZOS)


def traiter(img: Image.Image) -> Image.Image:
    """Meme rendu pour tous les portraits : contraste regle, couleurs adoucies, ombres bleutees, vignette."""
    img = ImageOps.autocontrast(img.convert("RGB"), cutoff=1)
    img = ImageEnhance.Color(img).enhance(0.9)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    a = np.asarray(img).astype(np.float32) / 255
    lum = a.mean(axis=2, keepdims=True)
    ombre = np.array([0.06, 0.09, 0.16], np.float32)        # bleu nuit dans les ombres
    lumiere = np.array([1.0, 0.98, 0.93], np.float32)       # hautes lumieres legerement chaudes
    a = a * (1 - 0.12 * (1 - lum)) + ombre * 0.12 * (1 - lum)
    a = a * (0.94 + 0.06 * lumiere * lum)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h * 0.45) / (h / 2)) ** 2)
    a *= (1 - 0.35 * np.clip(d - 0.5, 0, 1) ** 1.4)[..., None]
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


# ------------------------------------------------------------------ point d'entree

def chercher(nom: str) -> tuple[Path | None, str]:
    """Renvoie (chemin du portrait pret a l'emploi, credit a afficher) ou (None, '') si rien de bon."""
    cle = re.sub(r"[^a-z0-9]+", "-", _sans_accents(nom)).strip("-")
    dossier = CACHE / f"{cle}-{VERSION}"
    meta = dossier / "credit.json"
    photo = dossier / "photo.jpg"
    if meta.exists():
        info = json.loads(meta.read_text(encoding="utf-8"))
        return (photo, info["credit"]) if info.get("ok") and photo.exists() else (None, "")
    dossier.mkdir(parents=True, exist_ok=True)

    def refuser(raison: str):
        print(f"  - pas de portrait pour {nom} ({raison}) : carte à la place")
        meta.write_text(json.dumps({"ok": False, "raison": raison}, ensure_ascii=False), encoding="utf-8")
        return None, ""

    try:
        candidats = _infos(_candidats(nom))
        if not candidats:
            return refuser("aucune photo sous licence libre")
        candidats.sort(key=lambda c: -c["note"])
        detecteur = _detecteur()
        meilleur, meilleure_note = None, None
        for c in candidats[:NB_TELECHARGES if detecteur else 1]:
            try:
                brut = requests.get(c["url"], headers=UA, timeout=60).content
                img = ImageOps.exif_transpose(Image.open(io.BytesIO(brut))).convert("RGB")
            except Exception:
                continue
            v = None
            if detecteur:
                v = analyser(img, detecteur)
                nv = note_visage(v, img.size[1])
                if nv is None:
                    print(f"    · écartée (visage absent ou trop petit) : {c['titre'][:60]}")
                    continue
                note = c["note"] + nv
            else:
                note = c["note"]
            if meilleure_note is None or note > meilleure_note:
                meilleur, meilleure_note = (c, img, v), note
        if meilleur is None:
            return refuser("aucune photo assez nette avec le visage bien visible")
        c, img, v = meilleur
        traiter(recadrer(img, v)).save(photo, quality=92)
        credit = f"Photo : {c['auteur'][:60]} / Wikimedia Commons, {c['licence']}"
        meta.write_text(json.dumps({"ok": True, "credit": credit, "fichier": c["titre"],
                                    "officiel": c["officiel"], "annee": c["annee"]}, ensure_ascii=False),
                        encoding="utf-8")
        print(f"  - portrait de {nom} : {c['titre'][:70]}{' (officiel)' if c['officiel'] else ''}")
        return photo, credit
    except Exception as err:  # reseau, format inattendu : on ne bloque jamais l'edition
        print(f"  - portrait indisponible pour {nom} ({str(err)[:60]})")
        return None, ""
