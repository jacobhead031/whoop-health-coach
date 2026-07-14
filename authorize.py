"""One-time WHOOP OAuth flow: opens the browser, catches the redirect, saves tokens.json."""

import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from whoop_client import (
    AUTH_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    SCOPES,
    TOKEN_URL,
    save_tokens,
)


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise SystemExit("Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET in .env (copy .env.example).")

    state = secrets.token_urlsafe(16)  # WHOOP requires state, min 8 chars
    url = AUTH_URL + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
        }
    )

    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            result.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"All set - you can close this tab.")

        def log_message(self, *args):
            pass

    port = urlparse(REDIRECT_URI).port or 80
    server = HTTPServer(("localhost", port), Handler)
    print(f"Opening browser; waiting for WHOOP to redirect to {REDIRECT_URI} ...")
    webbrowser.open(url)
    while "code" not in result and "error" not in result:
        server.handle_request()
    server.server_close()

    if "error" in result:
        raise SystemExit(f"Authorization failed: {result['error']}")
    if result.get("state") != state:
        raise SystemExit("State mismatch - aborting.")

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    resp.raise_for_status()
    save_tokens(resp.json())
    print("Saved tokens.json. Try: python fetch_day.py")


if __name__ == "__main__":
    main()
