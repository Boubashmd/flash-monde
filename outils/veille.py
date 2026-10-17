"""Veille : y a-t-il une video envoyee au bot Telegram ? Si oui, lance le traitement (video.yml).

Lance toutes les 5 minutes par cron-job.org (workflow « Veille video »). Ne consomme AUCUN message
Telegram (simple coup d'oeil), donc ne gene jamais une edition en cours. Bibliotheque standard seule,
pour demarrer en quelques secondes.
"""
import json
import os
import sys
import urllib.error
import urllib.request

TYPES = ["message", "callback_query"]
OCCUPANTS = ("editions.yml", "video.yml", "semaine.yml")


def _json(url: str, donnees=None, entetes=None, methode=None):
    req = urllib.request.Request(url, data=json.dumps(donnees).encode() if donnees is not None else None,
                                 headers={"Content-Type": "application/json", **(entetes or {})}, method=methode)
    with urllib.request.urlopen(req, timeout=30) as r:
        corps = r.read()
        return json.loads(corps) if corps else {}


def main() -> int:
    depot, jeton_gh = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    gh = {"Authorization": f"Bearer {jeton_gh}", "Accept": "application/vnd.github+json"}

    # 1. une edition ou un traitement tourne deja : on repassera dans 5 minutes
    for statut in ("in_progress", "queued"):
        runs = _json(f"https://api.github.com/repos/{depot}/actions/runs?status={statut}&per_page=50",
                     entetes=gh).get("workflow_runs", [])
        if any(r.get("path", "").endswith(OCCUPANTS) for r in runs):
            print(f"Une tâche Flash Monde est déjà en cours ({statut}) : rien à faire.")
            return 0

    # 2. videos deja mises de cote pendant une edition ?
    en_attente = False
    try:
        with open("data/videos_attente.json", encoding="utf-8") as f:
            en_attente = bool(json.load(f))
    except (OSError, ValueError):
        pass

    # 3. coup d'oeil chez Telegram, sans rien confirmer
    if not en_attente:
        chat = str(os.environ["TELEGRAM_CHAT_ID"])
        url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/getUpdates"
        try:
            maj = _json(url, {"timeout": 0, "allowed_updates": TYPES}).get("result", [])
        except urllib.error.HTTPError as err:  # 409 : une autre lecture est en cours
            print(f"Telegram occupé ({err.code}) : on repassera.")
            return 0
        for u in maj:
            m = u.get("message") or {}
            doc = m.get("document") or {}
            if str(m.get("chat", {}).get("id")) == chat and (
                    m.get("video") or m.get("animation") or str(doc.get("mime_type", "")).startswith("video/")):
                en_attente = True
                break

    if not en_attente:
        print("Aucune vidéo reçue.")
        return 0
    _json(f"https://api.github.com/repos/{depot}/actions/workflows/video.yml/dispatches", {"ref": "main"},
          entetes=gh, methode="POST")
    print("Vidéo reçue : traitement lancé (workflow video.yml).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
