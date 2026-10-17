"""Synthese vocale : Azure (gratuit jusqu'a 500 000 caracteres par mois), ElevenLabs (payant),
Edge (gratuit, non officiel) ou silence (test).

Chaque fichier audio est accompagne des horaires de ses mots, pour des sous-titres
synchronises mot a mot :
- Azure : evenements "WordBoundary" du SDK Microsoft ;
- ElevenLabs : horaires exacts renvoyes par l'API (endpoint "with-timestamps") ;
- Edge : evenements "WordBoundary" ;
- sinon : estimation d'apres la longueur des mots et la ponctuation.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import unicodedata
from pathlib import Path

import requests

API = "https://api.elevenlabs.io/v1/text-to-speech"


def duree(fichier: Path) -> float:
    """Duree en secondes, lue avec ffmpeg seul (pas besoin de ffprobe)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(fichier)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise RuntimeError(f"durée illisible pour {fichier.name}")
    h, mn, sec = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(sec)


# ------------------------------------------------------------------ horaires des mots

def _mots(texte: str) -> list:
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", texte)]


def _nu(mot: str) -> str:
    """Mot sans accents ni ponctuation, pour comparer deux decoupages."""
    m = unicodedata.normalize("NFD", mot.lower())
    return "".join(c for c in m if c.isalnum())


def estimer_mots(texte: str, duree_audio: float, debut: float = 0.12, marge_fin: float = 0.15) -> list:
    """Horaires approximatifs, quand le fournisseur de voix n'en donne pas."""
    mots = _mots(texte)
    if not mots:
        return []
    poids = [len(m) + (7 if m[-1] in ".!?…" else 4 if m[-1] in ",;:" else 1.5) for m, _, _ in mots]
    dispo = max(0.5, duree_audio - debut - marge_fin)
    t, out = debut, []
    total = sum(poids)
    for (m, _, _), p in zip(mots, poids):
        d = dispo * p / total
        out.append((m, round(t, 3), round(t + d * 0.9, 3)))
        t += d
    return out


def _depuis_alignement(texte: str, al: dict) -> list | None:
    """Horaires des mots a partir de l'alignement caractere par caractere d'ElevenLabs."""
    try:
        car = al["characters"]
        debuts = al["character_start_times_seconds"]
        fins = al["character_end_times_seconds"]
    except (KeyError, TypeError):
        return None
    lu = "".join(car)
    if lu == texte:
        return [(m, float(debuts[a]), float(fins[b - 1])) for m, a, b in _mots(texte)]
    # texte legerement normalise par l'API : on retrouve chaque mot dans l'ordre
    out, curseur = [], 0
    for m, _, _ in _mots(texte):
        k = lu.find(m, curseur)
        if k < 0:
            return None
        out.append((m, float(debuts[k]), float(fins[k + len(m) - 1])))
        curseur = k + len(m)
    return out


def _recaler(texte: str, reperes: list) -> list | None:
    """Associe les mots reperes par Edge (sans ponctuation, parfois coupes : "L'" + "armée")
    aux mots du texte. Renvoie None en cas de desaccord : les horaires seront estimes."""
    jetons = [(_nu(r[0]), r[1], r[2]) for r in reperes if _nu(r[0])]
    out, j, fin = [], 0, 0.0
    for m, _, _ in _mots(texte):
        cible = _nu(m)
        if not cible:  # signe isole (tiret...) : meme horaire que le mot precedent
            out.append((m, fin, fin))
            continue
        lu, t0, t1 = "", None, fin
        while j < len(jetons) and len(lu) < len(cible):
            n, a, b = jetons[j]
            t0 = a if t0 is None else t0
            lu, t1, j = lu + n, b, j + 1
        if lu != cible:
            return None
        out.append((m, t0, t1))
        fin = t1
    return out


# ------------------------------------------------------------------ fournisseurs

