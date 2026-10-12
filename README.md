# Flash Monde

Fabrique automatiquement une vidéo verticale (1080x1920, environ 1 minute) des gros titres mondiaux en français, 3 à 4 fois par jour, et te l'envoie sur Telegram pour validation avant publication.

## Ce que fait chaque édition

1. **Collecte** : lit les flux RSS listés dans `config.yaml`.
2. **Tri** : regroupe les articles qui parlent du même fait et ne garde que les sujets repris par au moins 2 médias différents. Un sujet déjà traité n'est pas repris pendant 36 h.
3. **Rédaction** : Claude réécrit chaque info en 2 phrases neutres, uniquement à partir de ce que les sources ont en commun. Il peut écarter un sujet si les sources se contredisent.
4. **Voix** : ElevenLabs (payant) ou Edge (gratuit).
5. **Montage** : photo libre Pexels choisie selon le pays (ou fond dégradé), titre, sous-titres, sources affichées à l'écran.
6. **Livraison** : `sorties/<date>_<édition>/` contient `video.mp4`, `legende.txt` (texte à coller sous la vidéo, avec les liens des sources) et `script.json` (tout ce qui a servi, pour vérifier).

S'il n'y a aucun sujet assez recoupé, aucune vidéo n'est produite. C'est voulu.

## Installation sur ton PC

Il faut Python 3.10 ou plus, et FFmpeg (`winget install Gyan.FFmpeg` sous Windows, `sudo apt install ffmpeg` sous Linux).

```
pip install -r requirements.txt
python -m src.main --demo --voix silence
```

Cette commande fabrique une vidéo de test avec des articles fictifs, sans aucune clé. Si elle fonctionne, l'installation est bonne.

## Les clés à créer

Copie `.env.example` en `.env` et remplis-le.

| Clé | Où l'obtenir | Sert à |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | Rédaction des brèves |
| `ELEVENLABS_API_KEY` | elevenlabs.io, puis mets l'identifiant de la voix choisie dans `config.yaml` (`elevenlabs_voice_id`) | Voix |
| `PIXABAY_API_KEY` | pixabay.com/api/docs (gratuit, connecte-toi : la clé apparaît dans la page) | Photos |
| `TELEGRAM_BOT_TOKEN` | parle à @BotFather sur Telegram, commande `/newbot` | Envoi de la vidéo |
| `TELEGRAM_CHAT_ID` | envoie un message à ton bot, puis ouvre `https://api.telegram.org/bot<TOKEN>/getUpdates` et lis `chat.id` | Destinataire |

Sans clé ElevenLabs, la voix gratuite Edge est utilisée. Sans clé Pixabay, le fond est un dégradé. Sans clé Anthropic, le texte n'est **pas reformulé** : ces vidéos servent à tester, ne les publie pas.

## Utilisation

```
python -m src.main --verifier-sources   # teste chaque flux RSS
python -m src.main --sans-telegram      # fabrique l'édition, sans l'envoyer
python -m src.main                      # fabrique et envoie sur Telegram
```

Commence par `--verifier-sources` : mets un `#` dans `config.yaml` devant les sources qui ne répondent pas.

## Automatisation avec GitHub Actions

1. Crée un dépôt **privé** sur GitHub et pousse ce dossier dedans.
2. Dans le dépôt : Settings > Secrets and variables > Actions, ajoute les 5 clés ci-dessus.
3. Onglet Actions > Flash Monde > Run workflow pour un premier essai manuel.

Ensuite les éditions partent seules à 7h00, 12h30, 18h30 et 22h00 (heure d'hiver de Paris). Les horaires se changent dans `.github/workflows/editions.yml`.

## Validation sur Telegram

Avant de fabriquer la voix et la vidéo, le bot t'envoie les sujets retenus, un message par sujet, avec deux boutons : **Garder** ou **Écarter**. Un dernier message propose **Lancer la vidéo** ou **Annuler l'édition**.

- Tous les sujets sont gardés par défaut : si tout te va, un seul clic sur « Lancer la vidéo » suffit.
- Les sujets écartés ne sont pas reproposés pendant 36 h.
- Sans réponse dans le délai (30 min par défaut, réglable dans `config.yaml`, section `validation`), l'édition est abandonnée et rien n'est consommé chez ElevenLabs.
- `python -m src.main --sans-validation` fabrique directement, sans attendre.

Sur GitHub Actions, l'attente compte dans les minutes de calcul. Un dépôt **public** a des minutes illimitées ; un dépôt privé gratuit est limité (2 000 minutes par mois au moment où j'écris ces lignes), ce que 4 éditions par jour avec attente peuvent dépasser. Le code ne contient aucun secret : les clés restent dans les « Secrets » du dépôt.

## Réglages utiles (`config.yaml`)

- `edition.zones_prioritaires` et `edition.themes_prioritaires` : la ligne éditoriale. Ces sujets passent devant les autres ; avec `zones_strictes: true`, ils sont les seuls gardés.
- `edition.nb_titres` : nombre d'infos par vidéo.
- `edition.min_sources` : passe à 3 pour être plus strict, au prix de moins de sujets.
- `edition.exclure` : mots qui font ignorer un article.
- `chaine.nom`, `chaine.signature`, `video.couleur_accent` : habillage.

## Avant de publier

- **Regarde chaque vidéo.** Le recoupement réduit les erreurs, il ne les supprime pas : deux médias peuvent reprendre la même dépêche erronée.
- Ouvre `script.json` en cas de doute : tu y vois les articles d'origine de chaque brève.
- Sur TikTok et YouTube, coche l'option signalant un contenu généré par IA.
- Les photos illustrent le pays, pas l'événement. N'ajoute jamais d'image tirée d'un média.

## Limites connues de cette première version

- Les sous-titres sont calés sur la durée de la voix, pas mot à mot : un léger décalage est possible sur les phrases longues.
- Le pays est déduit des titres par mots-clés (`src/pays.py`, environ 70 pays). Sans pays reconnu, l'illustration est générique.
- La publication sur les réseaux reste manuelle.
