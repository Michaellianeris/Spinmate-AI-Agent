"""One-time helper: gets a Gmail API refresh token for sending the welcome email over HTTPS.

Run it on your computer (plain Python 3, no packages needed), not in the container:

    python3 app/gmail_auth.py

It asks for the OAuth client id and secret (Google Cloud > APIs & Services > Credentials > Desktop app),
opens Google's consent page, and prints the GMAIL_* lines to paste into .env.
"""
import getpass
import http.server
import json
import os
import secrets
import ssl
import sys
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/gmail.send"      # send only: no access to your inbox
PORT = int(os.environ.get("GMAIL_AUTH_PORT", "8765"))


def main():
    client_id = os.environ.get("GMAIL_CLIENT_ID") or input("OAuth client ID: ").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET") or getpass.getpass("OAuth client secret: ").strip()
    redirect = f"http://127.0.0.1:{PORT}/"
    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            if ok:
                got["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h2>SpinMate: done, you can close this tab.</h2>" if ok else
                              f"<h2>SpinMate: no code received ({q.get('error', ['unknown'])[0]}).</h2>").encode())

        def log_message(self, *a):
            pass

    print("\nOpen this address in your browser and allow \"Send email on your behalf\":\n\n" + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as srv:
        while "code" not in got:
            srv.handle_request()

    body = urllib.parse.urlencode({"code": got["code"], "client_id": client_id, "client_secret": client_secret,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", body),
                                timeout=20, context=ssl.create_default_context()) as r:
        tok = json.loads(r.read())
    if "refresh_token" not in tok:
        sys.exit("Google returned no refresh token. Remove SpinMate's access at myaccount.google.com/permissions "
                 "and run this again.")
    print("Add these lines to .env, then run: docker compose up -d --force-recreate\n")
    print(f"GMAIL_CLIENT_ID={client_id}\nGMAIL_CLIENT_SECRET={client_secret}\nGMAIL_REFRESH_TOKEN={tok['refresh_token']}")


if __name__ == "__main__":
    main()