def _reglages(cfg: dict, modele: str) -> dict:
    r = cfg.get("reglages") or {}
    if modele.startswith("eleven_v3"):
        # v3 n'accepte que 3 niveaux de stabilite : 0 creatif, 0.5 naturel, 1 regulier
        st = float(r.get("stabilite", 0.5))
        s = {"stability": min((0.0, 0.5, 1.0), key=lambda v: abs(v - st)),
             "similarity_boost": float(r.get("similarite", 0.75))}
    else:
        s = {
            "stability": float(r.get("stabilite", 0.45)),
            "similarity_boost": float(r.get("similarite", 0.75)),
            "style": float(r.get("style", 0.1)),
            "use_speaker_boost": True,
        }
    vitesse = float(r.get("vitesse", 1.0))
    if abs(vitesse - 1.0) > 0.01:
        s["speed"] = vitesse
    return s


def pour_la_voix(texte: str) -> str:
    """Petites retouches pour une lecture naturelle (le texte affiche reste inchange)."""
    t = re.sub(r"(\d)[ \u00a0\u202f](\d{3})\b", r"\1\2", texte)  # 12 000 -> 12000 (un seul nombre)
    t = t.replace("«", "").replace("»", "").replace("(", ", ").replace(")", ", ")
    t = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s+", " ", t))
    t = re.sub(r",\s*,", ",", t)
    return re.sub(r",\s*([.!?])", r"\1", t).strip()


def _associer(texte: str, dits: list) -> list | None:
    """Reporte les horaires des mots prononces sur les mots affiches (decoupages differents)."""
    affiches = [m for m, _, _ in _mots(texte)]
    out, i, j, fin = [], 0, 0, 0.0
    while i < len(affiches):
        if not _nu(affiches[i]):
            out.append((affiches[i], fin, fin))
            i += 1
            continue
        ga, gb = [i], [j]
        la, lb = _nu(affiches[i]), _nu(dits[j][0]) if j < len(dits) else None
        if lb is None:
            return None
        while la != lb:
            if len(la) < len(lb):
                i2 = ga[-1] + 1
                if i2 >= len(affiches):
                    return None
                ga.append(i2)
                la += _nu(affiches[i2])
            else:
                j2 = gb[-1] + 1
                if j2 >= len(dits):
                    return None
                gb.append(j2)
                lb += _nu(dits[j2][0])
        t0, t1 = dits[gb[0]][1], dits[gb[-1]][2]
        pas = (t1 - t0) / len(ga)
        for k, ia in enumerate(ga):
            out.append((affiches[ia], t0 + k * pas, t0 + (k + 1) * pas))
        fin, i, j = t1, ga[-1] + 1, gb[-1] + 1
        while j < len(dits) and not _nu(dits[j][0]):
            j += 1
    return out


_MODELE_RETENU: dict = {}  # un seul modele par edition : la voix ne change pas d'une info a l'autre
avertissements: list = []  # remontes sur Telegram (ex. : modele indisponible, repli)
derniere_erreur = ""
DIALOGUE = "https://api.elevenlabs.io/v1/text-to-dialogue/with-timestamps"


def voix_id(cfg: dict) -> str:
    """La voix se change sans toucher au code : variable GitHub ELEVENLABS_VOICE_ID."""
    return os.getenv("ELEVENLABS_VOICE_ID") or cfg["elevenlabs_voice_id"]


def _modeles(cfg: dict) -> list:
    """Modele voulu (variable GitHub ELEVENLABS_MODELE ou config), puis modeles de secours."""
    liste = [os.getenv("ELEVENLABS_MODELE") or cfg.get("elevenlabs_modele", "eleven_multilingual_v2")]
    secours = cfg.get("elevenlabs_modele_secours", ["eleven_multilingual_v2"])
    for m in ([secours] if isinstance(secours, str) else secours):
        if m not in liste:
            liste.append(m)
    if "eleven_multilingual_v2" not in liste:
        liste.append("eleven_multilingual_v2")
    return liste


