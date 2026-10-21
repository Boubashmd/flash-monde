"""Hashtags de chaque video : pays, personnalites et themes de l'edition.

Ils aident TikTok et YouTube a montrer la video aux personnes qui suivent ces pays
(diasporas, passionnes de geopolitique...). On reste sous les plafonds des plateformes :
au-dela de 15 hashtags, YouTube les ignore tous.
"""
from __future__ import annotations

import re
import unicodedata

# formes les plus utilisees (sans accent : c'est ainsi qu'on les tape le plus souvent)
PAYS = {
    "USA": ["#USA", "#EtatsUnis"], "GBR": ["#RoyaumeUni", "#UK"], "RUS": ["#Russie"], "UKR": ["#Ukraine"],
    "ISR": ["#Israel"], "PSE": ["#Palestine"], "LBN": ["#Liban"], "SYR": ["#Syrie"], "IRN": ["#Iran"],
    "IRQ": ["#Irak"], "SAU": ["#ArabieSaoudite"], "ARE": ["#Emirats"], "QAT": ["#Qatar"], "TUR": ["#Turquie"],
    "EGY": ["#Egypte"], "CHN": ["#Chine"], "TWN": ["#Taiwan"], "JPN": ["#Japon"], "PRK": ["#CoreeDuNord"],
    "KOR": ["#CoreeDuSud"], "IND": ["#Inde"], "PAK": ["#Pakistan"], "AFG": ["#Afghanistan"],
    "FRA": ["#France"], "DEU": ["#Allemagne"], "ITA": ["#Italie"], "ESP": ["#Espagne"], "POL": ["#Pologne"],
    "HUN": ["#Hongrie"], "BEL": ["#Belgique"], "CHE": ["#Suisse"], "UE": ["#UnionEuropeenne", "#UE"],
    "COD": ["#RDC"], "COG": ["#Congo"], "CIV": ["#CoteDIvoire"], "ZAF": ["#AfriqueDuSud"], "NER": ["#Niger"],
    "MLI": ["#Mali"], "BFA": ["#BurkinaFaso"], "SEN": ["#Senegal"], "DZA": ["#Algerie"], "MAR": ["#Maroc"],
    "TUN": ["#Tunisie"], "LBY": ["#Libye"], "SDN": ["#Soudan"], "ETH": ["#Ethiopie"], "NGA": ["#Nigeria"],
    "CMR": ["#Cameroun"], "TCD": ["#Tchad"], "VEN": ["#Venezuela"], "BRA": ["#Bresil"], "MEX": ["#Mexique"],
    "CAN": ["#Canada"], "ARG": ["#Argentine"], "CUB": ["#Cuba"], "HTI": ["#Haiti"], "ARM": ["#Armenie"],
    "AZE": ["#Azerbaidjan"], "GEO": ["#Georgie"], "BLR": ["#Bielorussie"], "SRB": ["#Serbie"],
}
GENERAUX = ["#actualite", "#info", "#FlashMonde"]


def sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn")


def _forme(mots: str) -> str:
    """« Afrique du Sud » -> « #AfriqueDuSud »."""
    propre = re.sub(r"[^A-Za-z0-9 ]", " ", sans_accents(mots))
    return "#" + "".join(m[:1].upper() + m[1:] for m in propre.split())


def pour_pays(code: str, nom: str) -> list:
    if code in PAYS:
        return PAYS[code]
    return [_forme(nom)] if nom and code not in ("MONDE",) else []


def pour_personne(nom: str) -> str:
    """Le nom de famille, forme la plus cherchee (#Trump, #Macron, #Zelensky)."""
    morceaux = [m for m in re.split(r"\s+", nom.strip()) if m]
    return _forme(morceaux[-1]) if morceaux else ""


def construire(sujets: list, nom_pays, maximum: int = 12) -> list:
    """sujets : dicts avec 'iso' (codes), 'personne', 'hashtags'. nom_pays(code) -> nom affiche."""
    pays, personnes, themes = [], [], []
    for s in sujets:
        for code in (s.get("iso") or [])[:3]:
            pays += pour_pays(code, nom_pays(code))
        if s.get("personne"):
            personnes.append(pour_personne(s["personne"]))
        themes += s.get("hashtags") or []
    # d'abord le pays principal de chaque info, puis le reste
    principaux = [t for s in sujets for t in pour_pays((s.get("iso") or ["MONDE"])[0],
                                                         nom_pays((s.get("iso") or ["MONDE"])[0]))[:1]]
    vus, specifiques = set(), []
    for t in principaux + personnes + pays + themes:
        cle = sans_accents(t).lower()
        if len(t) > 2 and cle not in vus:
            vus.add(cle)
            specifiques.append(t)
    generaux = [t for t in GENERAUX if sans_accents(t).lower() not in vus]
    return specifiques[:max(0, maximum - len(generaux))] + generaux
