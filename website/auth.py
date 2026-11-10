"""Google sign-in for merchants (OAuth 2.0 "authorization code" flow).

How it works, step by step:
  1. /login sends the browser to Google with our client id and a random "state" value.
  2. The merchant signs in at Google. Google sends the browser back to /auth/callback with a code.
  3. Our server (never the browser) swaps that code for tokens, using the client SECRET.
     The secret lives only in the server's environment (.env), never in HTML or JavaScript.
  4. We ask Google who the person is (their id, email, name) and remember them in our database.
     The browser only gets a signed session cookie that says "this is user X".
  5. Google's access token expires after about an hour. Before calling the chat service we check
     it, and if it has expired we use the refresh token to get a new one. If Google refuses
     (for example the merchant removed access), the merchant is signed out and asked to sign in again.

Without Google keys (GOOGLE_CLIENT_ID empty) the site offers a clearly marked development sign-in,
so the team can run everything before the keys from Rupesh arrive. Turn it off with DEV_LOGIN=0.
"""
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REFRESH_MARGIN_S = 60


class SignInRequired(Exception):
    pass


class NotAllowed(Exception):
    pass


def allowed_emails(value: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in value.split(",") if part.strip())


class Users:
    """Website's own small database: who has signed in, and their Google tokens."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT, name TEXT, provider TEXT NOT NULL,
                access_token TEXT, refresh_token TEXT, expires_at REAL)""")

    def save(self, uid, email, name, provider, access_token=None, refresh_token=None, expires_at=None):
        with self.lock, self.db:
            old = self.get(uid)
            refresh_token = refresh_token or (old["refresh_token"] if old else None)  # Google sends it only sometimes
            self.db.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?)",
                            (uid, email, name, provider, access_token, refresh_token, expires_at))

    def get(self, uid):
        r = self.db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None

    def update_tokens(self, uid, access_token, expires_at):
        with self.lock, self.db:
            self.db.execute("UPDATE users SET access_token=?, expires_at=? WHERE id=?", (access_token, expires_at, uid))


class GoogleAuth:
    def __init__(self, client_id, client_secret, redirect_uri, http=None, allowed=frozenset()):
        self.client_id, self.client_secret, self.redirect_uri = client_id, client_secret, redirect_uri
        self.http = http or requests  # tests pass a fake here
        self.allowed = allowed

    def email_allowed(self, email: str) -> bool:
        if not self.allowed:
            return True
        return email.strip().lower() in self.allowed

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    def login_url(self, session):
        state = secrets.token_urlsafe(24)
        session["oauth_state"] = state
        q = {"client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
             "scope": "openid email profile", "access_type": "offline", "prompt": "consent", "state": state}
        return AUTH_URL + "?" + urllib.parse.urlencode(q)

    def finish(self, session, args, users):
        """Handle Google's redirect back. Returns the user id."""
        if not args.get("state") or args.get("state") != session.pop("oauth_state", None):
            raise SignInRequired("The sign-in link expired. Please try again.")
        if args.get("error") or not args.get("code"):
            raise SignInRequired("Google sign-in was cancelled.")
        tok = self.http.post(TOKEN_URL, data={
            "code": args["code"], "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code"}, timeout=10)
        if tok.status_code != 200:
            raise SignInRequired("Google didn't accept the sign-in. Please try again.")
        t = tok.json()
        info = self.http.get(USERINFO_URL, headers={"Authorization": f"Bearer {t['access_token']}"}, timeout=10).json()
        email = (info.get("email") or "").strip()
        if not self.email_allowed(email):
            raise NotAllowed()
        uid = "google:" + info["sub"]
        users.save(uid, email, info.get("name"), "google", t["access_token"], t.get("refresh_token"),
                   time.time() + int(t.get("expires_in", 3600)))
        return uid

    def ensure_fresh(self, user, users):
        """Refresh Google's access token if it has expired. Raises SignInRequired if that fails."""
        if user["provider"] != "google":
            return user
        if user["expires_at"] and user["expires_at"] - time.time() > REFRESH_MARGIN_S:
            return user
        if not user["refresh_token"]:
            raise SignInRequired("Your sign-in expired.")
        r = self.http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": user["refresh_token"], "grant_type": "refresh_token"}, timeout=10)
        if r.status_code != 200:
            raise SignInRequired("Your sign-in expired.")
        t = r.json()
        users.update_tokens(user["id"], t["access_token"], time.time() + int(t.get("expires_in", 3600)))
        return users.get(user["id"])
