"""Redaction du texte lu a l'antenne.

Regle d'or : on ne dit que ce que plusieurs sources affirment, avec nos propres mots.
"""
from __future__ import annotations

import json
import os
import re

TITRE_MAX = 60

CONSIGNES = """Tu es secrétaire de rédaction d'un flash d'information vidéo en français, consacré à la politique, à la géopolitique, à la diplomatie et aux grands faits d'ordre public. Le texte est lu à voix haute par une voix de synthèse, comme un journal radio.

Pour chaque sujet, tu reçois les titres et résumés de plusieurs médias. Rédige une brève.

Faits :
- N'écris QUE des faits présents dans au moins deux des sources fournies. N'ajoute aucune information, aucun contexte de ta propre connaissance.
- Reformule entièrement : ne reprends aucune phrase ni tournure d'une source.
- Ton neutre et factuel. Aucun adjectif de jugement, aucun commentaire, aucune supposition.
- Si les sources emploient le conditionnel ou citent quelqu'un, garde cette prudence ("selon...", "d'après...").
- Sur les conflits et la politique, emploie le vocabulaire le plus neutre des sources et attribue les affirmations de chaque camp ("selon Kiev", "d'après l'armée israélienne").

Faits divers :
- Jamais le nom d'un suspect, d'une victime ou d'un mineur. Présomption d'innocence : « un suspect », « un homme interpellé », « selon le parquet ».
- Aucun détail macabre ni spéculation sur les motifs. Situe le lieu (ville) et donne le bilan officiel.

Chiffres et victimes :
- Si les sources donnent des morts ou des blessés, mentionne-les toujours.
- Si les chiffres divergent, retiens la valeur la plus basse avec "au moins" (12 et 14 morts : "au moins douze morts").
- Ne change jamais l'ordre de grandeur : 300 blessés, c'est "des centaines de blessés", jamais "des dizaines".

Texte lu à l'antenne ("texte") :
- 2 ou 3 phrases courtes, 280 caractères maximum. Écris comme un présentateur de journal radio : direct, vivant, naturel, jamais familier.
- Quand c'est naturel, commence par situer le lieu en quelques mots (« En Ukraine, », « À Washington, », « Au Liban, »), puis donne tout de suite le fait principal.
- Une idée par phrase, 20 mots au plus. Sujet, verbe, complément. Pas d'incise longue, pas de parenthèses, pas de guillemets, pas de barre oblique, pas de deux-points.
- Écris ce qui se prononce : « pour cent » et non « % », « euros » et non « € », « dollars » et non « $ », « Monsieur » et non « M. ». Garde les nombres en chiffres (80, 6,1, 1 200).
- Un sigle n'est permis que s'il se prononce couramment (Otan, ONU, UE, FMI, OMS). Sinon, écris le nom en clair.
- Ne répète pas le titre mot pour mot : il est déjà affiché à l'écran.
- Mets une virgule là où un présentateur respire.
- Varie les tournures d'une brève à l'autre (pas deux brèves qui commencent de la même façon).

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

Suite d'une info :
- Un sujet marqué « Suite de : ... » a déjà fait l'objet d'une vidéo, dont le titre est donné. Les articles fournis sont seulement les nouveaux.
- Titre et texte portent UNIQUEMENT sur le fait nouveau (réaction, riposte, bilan revu, décision, vote...). Ne raconte pas à nouveau ce que disait la vidéo précédente ; au besoin une demi-phrase de rappel suffit.
- Le titre doit être nettement différent du titre précédent.
- Si les nouveaux articles n'apportent aucun fait nouveau par rapport au titre précédent, mets "ecarter": true.

Tri :
- "politique" : true si le cœur du sujet est la politique intérieure ou internationale, la géopolitique, la diplomatie, un conflit armé, des sanctions, une élection, une décision de justice visant des responsables politiques, une manifestation, OU un fait divers marquant d'ordre public ou de sécurité (émeutes, pillages, violences urbaines, attaque, fusillade, attentat, prise d'otages, grande opération de police). false pour l'économie, la science, la culture, le sport, les catastrophes naturelles, les accidents et les faits divers ordinaires (cambriolage, accident de la route, drame familial…), sauf si leur cœur est une décision ou un affrontement politique.
- "ecarter" : true si les articles d'un sujet ne parlent pas du même fait, ou se contredisent sur l'essentiel.
- "doublon_de" : si ce sujet raconte le même fait ou la même affaire qu'un sujet précédent de la liste (même événement vu sous un autre angle), mets l'id de ce sujet précédent ; sinon null.

Illustration :
- "pays" : liste de codes ISO 3166-1 alpha-3 des pays concernés, le principal en premier (ex. ["UKR", "RUS"]). "UE" si le sujet concerne l'Union européenne en tant que telle, "MONDE" s'il est mondial.
- "personne" : nom complet, tel que sur Wikipédia, de LA personnalité publique au cœur du sujet (chef d'État ou de gouvernement, ministre, dirigeant de parti ou d'organisation internationale). null s'il n'y en a pas une seule clairement centrale. Jamais une victime, un particulier, un mineur, ni une personne poursuivie dans une affaire de droit commun.

Habillage :
- "chiffre" : le chiffre le plus marquant du texte, écrit exactement comme dans "texte", avec une légende de 3 ou 4 mots : {"nombre": "80", "legende": "drones abattus"}. Il doit être un fait, pas une date ni une année. null s'il n'y en a pas.
- "lien" : si l'info décrit l'action d'un pays envers un autre (frappe, sanctions, menace, aide, expulsion, attaque), {"de": "USA", "vers": "IRN", "type": "action"} ; si c'est un échange entre deux pays (rencontre, accord, négociation), "type": "echange". Les deux codes doivent figurer dans "pays". null sinon.
- "hashtags" : 2 hashtags thématiques en français, sans accent ni espace, qui aident à trouver la vidéo, par exemple ["#Sanctions", "#Diplomatie"]. Jamais de nom de pays ni de personne : ils sont ajoutés automatiquement.

Réponds uniquement avec un tableau JSON valide, un objet par sujet, dans le même ordre. Dans les textes, n'utilise jamais de guillemets droits (") : uniquement des guillemets français « ».
[{"id": 1, "titre": "...", "texte": "...", "politique": true, "ecarter": false, "pays": ["FRA"], "personne": null, "doublon_de": null, "chiffre": null, "lien": null, "hashtags": ["#Diplomatie", "#Elections"]}]"""

