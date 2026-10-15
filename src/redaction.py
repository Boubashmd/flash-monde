"""Redaction du texte lu a l'antenne.

Regle d'or : on ne dit que ce que plusieurs sources affirment, avec nos propres mots.
"""
from __future__ import annotations

import json
import os
import re

TITRE_MAX = 60

CONSIGNES = """Tu es secrétaire de rédaction d'un flash d'information vidéo en français, consacré à la politique, à la géopolitique et à la diplomatie. Il est lu par une voix de synthèse.

Pour chaque sujet, tu reçois les titres et résumés de plusieurs médias. Rédige une brève.

Faits :
- N'écris QUE des faits présents dans au moins deux des sources fournies. N'ajoute aucune information, aucun contexte de ta propre connaissance.
- Reformule entièrement : ne reprends aucune phrase ni tournure d'une source.
- Ton neutre et factuel. Aucun adjectif de jugement, aucun commentaire, aucune supposition.
- Si les sources emploient le conditionnel ou citent quelqu'un, garde cette prudence ("selon...", "d'après...").
- Sur les conflits et la politique, emploie le vocabulaire le plus neutre des sources et attribue les affirmations de chaque camp ("selon Kiev", "d'après l'armée israélienne").

Chiffres et victimes :
- Si les sources donnent des morts ou des blessés, mentionne-les toujours.
- Si les chiffres divergent, retiens la valeur la plus basse avec "au moins" (12 et 14 morts : "au moins douze morts").
- Ne change jamais l'ordre de grandeur : 300 blessés, c'est "des centaines de blessés", jamais "des dizaines".

Texte :
- "texte" : 2 phrases, 260 caractères maximum, faciles à dire à voix haute. Pas de sigle obscur, pas de parenthèses, pas de guillemets.
- Il commence par le fait principal, pas par une mise en contexte.

Titre : style d'agence de presse (AFP, Reuters), 60 caractères maximum, sans point final.
- C'est une phrase complète : SUJET explicite + verbe conjugué + complément. On doit savoir QUI fait QUOI.
- Le sujet est nommé : un pays ou sa capitale (Moscou, Washington, Kiev), une institution (l'Otan, l'UE), un dirigeant ou un groupe (les Houthis). Jamais de titre qui commence par un verbe.
- Le fait le plus fort en premier, au présent. Accrocheur par la précision, pas par l'adjectif.
- Interdits : exagération, "choc", "incroyable", "scandale", majuscules de cri, point d'exclamation, question, suspense sans réponse dans le texte, pronostic.
- Si quelqu'un ne lit que le titre, il ne doit rien croire de faux.
- Exemples :
  faux (pas de sujet) : "Arrête le rédacteur en chef de Novaïa Gazeta en Russie"
  bon : "Moscou arrête le rédacteur en chef de Novaïa Gazeta"
  faux (pas de sujet) : "Perturbe le réseau satellitaire Starshield en Ukraine"
  bon : "La Russie brouille le réseau Starshield en Ukraine"
  plat : "Donald Trump annonce une trêve énergétique Ukraine-Russie non confirmée"
  bon : "Trêve de Trump : ni Kiev ni Moscou ne confirment"
  plat : "Arabie saoudite : aéroport de Riyad fermé après des attaques houthis"
  bon : "Les Houthis frappent Riyad, l'aéroport fermé"

Tri :
- "politique" : true si le cœur du sujet est la politique intérieure ou internationale, la géopolitique, la diplomatie, un conflit armé, des sanctions, une élection, ou une décision de justice visant des responsables politiques. false pour l'économie, la science, la culture, le sport, les faits divers ou les catastrophes naturelles, sauf si leur cœur est une décision ou un affrontement politique.
- "ecarter" : true si les articles d'un sujet ne parlent pas du même fait, ou se contredisent sur l'essentiel.
- "doublon_de" : si ce sujet raconte le même fait ou la même affaire qu'un sujet précédent de la liste (même événement vu sous un autre angle), mets l'id de ce sujet précédent ; sinon null.

Illustration :
- "pays" : liste de codes ISO 3166-1 alpha-3 des pays concernés, le principal en premier (ex. ["UKR", "RUS"]). "UE" si le sujet concerne l'Union européenne en tant que telle, "MONDE" s'il est mondial.
- "personne" : nom complet, tel que sur Wikipédia, de LA personnalité publique au cœur du sujet (chef d'État ou de gouvernement, ministre, dirigeant de parti ou d'organisation internationale). null s'il n'y en a pas une seule clairement centrale. Jamais une victime, un particulier, un mineur, ni une personne poursuivie dans une affaire de droit commun.

Réponds uniquement avec un tableau JSON, un objet par sujet, dans le même ordre :
[{"id": 1, "titre": "...", "texte": "...", "politique": true, "ecarter": false, "pays": ["FRA"], "personne": null, "doublon_de": null}]"""

RACCOURCIR = """Raccourcis chaque titre à 60 caractères maximum, espaces compris, style agence de presse.
Règle absolue : garde le SUJET (qui agit) et le verbe. Pour gagner de la place, retire d'abord les compléments secondaires, remplace un pays par sa capitale ou un nom long par un nom court. Le titre ne doit jamais commencer par un verbe. N'ajoute rien, pas de point final ni d'exclamation.
Réponds uniquement avec un tableau JSON de chaînes, dans le même ordre."""


def _appeler(systeme: str, contenu: str, modele: str) -> str:
    import anthropic

    client = anthropic.Anthropic()  # lit ANTHROPIC_API_KEY
    rep = client.messages.create(
        model=modele,
        max_tokens=2000,
        system=systeme,
        messages=[{"role": "user", "content": contenu}],
    )
    return rep.content[0].text


