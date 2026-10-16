"""Flash Monde - fabrique une edition video des gros titres mondiaux.

Utilisation :
    python -m src.main                      fabrique l'edition du moment
    python -m src.main --semaine            edition « la semaine dans le monde » (le dimanche)
    python -m src.main --bilan              bilan des vues YouTube de la semaine, sur Telegram
    python -m src.main --demo               edition de test avec des articles fictifs
    python -m src.main --verifier-sources   teste chaque flux RSS
Options : --voix silence|edge|elevenlabs   --sans-telegram   --sans-validation   --si-pas-faite
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import carte, cluster, collect, film, hashtags, pays, portrait, redaction, telegram, voix, youtube

RACINE = Path(__file__).resolve().parent.parent
F_VUS = RACINE / "data" / "vus.json"
F_ARCHIVES = RACINE / "data" / "archives.json"
F_STATS = RACINE / "data" / "stats.json"
F_DERNIERE = RACINE / "data" / "derniere_edition.json"
DELAI_DOUBLON_H = 2   # une edition planifiee est sautee si une autre a deja tourne dans ces 2 h
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


def charger_env() -> None:
    f = RACINE / ".env"
    if not f.exists():
        return
    for ligne in f.read_text(encoding="utf-8").splitlines():
        if "=" in ligne and not ligne.strip().startswith("#"):
            cle, val = ligne.split("=", 1)
            if val.strip():
                os.environ.setdefault(cle.strip(), val.strip().strip('"').strip("'"))


def date_longue(d: datetime) -> str:
    return f"{d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]} {d.year}"


def date_info(sujet: dict, fuseau: ZoneInfo) -> str:
    """Date de l'info : celle de l'article le plus recent du sujet (affichee, jamais lue)."""
    dates = []
    for a in sujet.get("articles", []):
        try:
            dates.append(datetime.fromisoformat(a.date))
        except (TypeError, ValueError):
            pass
    d = max(dates).astimezone(fuseau) if dates else datetime.now(fuseau)
    return date_longue(d)


def articles_demo() -> list:
    brut = json.loads((RACINE / "demo" / "articles.json").read_text(encoding="utf-8"))
    maintenant = datetime.now(timezone.utc)
    return [
        collect.Article(a["source"], a["titre"], a["resume"], a.get("lien", ""),
                        (maintenant - timedelta(minutes=20 * i)).isoformat())
        for i, a in enumerate(brut)
    ]


def verifier_sources(cfg: dict) -> None:
    print("Test des flux RSS :")
    ok = 0
    for s in cfg["sources"]:
        try:
            n = len(collect.lire_flux(s["nom"], s["url"]))
            print(f"  OK   {s['nom']:<14} {n} articles")
            ok += 1
        except Exception as err:
            print(f"  KO   {s['nom']:<14} {str(err)[:90]}")
    print(f"{ok}/{len(cfg['sources'])} sources joignables. Mets un # devant celles en KO dans config.yaml.")


def lire_json(f: Path) -> list:
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    except ValueError:
        return []


def ecrire_json(f: Path, donnees) -> None:
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")


def lien_journal() -> str:
    if os.getenv("GITHUB_RUN_ID"):
        return (f"\nJournal : {os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/"
                f"{os.getenv('GITHUB_REPOSITORY')}/actions/runs/{os.getenv('GITHUB_RUN_ID')}")
    return ""


def alerter(texte: str) -> None:
    """Previent sur Telegram (et marque l'alerte pour que GitHub n'en envoie pas une seconde)."""
    try:
        if telegram.configure():
            telegram.message("⚠️ Flash Monde\n" + texte + lien_journal())
            (RACINE / "sorties").mkdir(exist_ok=True)
            (RACINE / "sorties" / ".alerte").write_text("envoyée", encoding="utf-8")
    except Exception as err:
        print(f"  ! alerte Telegram impossible : {err}")


class Edition:
    def __init__(self, args, cfg: dict):
        self.args, self.cfg = args, cfg
        self.nom = cfg["chaine"]["nom"]
        self.fuseau = ZoneInfo(cfg["chaine"]["fuseau"])
        self.ici = datetime.now(self.fuseau)
        self.date_fr = f"{JOURS[self.ici.weekday()]} {self.ici.day}{'er' if self.ici.day == 1 else ''} " \
                       f"{MOIS[self.ici.month - 1]}"
        self.heure = f"{self.ici:%H}h{self.ici:%M}"
        suffixe = ("_semaine" if args.semaine else "") + ("_demo" if args.demo else "")
        self.dossier = RACINE / "sorties" / f"{self.ici:%Y-%m-%d_%Hh%M}{suffixe}"
        self.travail = self.dossier / "travail"
        self.etape = "démarrage"
        self.telegram = telegram.configure() and not args.sans_telegram and not args.demo
        self.vus = []

    def info(self, texte: str) -> None:
        print("  " + texte)
        if self.telegram:
            try:
                telegram.message(texte)
            except Exception:
                pass

    def nettoyer(self) -> None:
        for d in (self.travail, self.dossier):
            if d.exists() and not any(d.iterdir()):
                d.rmdir()

    # ------------------------------------------------------------ sujets du jour
    def sujets_du_jour(self) -> tuple:
        cfg, args, ed = self.cfg, self.args, self.cfg["edition"]
        self.etape = "collecte des articles"
        print("1/6 Collecte")
        if args.demo:
            articles = articles_demo()
        else:
            fenetre = ed["fenetre_heures"]
            if self.ici.hour < 12:
                fenetre = ed.get("fenetre_matin_heures", fenetre)
            print(f"  articles des {fenetre} dernières heures")
            articles, etat = collect.collecter(cfg["sources"], fenetre, ed["exclure"])
            for e in etat:
                print(f"  {'OK' if e['ok'] else 'KO'}  {e['nom']:<14} {e['detail']}")
            ko = [e["nom"] for e in etat if not e["ok"]]
            if len(ko) >= 3:
                self.info(f"ℹ️ {len(ko)} sources injoignables : {', '.join(ko)}")
        print(f"  {len(articles)} articles récents")

        self.etape = "tri des sujets"
        print("2/6 Tri et recoupement")
        limite = (datetime.now(timezone.utc) - timedelta(hours=ed["oubli_heures"])).isoformat()
        self.vus = [v for v in lire_json(F_VUS) if v["date"] >= limite]
        sujets = cluster.classer(cluster.regrouper(articles), ed["min_sources"], [] if args.demo else self.vus)
        zones = set(ed.get("zones_prioritaires", []))
        themes = [cluster.sans_accents(t.lower()) for t in ed.get("themes_prioritaires", [])]
        for s in sujets:
            titres = [a.titre for a in s["articles"]]
            s["pays"], s["requete"] = pays.detecter(titres)
            brut = cluster.sans_accents(" ".join(titres + [a.resume for a in s["articles"]]).lower())
            s["prioritaire"] = s["pays"] in zones
            s["score"] += (25 if s["prioritaire"] else 0) + (8 if any(t in brut for t in themes) else 0)
        if ed.get("zones_strictes"):
            sujets = [s for s in sujets if s["prioritaire"]]
        sujets.sort(key=lambda s: -s["score"])
        print(f"  {len(sujets)} sujets recoupés par au moins {ed['min_sources']} sources")
        if not sujets:
            self.info(f"{self.nom} : aucune info suffisamment recoupée ({self.heure}). Pas de vidéo.")
            return [], ""

        self.etape = "rédaction"
        print("3/6 Rédaction")
        sujets, mode = redaction.rediger(sujets[: ed["nb_titres"] + 5], cfg["redaction"])
        sujets = sujets[: ed["nb_titres"]]
        for s in sujets:
            s["liens"] = {src: next((a.lien for a in s["articles"] if a.source == src), "") for src in s["sources"]}
            s["date_affichee"] = date_info(s, self.fuseau)
        return sujets, mode

    # ------------------------------------------------------------ sujets de la semaine
    def sujets_de_la_semaine(self) -> tuple:
        self.etape = "choix des sujets de la semaine"
        print("1/6 Sujets de la semaine")
        maintenant = datetime.now(timezone.utc)
        archives = [a for a in lire_json(F_ARCHIVES) if not a.get("semaine")
                    and a.get("date", "") >= (maintenant - timedelta(days=7)).isoformat()]
        if len(archives) < 3:
            self.info(f"{self.nom} : pas assez d'infos archivées cette semaine pour le récapitulatif.")
            return [], ""
        for a in archives:
            d = datetime.fromisoformat(a["date"]).astimezone(self.fuseau)
            a["jour"] = f"{JOURS[d.weekday()]} {d.day}"
            a["_jetons"] = cluster.jetons(a["titre"] + " " + a["texte"])
        groupes = []  # une affaire = meme pays principal et vocabulaire proche
        for a in sorted(archives, key=lambda x: x["date"]):
            for g in groupes:
                b = g[-1]
                inter = len(a["_jetons"] & b["_jetons"]) / (len(a["_jetons"] | b["_jetons"]) or 1)
                if (a.get("iso") or ["?"])[0] == (b.get("iso") or ["!"])[0] and inter >= 0.18:
                    g.append(a)
                    break
            else:
                groupes.append([a])
        groupes.sort(key=lambda g: -(max(x.get("score", 0) for x in g) + 6 * (len(g) - 1)))
        choisis, par_pays = [], {}
        for g in groupes:
            p = (g[-1].get("iso") or ["MONDE"])[0]
            if par_pays.get(p, 0) < 2:
                choisis.append(g)
                par_pays[p] = par_pays.get(p, 0) + 1
            if len(choisis) == self.cfg.get("semaine", {}).get("nb_titres", 5):
                break
        for g in choisis:
            for a in g:
                a.pop("_jetons", None)
        self.etape = "rédaction de la semaine"
        print("3/6 Rédaction")
        sujets = redaction.rediger_semaine(choisis, self.cfg["redaction"])
        debut = (self.ici - timedelta(days=6))
        periode = (f"Semaine du {debut.day} au {self.ici.day} {MOIS[self.ici.month - 1]}"
                   if debut.month == self.ici.month else
                   f"Semaine du {debut.day} {MOIS[debut.month - 1]} au {self.ici.day} {MOIS[self.ici.month - 1]}")
        for s in sujets:
            s["date_affichee"] = periode
        return sujets, "anthropic"

    # ------------------------------------------------------------ fabrication et livraison
    def produire(self, sujets: list, mode: str) -> int:
        cfg, args = self.cfg, self.args
        self.travail.mkdir(parents=True, exist_ok=True)
        if not sujets:
            if mode:  # des sujets existaient, mais la redaction les a tous ecartes
                self.info(f"{self.nom} ({self.heure}) : pas de nouvelle info à publier "
                          "(déjà traitées, hors politique ou doublons). Pas de vidéo.")
            self.nettoyer()
            return 0
        for s in sujets:
            print(f"  - [{s.get('pays')}] {s['titre']}  ({', '.join(s['sources'])})")

        def memoriser(liste):
            if args.demo or not liste or args.semaine:
                return
            self.vus.extend({"sig": s["sig"], "date": datetime.now(timezone.utc).isoformat(), "titre": s["titre"]}
                            for s in liste)
            ecrire_json(F_VUS, self.vus)

        # validation sur Telegram, avant de depenser la voix
        v_val = cfg.get("validation", {})
        if self.telegram and v_val.get("active", True) and not args.sans_validation:
            self.etape = "validation Telegram"
            delai = int(v_val.get("delai_minutes", 30))
            print(f"  validation Telegram : en attente de ta réponse ({delai} min maximum)")
            entete = f"{self.nom} · {'récapitulatif de la semaine' if args.semaine else self.date_fr} · {self.heure}"
            choix = telegram.valider(sujets, entete + "\nSujets proposés :", delai)
            if choix is None:
                print("  pas de réponse : édition abandonnée")
                self.nettoyer()
                return 0
            memoriser([s for i, s in enumerate(sujets) if i not in choix])  # ne pas les reproposer
            sujets = [s for i, s in enumerate(sujets) if i in choix]
            if not sujets:
                print("  édition annulée sur Telegram")
                self.nettoyer()
                return 0
            print(f"  {len(sujets)} sujet(s) validé(s)")

        # voix : seulement le texte de chaque info (ni sommaire, ni nom d'edition, ni formule de fin)
        self.etape = "voix"
        print("4/6 Voix")
        v_cfg = cfg["voix"]
        fournisseur = voix.choisir_fournisseur(v_cfg)
        utilises, precedent = set(), None
        for i, s in enumerate(sujets):
            a = self.travail / f"voix_{i}.mp3"
            f, mots = voix.dire(s["texte"], a, v_cfg, fournisseur, precedent)
            if fournisseur == "elevenlabs" and f == "silence" and not args.demo:
                raise RuntimeError(f"la voix ElevenLabs a échoué ({voix.derniere_erreur}). Vérifie ton crédit "
                                   "ElevenLabs, ta clé ou l'identifiant de la voix. Vidéo non fabriquée.")
            utilises.add(f)
            precedent = s["texte"]
            s["_audio"], s["_mots"], s["_duree"] = a, mots, voix.duree(a)
        print(f"  voix : {', '.join(sorted(utilises))}")
        for a in voix.avertissements:
            self.info("ℹ️ " + a)

        # montage
        self.etape = "montage"
        print("5/6 Montage")
        i_cfg = cfg.get("images", {})
        index = carte._par_iso()
        plans = []
        for s in sujets:  # pays : celui donne par la redaction, sinon celui detecte par mots-cles
            if s.get("iso") and carte.connu(s["iso"][0]):
                s["pays"] = pays.nom_iso(s["iso"][0])
            else:
                s["iso"] = [pays.iso_de(s.get("pays", ""))] if pays.iso_de(s.get("pays", "")) else []
            codes = [c for c in s.get("iso", []) if c != "MONDE" and (c == "UE" or carte.connu(c))]
            cible = codes[0] if codes else None
            photo, credit_p = None, ""
            if i_cfg.get("portraits", True) and s.get("personne") and not args.demo:
                photo, credit_p = portrait.chercher(s["personne"])
            plans.append(film.Plan(
                titre=s["titre"], texte=s["texte"], date=s["date_affichee"], sources=s["sources"],
                credit="Carte : NASA, Natural Earth" + (f" · {credit_p}" if photo else ""),
                cible=cible, secondaires=[c for c in codes[1:4] if c != "UE"],
                iso2="eu" if cible == "UE" else (index[cible].get("iso2") if cible else None),
                nom_pays="Union européenne" if cible == "UE" else (pays.nom_iso(cible) if cible else ""),
                photo=photo, audio=s["_audio"], duree=s["_duree"], mots=s["_mots"],
                chiffre=s.get("chiffre"), lien=s.get("lien") if s.get("lien") and all(
                    c in codes for c in (s["lien"]["de"], s["lien"]["vers"])) else None,
            ))
        vid = cfg["video"]
        video, image = film.fabriquer(plans, self.dossier / "video.mp4", vid, self.travail, demo=args.demo,
                                      marque=self.nom if vid.get("fin_marque", True) else "",
                                      voix_reelle=utilises != {"silence"},
                                      graine=int(self.ici.strftime("%Y%m%d%H")))
        for f in self.travail.iterdir():
            f.unlink()
        self.travail.rmdir()

        # fiche de l'edition : legende a coller sous la video, avec les hashtags des pays
        self.etape = "livraison"
        print("6/6 Livraison")
        tags = hashtags.construire(sujets, pays.nom_iso, int(cfg.get("publication", {}).get("hashtags", 10)))
        lignes = [f"• {s['titre']}" for s in sujets] + ["", "Sources :"]
        for s in sujets:
            for src in s["sources"]:
                lien = (s.get("liens") or {}).get(src) or ""
                ligne = f"- {src}" + (f" : {lien}" if lien else "")
                if ligne not in lignes:
                    lignes.append(ligne)
        lignes += ["", "Images : NASA, Natural Earth" + (", Wikimedia Commons" if any(p.photo for p in plans) else "")
                   + ". Voix et montage générés automatiquement.", " ".join(tags)]
        legende = "\n".join(lignes)
        (self.dossier / "legende.txt").write_text(legende, encoding="utf-8")
        (self.dossier / "script.json").write_text(json.dumps({
            "date": self.ici.isoformat(), "redaction": mode, "voix": sorted(utilises), "hashtags": tags,
            "sujets": [{"titre": s["titre"], "texte": s["texte"], "pays": s.get("pays"), "sources": s["sources"],
                        "chiffre": s.get("chiffre"), "lien": s.get("lien"),
                        "articles": [a.to_dict() for a in s.get("articles", [])]} for s in sujets],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        if mode == "extractif" and not args.demo:
            print("  ! ATTENTION : texte non reformulé (mode extractif). Ne publie pas cette vidéo.")
        id_youtube = None
        if not args.demo:
            memoriser(sujets)
            if self.telegram:
                telegram.video(video, f"{self.nom} · {self.date_fr} · {self.heure}\n" + "\n".join(lignes[:len(sujets)])
                               + "\n\nÀ vérifier avant publication.")
                try:
                    telegram.photo(image, "Couverture, à choisir comme miniature sur TikTok")
                except Exception as err:
                    print(f"  ! couverture non envoyée : {err}")
                telegram.message(legende)
                print("  vidéo envoyée sur Telegram")
            self.etape = "publication YouTube"
            id_youtube = publier_youtube(cfg.get("youtube") or {}, video, image, sujets, legende, tags,
                                         args.sans_telegram)
            archiver(sujets, id_youtube, self.ici, args.semaine)
        print(f"Terminé : {video}  ({voix.duree(video):.0f} s)")
        return 0


def publier_youtube(y_cfg: dict, video: Path, image: Path, sujets: list, legende: str, tags: list,
                    sans_telegram: bool):
    """Apres la video : bouton « Publier sur YouTube » sur Telegram (ou publication directe)."""
    mode = y_cfg.get("publication", "bouton")
    if mode == "non" or not youtube.configure():
        return None
    if mode == "bouton":
        if not telegram.configure() or sans_telegram:
            return None
        print("  YouTube : en attente de ta décision sur Telegram")
        choix = telegram.demander("Publier cette vidéo sur YouTube ?",
                                  [("yt", "▶️ Publier sur YouTube"), ("non", "Ne pas publier")],
                                  int(y_cfg.get("delai_minutes", 30)))
        if choix != "yt":
            print("  YouTube : pas de publication")
            return None
    titre = sujets[0]["titre"]
    for t in tags[:2]:  # les hashtags du titre rendent le Short trouvable par pays
        if len(titre) + len(t) + 1 <= 100:
            titre += " " + t
    mots = [t.lstrip("#") for t in tags] + ["actualité", "géopolitique", "politique internationale"]
    try:
        vid = youtube.publier(video, titre, legende + " #Shorts", mots, y_cfg.get("confidentialite", "public"))
        youtube.miniature(vid, image)
        print(f"  publiée sur YouTube : {youtube.lien(vid)}")
        if telegram.configure() and not sans_telegram:
            telegram.message(f"Publiée sur YouTube : {youtube.lien(vid)}")
        return vid
    except Exception as err:
        print(f"  ! publication YouTube impossible : {str(err)[:200]}")
        if telegram.configure() and not sans_telegram:
            telegram.message(f"⚠️ Échec de la publication YouTube : {str(err)[:300]}")
        return None


def archiver(sujets: list, id_youtube, ici: datetime, semaine: bool) -> None:
    """Garde 3 semaines d'infos publiees : recapitulatif du dimanche et bilan des vues."""
    limite = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
    archives = [a for a in lire_json(F_ARCHIVES) if a.get("date", "") >= limite]
    for s in sujets:
        archives.append({
            "date": datetime.now(timezone.utc).isoformat(), "edition": ici.isoformat(), "semaine": semaine,
            "titre": s["titre"], "texte": s["texte"], "iso": s.get("iso", []), "pays": s.get("pays", ""),
            "sources": s["sources"], "liens": s.get("liens", {}), "score": s.get("score", 0),
            "personne": s.get("personne"), "sig": s.get("sig", []), "youtube": id_youtube,
        })
    ecrire_json(F_ARCHIVES, archives)


