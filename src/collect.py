"""Collecte des articles depuis les flux RSS."""
from __future__ import annotations

import html
import re
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


def collecter(sources: list[dict], fenetre_heures: float, exclure: list[str]):
    """Renvoie (articles recents, etat de chaque source)."""
    limite = datetime.now(timezone.utc) - timedelta(hours=fenetre_heures)
    exclure = [x.lower() for x in exclure]
    articles, etat = [], []
    for s in sources:
        try:
            tous = lire_flux(s["nom"], s["url"])
        except Exception as err:  # une source en panne ne doit pas bloquer l'edition
            etat.append({"nom": s["nom"], "ok": False, "detail": str(err)[:120]})
            continue
        recents = [
            a
            for a in tous
            if datetime.fromisoformat(a.date) >= limite
            and not any(x in a.titre.lower() for x in exclure)
        ]
        etat.append({"nom": s["nom"], "ok": True, "detail": f"{len(recents)} recents / {len(tous)}"})
        articles.extend(recents)
    return articles, etat
