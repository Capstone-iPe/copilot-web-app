"""A sleeping Render service is an HTML page. JSON means that service is up."""

import requests

from website.ready import classify, should_gate, snapshot


class Reply:
    def __init__(self, status, body=None, html=False):
        self.status_code = status
        self._body = body
        self._html = html

    def json(self):
        if self._html:
            raise ValueError("html")
        return self._body


class Http:
    def __init__(self, replies):
        self.replies = dict(replies)
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        reply = self.replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_html_is_waking_and_json_ok_is_up() -> None:
    assert classify(Reply(200, html=True)) == "waking"
    assert classify(Reply(502, html=True)) == "waking"
    assert classify(Reply(200, {"ok": True})) == "up"
    assert classify(Reply(200, {"status": "ok"})) == "up"
    assert classify(Reply(200, {"status": "ok", "index": "loading"})) == "waking"
    assert classify(Reply(200, {"status": "ok", "index": "error"})) == "down"


def test_a_local_chat_does_not_wait_and_a_live_one_does() -> None:
    assert should_gate("http://127.0.0.1:5001", "") is False
    assert should_gate("https://copilot-svc.onrender.com", "") is True
    assert should_gate("https://copilot-svc.onrender.com", "0") is False


def test_each_health_url_is_asked_and_html_keeps_sign_in_closed() -> None:
    http = Http({
        "https://copilot.example/v1/health": Reply(200, html=True),
        "https://onb.example/health": Reply(200, {"status": "ok"}),
        "https://corpus.example/health": requests.ConnectionError("asleep"),
    })
    body = snapshot([
        ("copilot", "Copilot", "https://copilot.example/v1/health"),
        ("onb", "ONB", "https://onb.example/health"),
        ("corpus", "Policy corpus", "https://corpus.example/health"),
    ], http)

    assert body["ready"] is False
    states = {item["id"]: item["state"] for item in body["services"]}
    assert states == {"web": "up", "copilot": "waking", "onb": "up", "corpus": "waking"}
    assert set(http.urls) == {
        "https://copilot.example/v1/health",
        "https://onb.example/health",
        "https://corpus.example/health",
    }


def test_the_sign_in_page_hides_google_until_health_is_json(make_website) -> None:
    http = Http({
        "https://copilot.example/v1/health": Reply(200, html=True),
        "https://onb.example/health": Reply(200, {"status": "ok"}),
        "https://corpus.example/health": Reply(200, {"status": "ok", "index": "ready"}),
    })
    app = make_website(
        WAKE_CHECK="1",
        READY_HTTP=http,
        GOOGLE_CLIENT_ID="CID",
        GOOGLE_CLIENT_SECRET="SECRET",
        CHAT_SERVICE_URL="https://copilot.example",
        ONB_HEALTH_URL="https://onb.example/health",
        CORPUS_HEALTH_URL="https://corpus.example/health",
    )
    page = app.test_client().get("/").get_data(as_text=True)
    assert 'id="signin-actions" hidden' in page
    assert "Sign in with Google" in page
    body = app.test_client().get("/ready").get_json()
    assert body["ready"] is False
    assert {item["id"]: item["state"] for item in body["services"]}["copilot"] == "waking"