def bilan(ed: Edition) -> int:
    """Vues, j'aime et commentaires des Shorts de la semaine, par pays et par heure, sur Telegram."""
    ed.etape = "bilan des vues"
    if not youtube.configure():
        print("YouTube non configuré : pas de bilan.")
        return 0
    limite = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    archives = [a for a in lire_json(F_ARCHIVES) if a.get("youtube") and a.get("date", "") >= limite]
    ids = list(dict.fromkeys(a["youtube"] for a in archives))
    if not ids:
        ed.info("📊 Bilan : aucune vidéo publiée sur YouTube cette semaine.")
        return 0
    stats = youtube.statistiques(ids)
    total = sum(v["vues"] for v in stats.values())
    lignes = [f"📊 Bilan YouTube de la semaine : {len(stats)} vidéos, {f'{total:,}'.replace(',', ' ')} vues"]
    top = sorted(stats.items(), key=lambda kv: -kv[1]["vues"])
    lignes.append("\nLes plus vues :")
    for vid, v in top[:3]:
        lignes.append(f"{v['vues']:,} vues · {v['jaime']} j'aime · {v['titre'][:60]}".replace(",", " "))
    par_pays, par_heure = {}, {}
    for a in archives:
        v = stats.get(a["youtube"])
        if not v:
            continue
        par_pays.setdefault(a.get("pays") or "Monde", []).append(v["vues"])
        h = datetime.fromisoformat(a["edition"]).strftime("%Hh")
        par_heure.setdefault(h, {})[a["youtube"]] = v["vues"]
    moy = sorted(((sum(x) / len(x), p) for p, x in par_pays.items()), reverse=True)
    lignes.append("\nVues moyennes par pays : " + " · ".join(f"{p} {m:.0f}" for m, p in moy[:6]))
    heures = sorted((h, sum(d.values()) / len(d)) for h, d in par_heure.items())
    lignes.append("Vues moyennes par heure de publication : " + " · ".join(f"{h} {m:.0f}" for h, m in heures))
    lignes.append("\n(TikTok ne donne pas ses statistiques aux outils extérieurs : regarde-les dans l'appli.)")
    historique = lire_json(F_STATS)
    historique.append({"date": datetime.now(timezone.utc).isoformat(), "videos": stats})
    ecrire_json(F_STATS, historique[-60:])
    ed.info("\n".join(lignes))
    return 0


