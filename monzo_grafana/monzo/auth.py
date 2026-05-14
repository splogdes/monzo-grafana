"""Monzo OAuth: token storage, refresh, and the one-time browser flow."""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import urllib.parse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import requests

from ..config import Config

log = logging.getLogger(__name__)

MONZO_AUTH_BASE = "https://auth.monzo.com/"
MONZO_TOKEN_URL = "https://api.monzo.com/oauth2/token"

HTTP_TIMEOUT_SECONDS = 15
TOKEN_REFRESH_GRACE = timedelta(minutes=5)
TOKEN_FILE_MODE = 0o600


def load_tokens(path: Path) -> dict[str, Any]:
    try:
        with path.open() as f:
            return json.load(f)  # type: ignore[no-any-return]
    except FileNotFoundError:
        return {}


def save_tokens(path: Path, tokens: dict[str, Any]) -> None:
    with path.open("w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(path, TOKEN_FILE_MODE)
    log.info("Tokens saved to %s", path)


def refresh_access_token(cfg: Config, tokens: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(
        MONZO_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "refresh_token": tokens["refresh_token"],
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    new_tokens: dict[str, Any] = resp.json()
    new_tokens["obtained_at"] = datetime.now(UTC).isoformat()
    save_tokens(cfg.token_file, new_tokens)
    log.info("Access token refreshed")
    return new_tokens


def get_valid_token(cfg: Config) -> str:
    tokens = load_tokens(cfg.token_file)
    if not tokens:
        sys.exit("No tokens found — run:  python poller.py auth")

    obtained = datetime.fromisoformat(
        tokens.get("obtained_at", "2000-01-01T00:00:00+00:00")
    )
    expires_in = int(tokens.get("expires_in", 0))
    expiry = obtained + timedelta(seconds=expires_in) - TOKEN_REFRESH_GRACE
    if datetime.now(UTC) >= expiry:
        log.info("Token expiring soon, refreshing…")
        tokens = refresh_access_token(cfg, tokens)

    return str(tokens["access_token"])


class _CallbackHandler(BaseHTTPRequestHandler):
    captured_code: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)
            _CallbackHandler.captured_code = params.get("code", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h1>Authorised! You can close this tab.</h1>")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args: Any) -> None:
        pass


def do_auth(cfg: Config) -> None:
    state = secrets.token_urlsafe(16)
    auth_url = MONZO_AUTH_BASE + "?" + urllib.parse.urlencode({
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "state": state,
    })

    print(f"\nOpen this URL in your browser:\n\n  {auth_url}\n")
    print("Monzo will send you a magic link by email.")
    print("Click it, approve in the Monzo app, then return here.\n")

    parsed = urllib.parse.urlparse(cfg.redirect_uri)
    port = parsed.port or 7923
    # MONZO_AUTH_BIND_ALL lets the container expose 0.0.0.0 for the OAuth
    # callback when Monzo redirects back through a published host port.
    bind = "0.0.0.0" if os.environ.get("MONZO_AUTH_BIND_ALL") else "localhost"
    server = HTTPServer((bind, port), _CallbackHandler)
    print(f"Waiting for OAuth callback on port {port}…")

    while _CallbackHandler.captured_code is None:
        server.handle_request()
    server.server_close()

    code = _CallbackHandler.captured_code
    resp = requests.post(
        MONZO_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "redirect_uri": cfg.redirect_uri,
            "code": code,
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    tokens: dict[str, Any] = resp.json()
    tokens["obtained_at"] = datetime.now(UTC).isoformat()
    save_tokens(cfg.token_file, tokens)
    print(f"\nAuth complete — tokens saved to {cfg.token_file}")