def _section(entete: str) -> str:
    """Un paragraphe des consignes, pour les reutiliser dans le recapitulatif de la semaine."""
    return next(b for b in CONSIGNES.split("\n\n") if b.startswith(entete))


SEMAINE = "\n\n".join([
    "Tu prépares l'édition « la semaine dans le monde » d'un flash d'information vidéo en français "
    "(politique, géopolitique, diplomatie, grands faits d'ordre public), lu à voix haute comme un journal radio.",
    "Pour chaque sujet, tu reçois une ou plusieurs brèves déjà publiées cette semaine, vérifiées et datées. "
    "Rédige une brève de synthèse qui raconte où en est l'affaire en fin de semaine.",
    "Faits :\n- N'utilise QUE les faits des brèves fournies. N'ajoute rien de ta propre connaissance.\n"
    "- Si plusieurs brèves se suivent, dis l'évolution (« lundi... ; jeudi... ») sans inventer de lien de cause.\n"
    "- Ton neutre et factuel, attribue les affirmations de chaque camp.",
    _section("Chiffres et victimes"), _section("Texte lu"), _section("Titre"), _section("Illustration"),
    _section("Habillage"),
    "Réponds uniquement avec un tableau JSON valide, un objet par sujet, dans le même ordre. Dans les textes, "
    "n'utilise jamais de guillemets droits : uniquement des guillemets français « ».\n"
    '[{"id": 1, "titre": "...", "texte": "...", "pays": ["FRA"], "personne": null, "chiffre": null, '
    '"lien": null, "hashtags": ["#Diplomatie", "#Elections"]}]',
])


RACCOURCIR = """Raccourcis chaque titre à 60 caractères maximum, espaces compris, style agence de presse.
Règle absolue : garde le SUJET (qui agit) et le verbe. Pour gagner de la place, retire d'abord les compléments secondaires, remplace un pays par sa capitale ou un nom long par un nom court. Le titre ne doit jamais commencer par un verbe. N'ajoute rien, pas de point final ni d'exclamation.
Réponds uniquement avec un tableau JSON de chaînes, dans le même ordre."""


MODELE_SECOURS = "claude-haiku-4-5-20251001"
avertissements: list = []  # messages a transmettre sur Telegram (ex. : modele de secours utilise)


def _appeler(systeme: str, contenu: str, modele: str) -> str:
    """Appel a Claude. Si le modele demande est refuse (nom inconnu, acces), on reessaie avec Haiku 4.5."""
    import anthropic

    client = anthropic.Anthropic(max_retries=4)  # lit ANTHROPIC_API_KEY ; nouveaux essais en cas de coupure
    for m in dict.fromkeys([modele, MODELE_SECOURS]):
        try:
            rep = client.messages.create(
                model=m,
                max_tokens=16000,  # de la marge : les modeles recents reflechissent avant de repondre
                system=systeme,
                messages=[{"role": "user", "content": contenu}],
            )
        except (anthropic.NotFoundError, anthropic.BadRequestError, anthropic.PermissionDeniedError) as err:
            print(f"  ! modèle {m} refusé ({str(err)[:160]})")
            if m == MODELE_SECOURS:
                raise
            msg = f"Le modèle {m} est refusé par l'API Claude : rédaction faite avec {MODELE_SECOURS}."
            if msg not in avertissements:
                avertissements.append(msg)
            continue
        # seuls les blocs de texte comptent (certains modeles renvoient aussi un bloc de reflexion)
        texte = "".join(getattr(b, "text", "") for b in rep.content if getattr(b, "type", "") == "text")
        if texte.strip():
            return texte
        raise ValueError(f"réponse vide du modèle {m} (arrêt : {rep.stop_reason})")
    raise RuntimeError("aucun modèle Claude disponible")


