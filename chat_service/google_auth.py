"""Ask Google whether a bearer string is still a valid access token."""

from __future__ import annotations

from dataclasses import dataclass

import requests

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    subject: str
    audience: str


def audiences(primary: str, raw: str = "") -> frozenset[str]:
    allowed = {primary.strip()} if primary.strip() else set()
    for part in raw.split(","):
        value = part.strip()
        if value:
            allowed.add(value)
    return frozenset(allowed)


def allowed_emails(raw: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


class GoogleTokenChecker:
    """Looks up a Google access token. Tests may pass a fake http client."""

    def __init__(self, allowed_audiences: frozenset[str], allowed: frozenset[str] = frozenset(), http=None, *, enforce: bool = False):
        self.allowed_audiences = allowed_audiences
        self.allowed = allowed
        self.http = http or requests
        self.enforce = enforce

    def inspect(self, token: str) -> GoogleIdentity | None:
        text = (token or "").strip()
        if not text or not self.allowed_audiences:
            return None
        try:
            info = self.http.get(TOKENINFO_URL, params={"access_token": text}, timeout=10)
            if info.status_code != 200:
                return None
            payload = info.json()
            audience = str(payload.get("aud") or payload.get("azp") or "").strip()
            if audience not in self.allowed_audiences:
                return None
            profile = self.http.get(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {text}"},
                timeout=10,
            )
            if profile.status_code != 200:
                return None
            body = profile.json()
        except (requests.RequestException, ValueError, TypeError):
            return None
        if body.get("email_verified") not in (True, "true"):
            return None
        email = body.get("email")
        subject = body.get("sub")
        if not isinstance(email, str) or "@" not in email:
            return None
        if not isinstance(subject, str) or not subject.strip():
            return None
        email = email.strip().lower()
        if self.enforce and self.allowed and email not in self.allowed:
            return None
        return GoogleIdentity(email=email, subject=subject.strip(), audience=audience)
