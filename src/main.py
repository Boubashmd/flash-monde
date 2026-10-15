"""Flash Monde - fabrique une edition video des gros titres mondiaux.

Utilisation :
    python -m src.main                      fabrique l'edition du moment
    python -m src.main --demo               edition de test avec des articles fictifs
    python -m src.main --verifier-sources   teste chaque flux RSS
Options : --voix silence|edge|elevenlabs   --sans-telegram   --sans-validation
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

from . import carte, cluster, collect, film, pays, portrait, redaction, telegram, voix

RACINE = Path(__file__).resolve().parent.parent
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
    for a in sujet["articles"]:
        try:
            dates.append(datetime.fromisoformat(a.date))
        except (TypeError, ValueError):
            pass
    d = max(dates).astimezone(fuseau) if dates else datetime.now(fuseau)
    return date_longue(d)


def articles_demo() -> list[collect.Article]:
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


def main() -> int:
    p = argparse.ArgumentParser(description="Flash Monde")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--verifier-sources", action="store_true")
    p.add_argument("--voix", choices=["elevenlabs", "edge", "silence"])
    p.add_argument("--sans-telegram", action="store_true")
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

    ed = cfg["edition"]
    nom = cfg["chaine"]["nom"]
    fuseau = ZoneInfo(cfg["chaine"]["fuseau"])
    ici = datetime.now(fuseau)
    date_fr = f"{JOURS[ici.weekday()]} {ici.day}{'er' if ici.day == 1 else ''} {MOIS[ici.month - 1]}"
    heure = f"{ici:%H}h{ici:%M}"
    dossier = RACINE / "sorties" / f"{ici:%Y-%m-%d_%Hh%M}{'_demo' if args.demo else ''}"
    travail = dossier / "travail"
    travail.mkdir(parents=True, exist_ok=True)

    # 1. Collecte
    print("1/6 Collecte")
    if args.demo:
        articles = articles_demo()
    else:
        articles, etat = collect.collecter(cfg["sources"], ed["fenetre_heures"], ed["exclure"])
        for e in etat:
            print(f"  {'OK' if e['ok'] else 'KO'}  {e['nom']:<14} {e['detail']}")
    print(f"  {len(articles)} articles récents")

    # 2. Tri : sujets repris par plusieurs medias, non deja publies
    print("2/6 Tri et recoupement")
    f_vus = RACINE / "data" / "vus.json"
    vus = json.loads(f_vus.read_text(encoding="utf-8")) if f_vus.exists() else []
    limite = (datetime.now(timezone.utc) - timedelta(hours=ed["oubli_heures"])).isoformat()
    vus = [v for v in vus if v["date"] >= limite]
    sujets = cluster.classer(cluster.regrouper(articles), ed["min_sources"], [] if args.demo else vus)
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
        msg = f"{nom} : aucune info suffisamment recoupée ({heure}). Pas de vidéo."
        print("  " + msg)
        if telegram.configure() and not args.sans_telegram and not args.demo:
            telegram.message(msg)
        return 0

    # 3. Redaction (on en prend 2 de plus au cas ou certains sont ecartes)
    print("3/6 Rédaction")
    sujets, mode = redaction.rediger(sujets[: ed["nb_titres"] + 5], cfg["redaction"])
    sujets = sujets[: ed["nb_titres"]]
    if not sujets:
        print("  tous les sujets ont été écartés. Pas de vidéo.")
        return 0
    for s in sujets:
        print(f"  - [{s['pays']}] {s['titre']}  ({', '.join(s['sources'])})")

    def memoriser(liste):
        if args.demo or not liste:
            return
        vus.extend({"sig": s["sig"], "date": datetime.now(timezone.utc).isoformat(), "titre": s["titre"]} for s in liste)
        f_vus.parent.mkdir(exist_ok=True)
        f_vus.write_text(json.dumps(vus, ensure_ascii=False, indent=1), encoding="utf-8")

    def nettoyer():
        for d in (travail, dossier):
            if d.exists() and not any(d.iterdir()):
                d.rmdir()

    # 3 bis. Validation sur Telegram, avant de depenser la voix
    v_val = cfg.get("validation", {})
    if (telegram.configure() and v_val.get("active", True) and not args.demo
            and not args.sans_telegram and not args.sans_validation):
        delai = int(v_val.get("delai_minutes", 30))
        print(f"  validation Telegram : en attente de ta réponse ({delai} min maximum)")
        choix = telegram.valider(sujets, f"{nom} · {date_fr} · {heure}\nSujets proposés :", delai)
        if choix is None:
            print("  pas de réponse : édition abandonnée")
            nettoyer()
            return 0
        memoriser([s for i, s in enumerate(sujets) if i not in choix])  # ne pas les reproposer
        sujets = [s for i, s in enumerate(sujets) if i in choix]
        if not sujets:
            print("  édition annulée sur Telegram")
            nettoyer()
            return 0
        print(f"  {len(sujets)} sujet(s) validé(s)")

    # 4. Voix : seulement le texte de chaque info (ni sommaire, ni nom d'edition, ni formule de fin)
    print("4/6 Voix")
    v_cfg = cfg["voix"]
    fournisseur = voix.choisir_fournisseur(v_cfg)
    utilises, precedent = set(), None
    for i, s in enumerate(sujets):
        a = travail / f"voix_{i}.mp3"
        f, mots = voix.dire(s["texte"], a, v_cfg, fournisseur, precedent)
        utilises.add(f)
        precedent = s["texte"]  # meme ton d'une info a l'autre
        s["_audio"], s["_mots"], s["_duree"] = a, mots, voix.duree(a)
    print(f"  voix : {', '.join(sorted(utilises))}")

    # 5. Montage : camera continue sur le globe, pays du sujet mis en avant
    print("5/6 Montage")
    i_cfg = cfg.get("images", {})
    index = carte._par_iso()
    plans = []
    for s in sujets:  # pays : celui donne par la redaction, sinon celui detecte par mots-cles
        if s.get("iso") and carte.connu(s["iso"][0]):
            s["pays"] = pays.nom_iso(s["iso"][0])
        else:
            s["iso"] = [pays.iso_de(s["pays"])] if pays.iso_de(s["pays"]) else []
        codes = [c for c in s.get("iso", []) if c != "MONDE" and (c == "UE" or carte.connu(c))]
        cible = codes[0] if codes else None
        photo, credit_p = None, ""
        if i_cfg.get("portraits", True) and s.get("personne") and not args.demo:
            photo, credit_p = portrait.chercher(s["personne"])
        plans.append(film.Plan(
            titre=s["titre"], texte=s["texte"], date=date_info(s, fuseau), sources=s["sources"],
            credit="Carte : NASA, Natural Earth" + (f" · {credit_p}" if photo else ""),
            cible=cible, secondaires=[c for c in codes[1:4] if c != "UE"],
            iso2="eu" if cible == "UE" else (index[cible].get("iso2") if cible else None),
            nom_pays="Union européenne" if cible == "UE" else (pays.nom_iso(cible) if cible else ""),
            photo=photo, audio=s["_audio"], duree=s["_duree"], mots=s["_mots"],
        ))
    vid = cfg["video"]
    video = film.fabriquer(plans, dossier / "video.mp4", vid, travail, demo=args.demo,
                           marque=nom if vid.get("fin_marque", True) else "",
                           voix_reelle=utilises != {"silence"})
    for f in travail.iterdir():
        f.unlink()
    travail.rmdir()

    # 6. Fiche de l'edition, memoire des sujets, envoi
    print("6/6 Livraison")
    lignes = [f"• {s['titre']}" for s in sujets]
    lignes += ["", "Sources :"]
    for s in sujets:
        for src in s["sources"]:
            a = next(x for x in s["articles"] if x.source == src)
            lignes.append(f"- {src} : {a.lien or a.titre}")
    lignes += ["", "Images : NASA, Natural Earth" + (", Wikimedia Commons" if any(p.photo for p in plans) else "")
               + ". Voix et montage générés automatiquement.",
               "#actualité #info #monde #géopolitique #flashinfo"]
    legende = "\n".join(lignes)
    (dossier / "legende.txt").write_text(legende, encoding="utf-8")
    (dossier / "script.json").write_text(json.dumps({
        "date": ici.isoformat(), "redaction": mode, "voix": sorted(utilises),
        "sujets": [{"titre": s["titre"], "texte": s["texte"], "pays": s["pays"], "sources": s["sources"],
                    "articles": [a.to_dict() for a in s["articles"]]} for s in sujets],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if mode == "extractif" and not args.demo:
        print("  ! ATTENTION : texte non reformulé (mode extractif). Ne publie pas cette vidéo.")
    if not args.demo:
        memoriser(sujets)
        if telegram.configure() and not args.sans_telegram:
            telegram.video(video, f"{nom} · {date_fr} · {heure}\n" + "\n".join(lignes[: len(sujets)])
                           + "\n\nÀ vérifier avant publication.")
            telegram.message(legende)
            print("  vidéo envoyée sur Telegram")
    print(f"Terminé : {video}  ({voix.duree(video):.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
