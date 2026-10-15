"""Detection du pays concerne par une info, pour choisir une photo d'illustration."""
from __future__ import annotations

import re

from .cluster import sans_accents

# nom affiche : (requete photo en anglais, mots-cles sans accents en minuscules)
PAYS = {
    "États-Unis": ("United States city skyline", ["etats-unis", "etats unis", "americain", "washington", "maison blanche", "new york", "californie", "texas"]),
    "France": ("Paris France", ["france", "francais", "paris", "elysee", "marseille", "lyon"]),
    "Royaume-Uni": ("London United Kingdom", ["royaume-uni", "royaume uni", "britannique", "londres", "angleterre", "ecosse"]),
    "Allemagne": ("Berlin Germany", ["allemagne", "allemand", "berlin"]),
    "Italie": ("Rome Italy", ["italie", "italien", "rome", "milan"]),
    "Espagne": ("Madrid Spain", ["espagne", "espagnol", "madrid", "barcelone"]),
    "Portugal": ("Lisbon Portugal", ["portugal", "portugais", "lisbonne"]),
    "Belgique": ("Brussels Belgium", ["belgique", "belge", "bruxelles"]),
    "Suisse": ("Switzerland Alps city", ["suisse", "geneve", "zurich", "berne"]),
    "Pays-Bas": ("Amsterdam Netherlands", ["pays-bas", "neerlandais", "amsterdam", "la haye"]),
    "Pologne": ("Warsaw Poland", ["pologne", "polonais", "varsovie"]),
    "Grèce": ("Athens Greece", ["grece", "grec", "athenes"]),
    "Albanie": ("Albania landscape", ["albanie", "albanais", "tirana"]),
    "Serbie": ("Belgrade Serbia", ["serbie", "serbe", "belgrade"]),
    "Hongrie": ("Budapest Hungary", ["hongrie", "hongrois", "budapest"]),
    "Suède": ("Stockholm Sweden", ["suede", "suedois", "stockholm"]),
    "Norvège": ("Norway fjord city", ["norvege", "norvegien", "oslo"]),
    "Ukraine": ("Kyiv Ukraine", ["ukraine", "ukrainien", "kiev", "kyiv"]),
    "Russie": ("Moscow Russia", ["russie", "russe", "moscou", "kremlin"]),
    "Turquie": ("Istanbul Turkey", ["turquie", "turc", "ankara", "istanbul"]),
    "Israël": ("Jerusalem city", ["israel", "israelien", "tel-aviv", "tel aviv", "jerusalem"]),
    "Palestine": ("Middle East old city", ["palestine", "palestinien", "gaza", "cisjordanie"]),
    "Liban": ("Beirut Lebanon", ["liban", "libanais", "beyrouth"]),
    "Syrie": ("Damascus Syria", ["syrie", "syrien", "damas"]),
    "Iran": ("Tehran Iran", ["iran", "iranien", "teheran"]),
    "Irak": ("Baghdad Iraq", ["irak", "irakien", "bagdad"]),
    "Arabie saoudite": ("Riyadh Saudi Arabia", ["arabie saoudite", "saoudien", "riyad"]),
    "Émirats arabes unis": ("Dubai skyline", ["emirats", "dubai", "abou dhabi"]),
    "Qatar": ("Doha Qatar", ["qatar", "doha"]),
    "Égypte": ("Cairo Egypt", ["egypte", "egyptien", "le caire"]),
    "Maroc": ("Morocco city", ["maroc", "marocain", "rabat", "casablanca"]),
    "Algérie": ("Algiers Algeria", ["algerie", "algerien", "alger"]),
    "Tunisie": ("Tunis Tunisia", ["tunisie", "tunisien", "tunis"]),
    "Libye": ("Libya desert city", ["libye", "libyen", "tripoli"]),
    "Soudan": ("Sudan Nile", ["soudan", "soudanais", "khartoum"]),
    "Éthiopie": ("Addis Ababa Ethiopia", ["ethiopie", "ethiopien", "addis-abeba"]),
    "Kenya": ("Nairobi Kenya", ["kenya", "kenyan", "nairobi"]),
    "Nigeria": ("Lagos Nigeria", ["nigeria", "nigerian", "lagos", "abuja"]),
    "Niger": ("Sahel landscape", ["niger", "nigerien", "niamey"]),
    "Mali": ("Mali Sahel", ["mali", "malien", "bamako"]),
    "Burkina Faso": ("Sahel village", ["burkina", "burkinabe", "ouagadougou"]),
    "Sénégal": ("Dakar Senegal", ["senegal", "senegalais", "dakar"]),
    "Côte d'Ivoire": ("Abidjan Ivory Coast", ["cote d'ivoire", "ivoirien", "abidjan"]),
    "Ghana": ("Accra Ghana", ["ghana", "ghaneen", "accra"]),
    "Cameroun": ("Cameroon city", ["cameroun", "camerounais", "yaounde", "douala"]),
    "RD Congo": ("Kinshasa Congo", ["rdc", "rd congo", "congolais", "kinshasa", "goma"]),
    "Afrique du Sud": ("Cape Town South Africa", ["afrique du sud", "sud-africain", "johannesburg", "pretoria"]),
    "Chine": ("Beijing China", ["chine", "chinois", "pekin", "shanghai"]),
    "Taïwan": ("Taipei Taiwan", ["taiwan", "taiwanais", "taipei"]),
    "Japon": ("Tokyo Japan", ["japon", "japonais", "tokyo"]),
    "Corée du Sud": ("Seoul South Korea", ["coree du sud", "sud-coreen", "seoul"]),
    "Corée du Nord": ("Korea mountains", ["coree du nord", "nord-coreen", "pyongyang"]),
    "Inde": ("New Delhi India", ["inde", "indien", "new delhi", "bombay", "mumbai"]),
    "Pakistan": ("Pakistan city", ["pakistan", "pakistanais", "islamabad", "karachi"]),
    "Afghanistan": ("Afghanistan mountains", ["afghanistan", "afghan", "kaboul", "taliban"]),
    "Bangladesh": ("Dhaka Bangladesh", ["bangladesh", "dacca"]),
    "Indonésie": ("Jakarta Indonesia", ["indonesie", "indonesien", "jakarta"]),
    "Philippines": ("Manila Philippines", ["philippines", "philippin", "manille"]),
    "Vietnam": ("Hanoi Vietnam", ["vietnam", "vietnamien", "hanoi"]),
    "Thaïlande": ("Bangkok Thailand", ["thailande", "thailandais", "bangkok"]),
    "Australie": ("Sydney Australia", ["australie", "australien", "sydney", "canberra"]),
    "Canada": ("Canada city skyline", ["canada", "canadien", "ottawa", "quebec", "montreal", "toronto"]),
    "Mexique": ("Mexico City", ["mexique", "mexicain", "mexico"]),
    "Brésil": ("Brazil city", ["bresil", "bresilien", "brasilia", "rio de janeiro", "sao paulo", "amazonie"]),
    "Argentine": ("Buenos Aires Argentina", ["argentine", "argentin", "buenos aires"]),
    "Chili": ("Santiago Chile", ["chili", "chilien", "santiago"]),
    "Colombie": ("Bogota Colombia", ["colombie", "colombien", "bogota"]),
    "Venezuela": ("Caracas Venezuela", ["venezuela", "venezuelien", "caracas"]),
    "Pérou": ("Lima Peru", ["perou", "peruvien", "lima"]),
    "Panama": ("Panama City skyline", ["panama", "panameen"]),
    "Haïti": ("Caribbean coast", ["haiti", "haitien", "port-au-prince"]),
    "Cuba": ("Havana Cuba", ["cuba", "cubain", "la havane"]),
    "Union européenne": ("European Union flags", ["union europeenne", "commission europeenne", "parlement europeen", "europeen"]),
    "Nations unies": ("United Nations building", ["onu", "nations unies", "conseil de securite"]),
}