def _json(brut: str):
    m = re.search(r"\[.*\]", brut, re.S)
    if not m:
        raise ValueError("réponse illisible du modèle")
    return json.loads(m.group(0))


def _dossier(sujets: list[dict]) -> str:
    blocs = []
    for i, s in enumerate(sujets, 1):
        lignes = [f"### Sujet {i}"]
        for a in s["articles"][:6]:
            lignes.append(f"- [{a.source}] {a.titre} — {a.resume[:350]}")
        blocs.append("\n".join(lignes))
    return "\n\n".join(blocs)


VERBES = re.compile(
    r"^(arr[êe]te|perturbe|brouille|annonce|frappe|bombarde|attaque|accuse|lance|signe|rejette|menace|"
    r"condamne|propose|suspend|reprend|ouvre|ferme|vote|adopte|refuse|appelle|exige|d[ée]nonce|confirme|"
    r"d[ée]ment|nie|revendique|limite|restreint|interdit|autorise|sanctionne|expulse|rappelle|reconna[iî]t|"
    r"envoie|livre|retire|d[ée]ploie|d[ée]clare|d[ée]cide|renforce|prolonge|l[èe]ve|impose|bloque|"
    r"arr[êe]tent|annoncent|frappent|revendiquent|accusent|menacent|d[ée]noncent|refusent|exigent)\b",
    re.I)


def _sans_sujet(titre: str) -> bool:
    """Titre qui commence par un verbe conjugue, sans sujet (ex. 'Arrete le redacteur...')."""
    t = titre.strip()
    if ":" in t.split(" ")[0]:
        return False
    return bool(VERBES.match(t))


REPARER = """Chaque titre ci-dessous n'a pas de sujet : on ne sait pas qui agit. Réécris-le en style agence de presse, 60 caractères maximum, en nommant le sujet (pays, capitale, institution, dirigeant) d'après le texte fourni. N'ajoute aucune information absente du texte.
Réponds uniquement avec un tableau JSON de chaînes, dans le même ordre."""


def _par_claude(sujets: list[dict], modele: str) -> list[dict]:
    breves = _json(_appeler(CONSIGNES, _dossier(sujets), modele))
    trop_longs = [b for b in breves if len(b.get("titre", "")) > TITRE_MAX + 5]
    if trop_longs:
        try:
            courts = _json(_appeler(RACCOURCIR, json.dumps([b["titre"] for b in trop_longs], ensure_ascii=False), modele))
            for b, c in zip(trop_longs, courts):
                if isinstance(c, str) and 0 < len(c) < len(b["titre"]) and not _sans_sujet(c):
                    b["titre"] = c
        except Exception as err:
            print(f"  ! raccourcissement des titres impossible ({str(err)[:60]})")
    sans_sujet = [b for b in breves if _sans_sujet(b.get("titre", ""))]
    if sans_sujet:
        try:
            dem = [{"titre": b["titre"], "texte": b.get("texte", "")} for b in sans_sujet]
            neufs = _json(_appeler(REPARER, json.dumps(dem, ensure_ascii=False), modele))
            for b, c in zip(sans_sujet, neufs):
                if isinstance(c, str) and c.strip() and not _sans_sujet(c):
                    b["titre"] = c.strip()
        except Exception as err:
            print(f"  ! réparation des titres impossible ({str(err)[:60]})")
    return breves


def _extractif(sujets: list[dict]) -> list[dict]:
    """Secours sans IA : titre et debut du resume de la premiere source. Test uniquement."""
    out = []
    for i, s in enumerate(sujets, 1):
        a = max(s["articles"], key=lambda x: len(x.resume))
        phrases = re.split(r"(?<=[.!?])\s+", a.resume)
        texte = " ".join(phrases[:2])[:240] or a.titre
        titre = a.titre if len(a.titre) <= 60 else a.titre[:60].rsplit(" ", 1)[0] + "…"
        out.append({"id": i, "titre": titre, "texte": texte, "politique": True, "ecarter": False})
    return out


def rediger(sujets: list[dict], cfg: dict) -> tuple[list[dict], str]:
    """Ajoute 'titre' et 'texte' a chaque sujet. Renvoie (sujets gardes, mode utilise)."""
    mode = cfg.get("fournisseur", "anthropic")
    if mode == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        print("  ! ANTHROPIC_API_KEY absente : rédaction en mode extractif (test seulement)")
        mode = "extractif"
    politique_seulement = cfg.get("politique_seulement", True)
    breves = _par_claude(sujets, cfg["modele"]) if mode == "anthropic" else _extractif(sujets)
    gardes = []
    for s, b in zip(sujets, breves):
        nom = s["articles"][0].titre[:70]
        if b.get("doublon_de"):
            print(f"  - écarté (doublon d'un autre sujet) : {nom}")
            continue
        if _sans_sujet(b.get("titre", "")):
            print(f"  - écarté (titre sans sujet) : {b.get('titre')}")
            continue
        if b.get("ecarter") or not b.get("texte"):
            print(f"  - écarté (sources incohérentes) : {nom}")
            continue
        if politique_seulement and b.get("politique") is False:
            print(f"  - écarté (hors politique) : {nom}")
            continue
        s["titre"] = b["titre"].strip().rstrip(".")
        s["texte"] = b["texte"].strip()
        iso = b.get("pays") or []
        s["iso"] = [c.strip().upper() for c in (iso if isinstance(iso, list) else [iso]) if isinstance(c, str) and c.strip()]
        s["personne"] = b.get("personne") if isinstance(b.get("personne"), str) and b.get("personne").strip() else None
        gardes.append(s)
    return gardes, mode