def _requete(dit: str, modele: str, cfg: dict, precedent: str | None):
    entetes = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    params = {"output_format": "mp3_44100_128"}
    if modele.startswith("eleven_v4"):  # servi par l'API « text-to-dialogue »
        corps = {"inputs": [{"text": dit, "voice_id": voix_id(cfg)}], "model_id": modele,
                 "settings": {"stability": _reglages(cfg, "eleven_v3")["stability"]}}
        if precedent:
            corps["previous_text"] = pour_la_voix(precedent)[-100:]
        r = requests.post(DIALOGUE, params=params, headers=entetes, json=corps, timeout=180)
        if r.status_code == 422:  # reglage refuse : on reessaie sans
            corps.pop("settings")
            r = requests.post(DIALOGUE, params=params, headers=entetes, json=corps, timeout=180)
        return r
    corps = {"text": dit, "model_id": modele, "voice_settings": _reglages(cfg, modele)}
    if precedent and not modele.startswith("eleven_v3"):  # meme ton d'une info a l'autre
        corps["previous_text"] = pour_la_voix(precedent)
    return requests.post(f"{API}/{voix_id(cfg)}/with-timestamps", params=params, headers=entetes,
                         json=corps, timeout=180)


def _elevenlabs(texte: str, sortie: Path, cfg: dict, precedent: str | None) -> list | None:
    modeles = [_MODELE_RETENU["modele"]] if "modele" in _MODELE_RETENU else _modeles(cfg)
    dit = pour_la_voix(texte)
    erreur = ""
    for modele in modeles:
        r = _requete(dit, modele, cfg, precedent)
        if r.status_code < 400:
            d = r.json()
            sortie.write_bytes(base64.b64decode(d["audio_base64"]))
            if "modele" not in _MODELE_RETENU:
                _MODELE_RETENU["modele"] = modele
                if modele != modeles[0]:
                    avertissements.append(f"Voix : {modeles[0]} indisponible ({erreur[:120]}), "
                                          f"édition lue avec {modele}.")
                print(f"  modèle de voix : {modele}")
            al = d.get("alignment") or d.get("normalized_alignment")
            dits = _depuis_alignement(dit, al) if al else None
            return _associer(texte, dits) if dits else None
        erreur = f"{modele} : {r.status_code} {r.text[:120]}"
        print(f"  ! ElevenLabs {erreur}")
        if r.status_code in (401, 402) or "quota" in r.text.lower():  # cle ou credit : inutile d'insister
            break
    raise RuntimeError(erreur)


def _edge(texte: str, sortie: Path, cfg: dict) -> list | None:
    import edge_tts

    reperes = []

    async def lancer():
        try:
            comm = edge_tts.Communicate(texte, cfg.get("edge_voix", "fr-FR-HenriNeural"), boundary="WordBoundary")
        except TypeError:  # anciennes versions : mots reperes par defaut
            comm = edge_tts.Communicate(texte, cfg.get("edge_voix", "fr-FR-HenriNeural"))
        with open(sortie, "wb") as f:
            async for bloc in comm.stream():
                if bloc["type"] == "audio":
                    f.write(bloc["data"])
                elif bloc["type"] == "WordBoundary":
                    t0 = bloc["offset"] / 1e7
                    reperes.append((bloc["text"], t0, t0 + bloc["duration"] / 1e7))

    asyncio.run(lancer())
    return _recaler(texte, reperes)