def deja_faite() -> bool:
    """Vrai si une edition a deja tourne recemment (pour que le creneau de rattrapage ne refasse rien)."""
    try:
        d = datetime.fromisoformat(json.loads(F_DERNIERE.read_text(encoding="utf-8"))["date"])
        return datetime.now(timezone.utc) - d < timedelta(hours=DELAI_DOUBLON_H)
    except Exception:
        return False


def marquer_faite() -> None:
    ecrire_json(F_DERNIERE, {"date": datetime.now(timezone.utc).isoformat()})


def main() -> int:
    p = argparse.ArgumentParser(description="Flash Monde")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--semaine", action="store_true", help="récapitulatif « la semaine dans le monde »")
    p.add_argument("--bilan", action="store_true", help="bilan des vues YouTube de la semaine")
    p.add_argument("--verifier-sources", action="store_true")
    p.add_argument("--voix", choices=["elevenlabs", "edge", "silence"])
    p.add_argument("--sans-telegram", action="store_true")
    p.add_argument("--si-pas-faite", action="store_true",
                   help="ne fait rien si une edition a deja tourne dans les 2 dernieres heures (taches planifiees)")
    p.add_argument("--sans-validation", action="store_true",
                   help="fabrique la video sans attendre ta validation sur Telegram")
    args = p.parse_args()

    os.chdir(RACINE)
    charger_env()
    cfg = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))
    if args.voix:
        cfg["voix"]["fournisseur"] = args.voix
    if args.verifier_sources:
        verifier_sources(cfg)
        return 0

    edition_normale = not (args.demo or args.semaine or args.bilan)
    if edition_normale and args.si_pas_faite and deja_faite():
        print("Une édition a déjà tourné il y a moins de 2 h : rien à faire (créneau de rattrapage).")
        return 0
    if edition_normale:
        marquer_faite()

    ed = Edition(args, cfg)
    try:
        if args.bilan:
            return bilan(ed)
        sujets, mode = ed.sujets_de_la_semaine() if args.semaine else ed.sujets_du_jour()
        return ed.produire(sujets, mode)
    except Exception as err:
        if not args.demo:
            alerter(f"Échec pendant l'étape « {ed.etape} » : {str(err)[:400]}")
        raise


if __name__ == "__main__":
    sys.exit(main())
