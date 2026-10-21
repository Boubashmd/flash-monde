"""Comparatif de voix : lit la meme breve avec plusieurs voix et envoie les extraits sur Telegram.

Lance par le workflow « Essai des voix » (GitHub > Actions > Essai des voix > Run workflow).
Rien n'est change dans la configuration : c'est seulement pour ecouter et choisir.
"""
import copy
import json
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yaml  # noqa: E402

from src import telegram, voix  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
TEXTE_DEFAUT = ("À Washington, Donald Trump a reçu Emmanuel Macron à la Maison-Blanche. Les deux dirigeants ont "
                "évoqué l'Ukraine et les droits de douane, sans annoncer d'accord.")


def breve() -> str:
    try:
        archives = json.loads((RACINE / "data" / "archives.json").read_text(encoding="utf-8"))
        a = [x for x in archives if x.get("texte")][-1]
        return a["texte"]
    except Exception:
        return TEXTE_DEFAUT


def azure_hd(texte: str, sortie: Path, nom: str) -> None:
    """Voix HD d'Azure (« Dragon HD ») : SSML simple, sans reglage de vitesse."""
    import azure.cognitiveservices.speech as sdk
    conf = sdk.SpeechConfig(subscription=os.environ["AZURE_SPEECH_KEY"], region=os.environ["AZURE_SPEECH_REGION"])
    conf.set_speech_synthesis_output_format(sdk.SpeechSynthesisOutputFormat.Audio24Khz160KBitRateMonoMp3)
    synth = sdk.SpeechSynthesizer(speech_config=conf, audio_config=None)
    ssml = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="fr-FR">'
            f'<voice name="{nom}">{escape(voix.pour_la_voix(texte))}</voice></speak>')
    res = synth.speak_ssml_async(ssml).get()
    if res.reason != sdk.ResultReason.SynthesizingAudioCompleted:
        d = sdk.CancellationDetails(res)
        raise RuntimeError(f"{d.reason} {d.error_details}"[:250])
    sortie.write_bytes(res.audio_data)


def main() -> int:
    os.chdir(RACINE)
    cfg = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))["voix"]
    texte = breve()
    dossier = RACINE / "sorties" / "essai_voix"
    dossier.mkdir(parents=True, exist_ok=True)
    essais = []

    def azure(vitesse):
        c = copy.deepcopy(cfg)
        c["azure_voix"], c["azure_vitesse"] = "fr-FR-RemyMultilingualNeural", vitesse
        return lambda s: voix._azure(texte, s, c)

    essais.append(("1. Rémy (Azure, actuelle, vitesse normale)", azure("0%")))
    essais.append(("2. Rémy (Azure, actuelle, +6 %)", azure("+6%")))
    essais.append(("3. Rémy HD (Azure Dragon HD)", lambda s: azure_hd(texte, s, "fr-FR-Remy:DragonHDLatestNeural")))
    essais.append(("4. Vivienne HD (Azure Dragon HD, voix féminine)",
                   lambda s: azure_hd(texte, s, "fr-FR-Vivienne:DragonHDLatestNeural")))
    if os.getenv("ELEVENLABS_API_KEY"):
        essais.append(("5. ElevenLabs (voix configurée)", lambda s: voix._elevenlabs(texte, s, cfg, None)))

    telegram.message("🎧 Comparatif de voix sur la même brève :\n\n" + texte)
    for k, (nom, fabriquer) in enumerate(essais, 1):
        sortie = dossier / f"voix_{k}.mp3"
        try:
            fabriquer(sortie)
            with open(sortie, "rb") as f:
                telegram._api("sendAudio", data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "title": nom,
                                                 "caption": nom}, files={"audio": f})
            print(f"OK  {nom}")
        except Exception as err:
            print(f"KO  {nom} : {err}")
            telegram.message(f"{nom} : indisponible ({str(err)[:200]})")
    telegram.message("Dis-moi laquelle tu préfères (le numéro), je la mets en place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
