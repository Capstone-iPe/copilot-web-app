#!/usr/bin/env python3
"""FE Google sign-in + refresh for local tools. Prints one access token to stdout.

Uses the same GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET as the website.
For the loopback CLI path, set GOOGLE_CLI_REDIRECT_URI (and register it on the Google client).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = ROOT / ".fe-google.json"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
MARGIN_S = 60
DEFAULT_CLI_REDIRECT = "http://127.0.0.1:8765/callback"


def main() -> int:
    _load_dotenv(ROOT / ".env")
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    redirect = (
        os.environ.get("GOOGLE_CLI_REDIRECT_URI", "").strip()
        or DEFAULT_CLI_REDIRECT
    )
    if not client_id or not client_secret:
        print(
            "Need GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in copilot-web-app/.env.",
            file=sys.stderr,
        )
        return 1
    try:
        access = ensure_access(client_id, client_secret, redirect)
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(f"Google sign-in failed: {exc}", file=sys.stderr)
        return 1
    print(access)
    return 0


def ensure_access(client_id: str, client_secret: str, redirect: str) -> str:
    saved = _read_tokens()
    now = time.time()
    if saved.get("access_token") and float(saved.get("expires_at") or 0) > now + MARGIN_S:
        return str(saved["access_token"])
    refresh = str(saved.get("refresh_token") or "").strip()
    if refresh:
        try:
            return _refresh(client_id, client_secret, refresh, saved)
        except Exception as exc:  # noqa: BLE001
            print(f"Refresh failed ({exc}); opening browser…", file=sys.stderr)
    return _browser_sign_in(client_id, client_secret, redirect, saved)


def _refresh(client_id: str, client_secret: str, refresh: str, saved: dict) -> str:
    payload = _post_form(
        TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
    )
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise RuntimeError("no access_token")
    expires = int(payload.get("expires_in") or 3600)
    saved["access_token"] = access
    saved["refresh_token"] = payload.get("refresh_token") or refresh
    saved["expires_at"] = time.time() + expires
    _write_tokens(saved)
    print("refreshed Google access token", file=sys.stderr)
    return access


def _browser_sign_in(client_id: str, client_secret: str, redirect: str, saved: dict) -> str:
    parsed = urllib.parse.urlparse(redirect)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        raise RuntimeError(
            f"GOOGLE_CLI_REDIRECT_URI must be a loopback URL with a port, got {redirect!r}"
        )
    state = secrets.token_urlsafe(24)
    box: dict = {}
    ready = threading.Event()
    server = ThreadingHTTPServer(
        (parsed.hostname, parsed.port), _handler(box, ready, parsed.path or "/")
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    url = f"{AUTH_URL}?{query}"
    print("Opening the browser to sign in with Google.", file=sys.stderr)
    print(url, file=sys.stderr)
    webbrowser.open(url)
    if not ready.wait(timeout=300):
        server.shutdown()
        raise RuntimeError("timed out waiting for Google callback")
    server.shutdown()
    if box.get("error") or not box.get("code"):
        raise RuntimeError(box.get("error") or "no code from Google")
    if box.get("state") != state:
        raise RuntimeError("OAuth state mismatch")
    payload = _post_form(
        TOKEN_URL,
        {
            "code": box["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
    )
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise RuntimeError("no access_token from Google")
    info = _get_json(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
    email = (info.get("email") or "").strip()
    print(f"signed in as {email or '(unknown)'}", file=sys.stderr)
    expires = int(payload.get("expires_in") or 3600)
    saved = {
        "access_token": access,
        "refresh_token": payload.get("refresh_token") or saved.get("refresh_token"),
        "expires_at": time.time() + expires,
        "email": email,
    }
    _write_tokens(saved)
    return access


def _handler(box: dict, ready, path: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != path:
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(parsed.query)
            box.clear()
            box.update(
                {
                    "code": (query.get("code") or [""])[0],
                    "state": (query.get("state") or [None])[0],
                    "error": (query.get("error") or [None])[0],
                }
            )
            body = b"Signed in. You can close this window and return to the terminal."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            ready.set()

        def log_message(self, _format: str, *_args) -> None:
            return

    return Handler


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(detail or str(exc)) from exc


def _get_json(url: str, headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _read_tokens() -> dict:
    if not TOKEN_PATH.exists():
        return {}
    try:
        data = json.loads(TOKEN_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_tokens(data: dict) -> None:
    TOKEN_PATH.write_text(json.dumps(data, indent=2) + "\n")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