def _azure(texte: str, sortie: Path, cfg: dict) -> list | None:
    """Voix Microsoft Azure (cle AZURE_SPEECH_KEY, region AZURE_SPEECH_REGION)."""
    import datetime
    from xml.sax.saxutils import escape

    import azure.cognitiveservices.speech as sdk

    conf = sdk.SpeechConfig(subscription=os.environ["AZURE_SPEECH_KEY"],
                            region=os.environ["AZURE_SPEECH_REGION"])
    conf.set_speech_synthesis_output_format(sdk.SpeechSynthesisOutputFormat.Audio24Khz160KBitRateMonoMp3)
    synth = sdk.SpeechSynthesizer(speech_config=conf, audio_config=None)
    reperes = []

    def mot(evt):
        if evt.boundary_type != sdk.SpeechSynthesisBoundaryType.Word:
            return
        t0 = evt.audio_offset / 1e7
        d = evt.duration
        d = d.total_seconds() if isinstance(d, datetime.timedelta) else d / 1e7
        reperes.append((evt.text, t0, t0 + d))

    synth.synthesis_word_boundary.connect(mot)
    voix = cfg.get("azure_voix", "fr-FR-RemyMultilingualNeural")
    vitesse = cfg.get("azure_vitesse", "0%")
    ssml = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="fr-FR">'
            f'<voice name="{voix}"><lang xml:lang="fr-FR"><prosody rate="{vitesse}">'
            f'{escape(pour_la_voix(texte))}</prosody></lang></voice></speak>')
    res = synth.speak_ssml_async(ssml).get()
    if res.reason != sdk.ResultReason.SynthesizingAudioCompleted:
        detail = sdk.CancellationDetails(res)
        raise RuntimeError(f"Azure : {detail.reason} {detail.error_details}"[:300])
    sortie.write_bytes(res.audio_data)
    return _associer(texte, reperes) if reperes else None


def _azure_pret(cfg: dict) -> bool:
    """Cles presentes, module installe et petit essai reussi : sinon, voix de secours."""
    if not (os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION")):
        print("  ! Azure non configuré (AZURE_SPEECH_KEY / AZURE_SPEECH_REGION)")
        return False
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _azure("Test.", Path(d) / "essai.mp3", cfg)
        return True
    except Exception as err:
        msg = " ".join(str(err).split())[:150]
        print(f"  ! Azure indisponible : {msg}")
        avertissements.append(f"Voix Azure indisponible ({msg}), voix de secours utilisée.")
        return False


def _silence(texte: str, sortie: Path) -> None:
    secondes = max(2.0, len(texte) / 15)  # environ 15 caracteres par seconde a l'oral
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", f"{secondes:.2f}", "-c:a", "libmp3lame", str(sortie)],
        check=True,
    )


def choisir_fournisseur(cfg: dict) -> str:
    """Determine une fois pour toutes la voix de l'edition (pas de melange de voix)."""
    f = cfg.get("fournisseur", "elevenlabs")
    if f == "azure" and not _azure_pret(cfg):
        f = "elevenlabs"
    if f == "elevenlabs":
        if not os.getenv("ELEVENLABS_API_KEY") or "REMPLACE" in (os.getenv("ELEVENLABS_VOICE_ID")
                                                                 or cfg.get("elevenlabs_voice_id", "REMPLACE")):
            print("  ! ElevenLabs non configuré : passage à la voix gratuite Edge")
            f = "edge"
    if f == "edge":
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            print("  ! module edge-tts absent : vidéo sans voix")
            f = "silence"
    return f


def dire(texte: str, sortie: Path, cfg: dict, fournisseur: str, precedent: str | None = None) -> tuple:
    """Cree le fichier audio. Renvoie (fournisseur reellement utilise, horaires des mots)."""
    try:
        if fournisseur == "azure":
            mots = _azure(texte, sortie, cfg)
        elif fournisseur == "elevenlabs":
            mots = _elevenlabs(texte, sortie, cfg, precedent)
        elif fournisseur == "edge":
            mots = _edge(texte, sortie, cfg)
        else:
            _silence(texte, sortie)
            mots = None
        utilise = fournisseur
    except Exception as err:
        global derniere_erreur
        derniere_erreur = str(err)[:300]
        print(f"  ! voix {fournisseur} en échec ({str(err)[:80]}) : segment sans voix")
        _silence(texte, sortie)
        mots, utilise = None, "silence"
    if not mots:
        mots = estimer_mots(texte, duree(sortie))
    return utilise, mots
