"""Regroupe les articles qui parlent du meme fait et classe les sujets.

Principe : une info n'est retenue que si plusieurs medias differents la rapportent.
C'est le garde-fou principal contre les erreurs et les infos non confirmees.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

VIDES = set(
    """
    les des une que qui dans pour par sur avec sans sous vers chez entre apres avant contre
    est sont ete etre avoir ont aux ces ses son leur leurs cette cet mais donc car pas plus
    moins tres tout tous toute toutes comme alors ainsi aussi encore deja depuis pendant lors
    selon face fait faire dit annonce annoncent apres nouveau nouvelle nouveaux deux trois
    quatre cinq ans jour jours mois annee annees lundi mardi mercredi jeudi vendredi samedi
    dimanche pays monde premier premiere quand quel quelle pourquoi comment ce qu faut etait
    the and
    """.split()
)


def sans_accents(texte: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )


def jetons(texte: str) -> set[str]:
    """Mots significatifs, sans accents, reduits a leur racine (6 premieres lettres)."""
    mots = re.findall(r"[a-z0-9]+", sans_accents(texte.lower()))
    return {m[:6] for m in mots if len(m) >= 3 and m not in VIDES}


def _proches(a: dict, b: dict) -> bool:
    commun_titre = len(a["jt"] & b["jt"])
    union = len(a["j"] | b["j"]) or 1
    jaccard = len(a["j"] & b["j"]) / union
    return commun_titre >= 3 or (commun_titre >= 2 and jaccard >= 0.25) or jaccard >= 0.4


def regrouper(articles: list) -> list[list]:
    """Regroupement simple : un article rejoint un groupe s'il ressemble a l'un de ses membres."""
    fiches = [
        {"a": a, "jt": jetons(a.titre), "j": jetons(a.titre + " " + a.resume[:250])}
        for a in articles
    ]
    groupes: list[list[dict]] = []
    for f in fiches:
        for g in groupes:
            if any(_proches(f, m) for m in g):
                g.append(f)
                break
        else:
            groupes.append([f])
    return [[m["a"] for m in g] for g in groupes]


def signature(groupe: list) -> list[str]:
    """Empreinte d'un sujet, pour ne pas le republier a l'edition suivante."""
    compte: dict[str, int] = {}
    for a in groupe:
        for j in jetons(a.titre):
            compte[j] = compte.get(j, 0) + 1
    return sorted(sorted(compte, key=lambda j: (-compte[j], j))[:8])


def deja_vu(sig: list[str], vus: list[dict]) -> bool:
    s = set(sig)
    for v in vus:
        t = set(v["sig"])
        if s and t and len(s & t) / len(s | t) >= 0.5:
            return True
    return False


def classer(groupes: list[list], min_sources: int, vus: list[dict]) -> list[dict]:
    """Garde les sujets recoupes et non deja publies, du plus repris au moins repris."""
    maintenant = datetime.now(timezone.utc)
    sujets = []
    for g in groupes:
        sources = sorted({a.source for a in g})
        if len(sources) < min_sources:
            continue
        sig = signature(g)
        if deja_vu(sig, vus):
            continue
        plus_recent = max(datetime.fromisoformat(a.date) for a in g)
        age_h = (maintenant - plus_recent).total_seconds() / 3600
        score = len(sources) * 10 - age_h
        sujets.append({"articles": g, "sources": sources, "sig": sig, "score": score})
    return sorted(sujets, key=lambda s: -s["score"])
