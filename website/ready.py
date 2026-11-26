"""The sign-in page calls the health URLs that already exist.

A sleeping Render service answers that call with an HTML page. JSON means the service is up.
The same rule the chat client uses for a message.
"""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

_LOCAL = {"localhost", "127.0.0.1", "::1"}


def should_gate(chat_url: str, flag: str) -> bool:
    choice = (flag or "").strip().lower()
    if choice in {"0", "false", "off", "no"}:
        return False
    if choice in {"1", "true", "on", "yes"}:
        return True
    host = (urlparse(chat_url).hostname or "").lower()
    return host not in _LOCAL


def classify(response) -> str:
    """JSON from the service is up. HTML, or anything that is not JSON, is Render still starting it."""
    try:
        body = response.json()
    except ValueError:
        return "waking"
    if getattr(response, "status_code", 0) != 200 or not isinstance(body, dict):
        return "waking"
    if body.get("index") == "loading":
        return "waking"
    if body.get("index") == "error":
        return "down"
    if body.get("ok") is True or str(body.get("status") or "").lower() == "ok":
        return "up"
    return "waking"


def _one(http, url: str, timeout: float) -> str:
    try:
        return classify(http.get(url, timeout=timeout))
    except requests.RequestException:
        return "waking"


def awake() -> dict:
    services = [
        {"id": "copilot", "name": "Copilot", "state": "up"},
        {"id": "onb", "name": "ONB", "state": "up"},
        {"id": "corpus", "name": "Policy corpus", "state": "up"},
    ]
    return {"ready": True, "services": services}


def snapshot(checks: list[tuple[str, str, str]], http, timeout: float = 8) -> dict:
    """Ask each health URL once. They run together, so a sleeping service starts waking immediately."""
    with ThreadPoolExecutor(max_workers=max(1, len(checks))) as pool:
        states = list(pool.map(lambda check: _one(http, check[2], timeout), checks))
    services = [
        {"id": check[0], "name": check[1], "state": state}
        for check, state in zip(checks, states)
    ]
    return {"ready": all(item["state"] == "up" for item in services), "services": services}
