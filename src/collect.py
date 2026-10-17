"""Collecte des articles depuis les flux RSS."""
from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

import feedparser
import requests

UA = "Mozilla/5.0 (compatible; FlashMonde/1.0; lecteur RSS)"


@dataclass
class Article:
    source: str
    titre: str
    resume: str
    lien: str
    date: str  # ISO 8601, UTC
    groupe: str = ""  # groupe de presse : deux titres du meme groupe ne comptent que pour un media

    def to_dict(self):
        return asdict(self)


def _propre(texte: str) -> str:
    texte = re.sub(r"<[^>]+>", " ", texte or "")
    texte = html.unescape(texte)
    return re.sub(r"\s+", " ", texte).strip()


def lire_flux(nom: str, url: str, timeout: int = 20) -> list[Article]:
    """Lit un flux. Leve une exception si le flux est injoignable ou vide."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    flux = feedparser.parse(r.content)
    articles = []
    for e in flux.entries:
        titre = _propre(e.get("title", ""))
        if not titre:
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        # published_parsed est deja exprime en UTC
        date = datetime(*t[:6], tzinfo=timezone.utc) if t else datetime.now(timezone.utc)
        articles.append(
            Article(
                source=nom,
                titre=titre,
                resume=_propre(e.get("summary", ""))[:600],
                lien=e.get("link", ""),
                date=date.isoformat(),
            )
        )
    if not articles:
        raise ValueError("flux vide ou illisible")
    return articles


def lire_source(s: dict) -> list[Article]:
    """Essaie les adresses de la source dans l'ordre (« url » ou liste « urls ») ; garde la premiere qui marche."""
    adresses = s.get("urls") or [s["url"]]
    derniere = None
    for url in adresses:
        try:
            arts = lire_flux(s["nom"], url)
            for a in arts:
                a.groupe = s.get("groupe", s["nom"])
            return arts
        except Exception as err:
            derniere = err
    raise derniere or ValueError("aucune adresse")


def collecter(sources: list[dict], fenetre_heures: float, exclure: list[str]):
    """Renvoie (articles recents, etat de chaque source). Les flux sont lus en parallele."""
    limite = datetime.now(timezone.utc) - timedelta(hours=fenetre_heures)
    exclure = [x.lower() for x in exclure]

    def lire(s):
        try:
            return s, lire_source(s), None
        except Exception as err:  # une source en panne ne doit pas bloquer l'edition
            return s, [], err

    articles, etat = [], []
    with ThreadPoolExecutor(8) as pool:
        for s, tous, err in pool.map(lire, sources):
            if err is not None:
                etat.append({"nom": s["nom"], "ok": False, "detail": str(err)[:120],
                             "optionnel": bool(s.get("optionnel"))})
                continue
            recents = [
                a
                for a in tous
                if datetime.fromisoformat(a.date) >= limite
                and not any(x in a.titre.lower() for x in exclure)
            ]
            etat.append({"nom": s["nom"], "ok": True, "detail": f"{len(recents)} recents / {len(tous)}",
                         "optionnel": bool(s.get("optionnel"))})
            articles.extend(recents)
    return articles, etat
