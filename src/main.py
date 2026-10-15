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

from . import carte, satellite, cluster, collect, images, pays, portrait, redaction, rendu, telegram, voix

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


def nom_edition(heure: int) -> tuple[str, str]:
    """(texte affiche et lu, nom de dossier)"""
    if heure < 5:
        return "de la nuit", "nuit"
    if heure < 10:
        return "du matin", "matin"
    if heure < 15:
        return "de la mi-journée", "midi"
    if heure < 20:
        return "du soir", "soir"
    return "de la nuit", "nuit"


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

    ed, vid = cfg["edition"], cfg["video"]
    L, H, fps = vid["largeur"], vid["hauteur"], vid["fps"]
    accent = rendu._hex(vid["couleur_accent"])
    nom = cfg["chaine"]["nom"]
    ici = datetime.now(ZoneInfo(cfg["chaine"]["fuseau"]))
    edition, court = nom_edition(ici.hour)
    date_fr = f"{JOURS[ici.weekday()]} {ici.day}{'er' if ici.day == 1 else ''} {MOIS[ici.month - 1]}"
    dossier = RACINE / "sorties" / f"{ici:%Y-%m-%d}_{court}{'_demo' if args.demo else ''}"
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
        msg = f"{nom} : aucune info suffisamment recoupée pour l'édition {edition}. Pas de vidéo."
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
        choix = telegram.valider(sujets, f"{nom} · édition {edition} · {date_fr}\nSujets proposés :", delai)
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

    # 4. Voix
    print("4/6 Voix")
    v_cfg = cfg["voix"]
    fournisseur = voix.choisir_fournisseur(v_cfg)
    textes = (
        # les 2 premieres secondes decident si on reste : on ouvre sur l'info principale
        [f"{sujets[0]['titre'].replace(' : ', ', ')}. "
         + (f"Et {len(sujets) - 1} autre{'s' if len(sujets) > 2 else ''} info{'s' if len(sujets) > 2 else ''} à retenir. " if len(sujets) > 1 else "")
         + f"{nom}, édition {edition}."]
        + [s["texte"] for s in sujets]
        + [f"C'était {nom}. {cfg['chaine']['signature']}"]
    )
    audios, utilises = [], set()
    for i, t in enumerate(textes):
        a = travail / f"voix_{i}.mp3"
        utilises.add(voix.dire(t, a, v_cfg, fournisseur))
        audios.append((a, voix.duree(a)))
    print(f"  voix : {', '.join(sorted(utilises))}")

    # 5. Montage
    print("5/6 Montage")
    commun = dict(nom=nom, accent=accent, demo=args.demo)
    segments = []

    i_cfg = cfg["images"]
    mode_images = i_cfg.get("fournisseur", "carte")
    y_st = 1610 if mode_images == "satellite" else 1250  # sous-titres sous la carte

    def monter(i, photo, fixe, zoom_avant, centrage=(0.5, 0.5)):
        audio, d = audios[i]
        f = rendu.fond(photo, travail / f"fond_{i}.jpg", L, H, variante=i, centrage=centrage)
        blocs = rendu.decouper(textes[i])
        st = [
            (rendu.calque_sous_titre(b, travail / f"st_{i}_{j}.png", L, H, 1560 if i in (0, len(textes) - 1) else y_st), t0, t1)
            for j, (b, (t0, t1)) in enumerate(zip(blocs, rendu.minuter(blocs, d)))
        ]
        segments.append(rendu.segment(f, fixe, st, audio, d, travail / f"seg_{i}.mp4", L=L, H=H, fps=fps, zoom_avant=zoom_avant))


    def visuel(n, s):
        """(image, credit, voile, centrage) : portrait libre, sinon carte, sinon photo du pays."""
        photo_p, credit_p = None, ""
        if i_cfg.get("portraits", True) and s.get("personne") and not args.demo:
            photo_p, credit_p = portrait.chercher(s["personne"])
        if mode_images == "satellite":
            codes = [c for c in s.get("iso", []) if c == "UE" or carte.connu(c)]
            img = satellite.rendre(codes, travail / f"sat_{n}.jpg", L, H, centre_y=0.66 if photo_p else 0.6,
                                   portrait=photo_p)
            credit = "Carte : NASA, Natural Earth" + (f" · {credit_p}" if photo_p else "")
            return img, credit, 45, (0.5, 0.5)
        if photo_p:
            return photo_p, credit_p, 110, (0.5, 0.2)
        if mode_images == "carte":
            codes = [c for c in s.get("iso", []) if carte.connu(c)]
            return carte.rendre(codes or ["MONDE"], travail / f"carte_{n}.jpg", L, H, accent), "Carte : Natural Earth", 55, (0.5, 0.5)
        photo, credit = images.choisir(s["pays"], s["requete"], i_cfg)
        return photo, credit, 120, (0.5, 0.5)

    for s in sujets:  # pays : celui donne par la redaction, sinon celui detecte par mots-cles
        if s.get("iso") and carte.connu(s["iso"][0]):
            s["pays"] = pays.nom_iso(s["iso"][0])
        else:
            s["iso"] = [pays.iso_de(s["pays"])] if pays.iso_de(s["pays"]) else []
    tous = [c for s in sujets for c in s.get("iso", [])[:1] if c not in ("UE", "MONDE")]
    if mode_images == "satellite":
        photo_monde = satellite.rendre_monde(tous, travail / "carte_monde.jpg", L, H)
    elif mode_images == "carte":
        photo_monde = carte.rendre_monde(tous, travail / "carte_monde.jpg", L, H, accent)
    else:
        photo_monde, _ = images.choisir("Monde", "world map globe night", i_cfg)
    monter(0, photo_monde, rendu.calque_intro(
        travail / "fixe_0.png", L, H, edition=edition, date=date_fr.capitalize(),
        titres=[s["titre"] for s in sujets], **commun), True)
    for n, s in enumerate(sujets, 1):
        photo, credit, voile, centrage = visuel(n, s)
        fixe = rendu.calque_info(
            travail / f"fixe_{n}.png", L, H, sous=f"Édition {edition} · {date_fr}",
            rang=n, total=len(sujets), pays=s["pays"], titre=s["titre"],
            sources=s["sources"], credit=credit, voile=voile,
            y_sources=1720 if mode_images == "satellite" else 1470, **commun)
        monter(n, photo, fixe, n % 2 == 0, centrage)
    monter(len(textes) - 1, photo_monde, rendu.calque_fin(
        travail / "fixe_fin.png", L, H, signature=cfg["chaine"]["signature"], **commun), False)

    video = rendu.assembler(segments, dossier / "video.mp4")
    for f in travail.iterdir():
        f.unlink()
    travail.rmdir()

    # 6. Fiche de l'edition, memoire des sujets, envoi
    print("6/6 Livraison")
    lignes = [f"{nom} · édition {edition} · {date_fr}", ""]
    lignes += [f"{n}. {s['titre']}" for n, s in enumerate(sujets, 1)]
    lignes += ["", "Sources :"]
    for s in sujets:
        for src in s["sources"]:
            a = next(x for x in s["articles"] if x.source == src)
            lignes.append(f"- {src} : {a.lien or a.titre}")
    lignes += ["", "Voix et montage générés automatiquement.", "#actualité #info #monde #flashinfo"]
    legende = "\n".join(lignes)
    (dossier / "legende.txt").write_text(legende, encoding="utf-8")
    (dossier / "script.json").write_text(json.dumps({
        "edition": edition, "date": ici.isoformat(), "redaction": mode, "voix": sorted(utilises),
        "sujets": [{"titre": s["titre"], "texte": s["texte"], "pays": s["pays"], "sources": s["sources"],
                    "articles": [a.to_dict() for a in s["articles"]]} for s in sujets],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if mode == "extractif" and not args.demo:
        print("  ! ATTENTION : texte non reformulé (mode extractif). Ne publie pas cette vidéo.")
    if not args.demo:
        memoriser(sujets)
        if telegram.configure() and not args.sans_telegram:
            telegram.video(video, "\n".join(lignes[: 2 + len(sujets)]) + "\n\nÀ vérifier avant publication.")
            telegram.message(legende)
            print("  vidéo envoyée sur Telegram")
    print(f"Terminé : {video}  ({voix.duree(video):.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
