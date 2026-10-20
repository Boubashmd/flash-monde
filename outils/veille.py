"""Veille : une video envoyee au bot, ou une decision prise sur Telegram ? Si oui, lance la suite.

Lance toutes les 5 minutes par cron-job.org (workflow « Veille video »). Ne consomme AUCUN message
Telegram (simple coup d'oeil), donc ne gene jamais une edition en cours. Bibliotheque standard seule,
pour demarrer en quelques secondes.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TYPES = ["message", "callback_query"]
OCCUPANTS = ("editions.yml", "video.yml", "semaine.yml")
DECISIONS = ("go", "stop", "yt", "non")


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
    recent = datetime.now(timezone.utc) - timedelta(minutes=100)  # une tache plus vieille est bloquee : on l'ignore
    for statut in ("in_progress", "queued"):
        runs = _json(f"https://api.github.com/repos/{depot}/actions/runs?status={statut}&per_page=50",
                     entetes=gh).get("workflow_runs", [])
        actifs = [r for r in runs if r.get("path", "").endswith(OCCUPANTS)
                  and datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) > recent]
        if actifs:
            print(f"Une tâche Flash Monde est déjà en cours ({statut}, {actifs[0].get('path')}) : rien à faire.")
            return 0

    # 2. ce qui est deja note dans le depot : videos mises de cote, decisions prises sur Telegram
    def charger(chemin):
        try:
            with open(chemin, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    video = bool(charger("data/videos_attente.json"))
    attentes = charger("data/attente.json")
    jetons = {r["jeton"] for r in attentes}
    maintenant = datetime.now(timezone.utc)
    reprise = any(r.get("decision") or maintenant - datetime.fromisoformat(r["cree"]) > timedelta(hours=24)
                  for r in attentes)

    # 3. coup d'oeil chez Telegram, sans rien confirmer : video envoyee ? bouton de decision clique ?
    clics = []
    if True:
        chat = str(os.environ["TELEGRAM_CHAT_ID"])
        url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/getUpdates"
        try:
            maj = _json(url, {"timeout": 0, "allowed_updates": TYPES}).get("result", [])
        except urllib.error.HTTPError as err:  # 409 : une autre lecture est en cours
            print(f"Telegram occupé ({err.code}) : on repassera.")
            maj = []
        print(f"{len(maj)} mise(s) à jour Telegram en attente, décisions connues : {sorted(jetons)}")
        for u in maj:
            cq = u.get("callback_query") or {}
            jeton, _, action = cq.get("data", "").partition(":")
            if jeton in jetons and action.split(":")[0] in DECISIONS:
                reprise = True
                clics.append(cq)
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) == chat and (m.get("text") or "").strip().lower() in ("relance", "/relance"):
                reprise = True  # commande de secours : « relance » envoye au bot
            doc = m.get("document") or {}
            if str(m.get("chat", {}).get("id")) == chat and (
                    m.get("video") or m.get("animation") or str(doc.get("mime_type", "")).startswith("video/")):
                video = True

    if reprise:
        _json(f"https://api.github.com/repos/{depot}/actions/workflows/editions.yml/dispatches",
              {"ref": "main", "inputs": {"reprendre": "true"}}, entetes=gh, methode="POST")
        print("Décision prise sur Telegram : suite lancée (editions.yml, reprise).")
        if clics:  # accuse de reception tout de suite (ne consomme pas le message)
            base = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
            for cq in clics:
                try:
                    _json(f"{base}/answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Reçu ✅"})
                except Exception:
                    pass
            try:
                _json(f"{base}/sendMessage", {"chat_id": os.environ["TELEGRAM_CHAT_ID"],
                                              "text": "✅ Clic reçu : je m'en occupe (environ 10 minutes)."})
            except Exception:
                pass
    if video:
        _json(f"https://api.github.com/repos/{depot}/actions/workflows/video.yml/dispatches", {"ref": "main"},
              entetes=gh, methode="POST")
        print("Vidéo reçue : traitement lancé (workflow video.yml).")
    if not (reprise or video):
        print("Rien de nouveau.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
