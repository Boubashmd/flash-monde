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


def deja_vu(sig: list[str], vus: list[dict]) -> dict | None:
    """Derniere publication du meme sujet (ou None si le sujet n'a jamais ete publie)."""
    s = set(sig)
    trouve = None
    for v in vus:
        t = set(v["sig"])
        if s and t and len(s & t) / len(s | t) >= 0.5:
            if trouve is None or v["date"] > trouve["date"]:
                trouve = v
    return trouve


def _date(a) -> datetime | None:
    try:
        d = datetime.fromisoformat(a.date)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def classer(groupes: list[list], min_sources: int, vus: list[dict],
            suites: bool = True, suite_apres_h: float = 5) -> list[dict]:
    """Garde les sujets recoupes, du plus repris au moins repris.

    Un sujet deja publie ne revient que comme « suite » : s'il a ete publie il y a au moins
    `suite_apres_h` heures ET qu'au moins `min_sources` medias ont publie de nouveaux articles
    depuis. Seuls ces nouveaux articles sont alors donnes a la redaction (le fait nouveau).
    """
    maintenant = datetime.now(timezone.utc)
    sujets = []
    for g in groupes:
        if len({a.source for a in g}) < min_sources:
            continue
        sig = signature(g)
        suite = None
        precedent = deja_vu(sig, vus)
        if precedent:
            if not suites:
                continue
            publie = datetime.fromisoformat(precedent["date"])
            if (maintenant - publie).total_seconds() / 3600 < suite_apres_h:
                continue
            nouveaux = [a for a in g if (_date(a) or publie) > publie]
            if len({a.source for a in nouveaux}) < min_sources:
                continue
            g, suite = nouveaux, precedent.get("titre", "")
        sources = sorted({a.source for a in g})
        dates = [d for d in (_date(a) for a in g) if d]
        age_h = (maintenant - max(dates)).total_seconds() / 3600 if dates else 0
        score = len(sources) * 10 - age_h - (5 if suite is not None else 0)
        sujets.append({"articles": g, "sources": sources, "sig": sig, "score": score, "suite": suite})
    return sorted(sujets, key=lambda s: -s["score"])
