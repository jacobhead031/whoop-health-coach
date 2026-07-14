"""Minimal WHOOP v2 API client: token storage, refresh, and paginated GETs."""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer/v2"
SCOPES = "read:recovery read:cycles read:sleep offline"
TOKENS_FILE = PROJECT_DIR / "tokens.json"

CLIENT_ID = os.environ.get("WHOOP_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("WHOOP_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("WHOOP_REDIRECT_URI", "http://localhost:8765/callback")


def save_tokens(payload):
    payload = dict(payload)
    payload["expires_at"] = time.time() + payload.get("expires_in", 3600)
    TOKENS_FILE.write_text(json.dumps(payload, indent=2))
    TOKENS_FILE.chmod(0o600)
    return payload


def get_access_token():
    if not TOKENS_FILE.exists():
        sys.exit("No tokens.json — run `python authorize.py` first.")
    tokens = json.loads(TOKENS_FILE.read_text())
    if time.time() < tokens.get("expires_at", 0) - 60:
        return tokens["access_token"]
    # WHOOP requires scope=offline on refresh, and rotates the refresh token
    # on every use — save_tokens must run before this token gets used.
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "offline",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return save_tokens(resp.json())["access_token"]


def api_get(path, params=None):
    resp = requests.get(
        f"{API_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_collection(path, start, end):
    """Fetch all records from a paginated collection endpoint within [start, end]."""
    records = []
    next_token = None
    while True:
        params = {"start": start, "end": end, "limit": 25}
        if next_token:
            params["nextToken"] = next_token
        page = api_get(path, params)
        records.extend(page.get("records", []))
        next_token = page.get("next_token")
        if not next_token:
            return records
