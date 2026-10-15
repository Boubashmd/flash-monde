"""Autorise Flash Monde a publier sur TA chaine YouTube. A lancer une seule fois, sur ton Mac :

    python3 outils/autoriser_youtube.py chemin/vers/client_secret.json

Le navigateur s'ouvre : connecte-toi avec le compte Google de la chaine et accepte.
Le script affiche ensuite les 3 valeurs a copier dans les Secrets GitHub.
Ne les colle jamais dans une conversation.
"""
import http.server
import json
import secrets
import sys
import urllib.parse
import webbrowser

import requests

# publier les videos, et lire leurs statistiques (vues, j'aime) pour le bilan de la semaine
PORTEE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    with open(sys.argv[1], encoding="utf-8") as f:
        brut = json.load(f)
    c = brut.get("installed") or brut.get("web") or {}
    client_id, client_secret = c.get("client_id"), c.get("client_secret")
    if not client_id or not client_secret:
        print("Fichier invalide : télécharge le JSON d'un identifiant OAuth de type « Application de bureau ».")
        return 1

    etat = secrets.token_urlsafe(16)
    recu = {}

    class Retour(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            recu.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>C'est bon, tu peux fermer cette page et revenir au Terminal.</h2>".encode())

        def log_message(self, *args):
            pass

    serveur = http.server.HTTPServer(("127.0.0.1", 0), Retour)
    retour = f"http://127.0.0.1:{serveur.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": retour, "response_type": "code", "scope": PORTEE,
        "access_type": "offline", "prompt": "consent", "state": etat,
    })
    print("Ouverture du navigateur. Si rien ne s'ouvre, copie ce lien dans ton navigateur :\n" + url)
    webbrowser.open(url)
    while "code" not in recu and "error" not in recu:
        serveur.handle_request()
    if recu.get("error") or recu.get("state") != etat:
        print(f"Autorisation refusée ou interrompue ({recu.get('error', 'état invalide')}).")
        return 1
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "code": recu["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": retour, "grant_type": "authorization_code",
    }, timeout=30)
    jeton = r.json()
    if "refresh_token" not in jeton:
        print(f"Pas de jeton reçu : {r.text[:200]}")
        return 1
    print("\nAutorisation réussie. Ajoute ces 3 secrets dans GitHub")
    print("(dépôt > Settings > Secrets and variables > Actions > New repository secret) :\n")
    print(f"YOUTUBE_CLIENT_ID      = {client_id}")
    print(f"YOUTUBE_CLIENT_SECRET  = {client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN  = {jeton['refresh_token']}")
    print("\nNe partage jamais ces valeurs, même avec Claude.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