DEFAUT = ("Monde", "world map globe")


def detecter(textes: list[str]) -> tuple[str, str]:
    """Renvoie (nom du pays, requete photo) pour le pays le plus cite dans les titres."""
    brut = sans_accents(" | ".join(textes).lower())
    meilleur, score_max = None, 0
    for nom, (requete, cles) in PAYS.items():
        score = sum(len(re.findall(rf"(?<![a-z]){re.escape(c)}s?(?![a-z])", brut)) for c in cles)
        if score > score_max:
            meilleur, score_max = (nom, requete), score
    return meilleur or DEFAUT


ISO = {
    "États-Unis": "USA", "France": "FRA", "Royaume-Uni": "GBR", "Allemagne": "DEU", "Italie": "ITA",
    "Espagne": "ESP", "Portugal": "PRT", "Belgique": "BEL", "Suisse": "CHE", "Pays-Bas": "NLD",
    "Pologne": "POL", "Grèce": "GRC", "Albanie": "ALB", "Serbie": "SRB", "Hongrie": "HUN", "Suède": "SWE",
    "Norvège": "NOR", "Ukraine": "UKR", "Russie": "RUS", "Turquie": "TUR", "Israël": "ISR",
    "Palestine": "PSE", "Liban": "LBN", "Syrie": "SYR", "Iran": "IRN", "Irak": "IRQ",
    "Arabie saoudite": "SAU", "Émirats arabes unis": "ARE", "Qatar": "QAT", "Égypte": "EGY",
    "Maroc": "MAR", "Algérie": "DZA", "Tunisie": "TUN", "Libye": "LBY", "Soudan": "SDN",
    "Éthiopie": "ETH", "Kenya": "KEN", "Nigeria": "NGA", "Niger": "NER", "Mali": "MLI",
    "Burkina Faso": "BFA", "Sénégal": "SEN", "Côte d'Ivoire": "CIV", "Ghana": "GHA", "Cameroun": "CMR",
    "RD Congo": "COD", "Afrique du Sud": "ZAF", "Chine": "CHN", "Taïwan": "TWN", "Japon": "JPN",
    "Corée du Sud": "KOR", "Corée du Nord": "PRK", "Inde": "IND", "Pakistan": "PAK",
    "Afghanistan": "AFG", "Bangladesh": "BGD", "Indonésie": "IDN", "Philippines": "PHL",
    "Vietnam": "VNM", "Thaïlande": "THA", "Australie": "AUS", "Canada": "CAN", "Mexique": "MEX",
    "Brésil": "BRA", "Argentine": "ARG", "Chili": "CHL", "Colombie": "COL", "Venezuela": "VEN",
    "Pérou": "PER", "Panama": "PAN", "Haïti": "HTI", "Cuba": "CUB",
    "Union européenne": "UE", "Nations unies": "MONDE", "Monde": "MONDE",
}
NOM_ISO = {v: k for k, v in ISO.items() if v not in ("MONDE",)}


def iso_de(nom: str) -> str | None:
    return ISO.get(nom)


def nom_iso(code: str) -> str:
    """Nom court en francais pour l'affichage (ex. 'Chine' plutot que le nom officiel)."""
    if code in NOM_ISO:
        return NOM_ISO[code]
    if code == "MONDE":
        return "Monde"
    from .carte import nom
    return nom(code)