def _json(brut: str):
    m = re.search(r"\[.*\]", brut, re.S)
    if not m:
        raise ValueError("réponse illisible du modèle")
    try:
        return json.loads(m.group(0))
    except ValueError:  # JSON mal forme (ex. : guillemets droits dans un texte) : on le repare
        from json_repair import loads as reparer
        donnees = reparer(m.group(0))
        if not isinstance(donnees, list) or not donnees:
            raise ValueError("réponse JSON illisible, même après réparation")
        print("  ! JSON du modèle réparé automatiquement")
        return donnees


def _dossier(sujets: list[dict]) -> str:
    blocs = []
    for i, s in enumerate(sujets, 1):
        lignes = [f"### Sujet {i}"]
        if s.get("suite") is not None:
            lignes.append(f"Suite de : « {s['suite']} »")
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
    try:
        breves = _json(_appeler(CONSIGNES, _dossier(sujets), modele))
    except ValueError as err:  # reponse inexploitable : un essai avec le modele de secours
        if modele == MODELE_SECOURS:
            raise
        print(f"  ! réponse inexploitable de {modele} ({err}) : essai avec {MODELE_SECOURS}")
        avertissements.append(f"Réponse inexploitable de {modele} : rédaction faite avec {MODELE_SECOURS}.")
        breves = _json(_appeler(CONSIGNES, _dossier(sujets), MODELE_SECOURS))
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


ESPACES = re.compile(r"[\u00a0\u202f\u2009]")


def valider_chiffre(c, texte: str):
    """Le chiffre affiche doit figurer tel quel dans le texte lu (aucune invention)."""
    if not isinstance(c, dict):
        return None
    nombre = ESPACES.sub(" ", str(c.get("nombre") or "")).strip()
    legende = re.sub(r"\s+", " ", str(c.get("legende") or "")).strip()
    if not re.match(r"^\d", nombre) or not legende or len(legende) > 30 or len(nombre) > 16:
        return None
    if nombre not in ESPACES.sub(" ", texte):
        return None
    if re.fullmatch(r"(19|20)\d\d", nombre):  # une annee n'est pas un chiffre marquant
        return None
    return {"nombre": nombre, "legende": legende}


def valider_lien(l, codes: list):
    if not isinstance(l, dict):
        return None
    de, vers = str(l.get("de") or "").upper(), str(l.get("vers") or "").upper()
    if de == vers or de not in codes or vers not in codes or "MONDE" in (de, vers):
        return None
    return {"de": de, "vers": vers, "type": "echange" if l.get("type") == "echange" else "action"}


def valider_hashtags(h) -> list:
    if not isinstance(h, list):
        return []
    propres = []
    for x in h[:3]:
        x = str(x).strip()
        x = x if x.startswith("#") else "#" + x
        if re.fullmatch(r"#[A-Za-z0-9_]{3,30}", x):
            propres.append(x)
    return propres


def rediger_semaine(groupes: list, cfg: dict) -> list:
    """Recapitulatif de la semaine. groupes : listes de breves archivees (une liste par affaire)."""
    blocs = []
    for i, g in enumerate(groupes, 1):
        lignes = [f"### Sujet {i}"]
        for b in sorted(g, key=lambda x: x["date"]):
            lignes.append(f"- [{b['jour']}] {b['titre']} — {b['texte']}")
        blocs.append("\n".join(lignes))
    breves = _json(_appeler(SEMAINE, "\n\n".join(blocs), cfg["modele"]))
    sujets = []
    for g, b in zip(groupes, breves):
        if not b.get("texte") or not b.get("titre") or _sans_sujet(b["titre"]):
            continue
        dernier = max(g, key=lambda x: x["date"])
        iso = b.get("pays") or dernier.get("iso") or []
        iso = [c.strip().upper() for c in (iso if isinstance(iso, list) else [iso]) if isinstance(c, str)]
        texte = b["texte"].strip()
        sources = sorted({src for x in g for src in x.get("sources", [])})
        sujets.append({
            "titre": b["titre"].strip().rstrip("."), "texte": texte, "iso": iso,
            "pays": dernier.get("pays", ""), "sources": sources,
            "liens": {k: v for x in g for k, v in (x.get("liens") or {}).items()},
            "personne": b.get("personne") if isinstance(b.get("personne"), str) else None,
            "chiffre": valider_chiffre(b.get("chiffre"), texte), "lien": valider_lien(b.get("lien"), iso),
            "hashtags": valider_hashtags(b.get("hashtags")), "score": max(x.get("score", 0) for x in g),
            "sig": [w for x in g for w in x.get("sig", [])][:12], "articles": [],
        })
    return sujets


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
        s["chiffre"] = valider_chiffre(b.get("chiffre"), s["texte"])
        s["lien"] = valider_lien(b.get("lien"), s["iso"])
        s["hashtags"] = valider_hashtags(b.get("hashtags"))
        gardes.append(s)
    return gardes, mode
