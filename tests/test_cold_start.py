"""A sleeping Render service returns HTML or drops the connection. The website retries."""

import json

import pytest
import requests

from website.chat_client import ChatClient, ChatServiceDown, _WAKING


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if not isinstance(self._body, dict):
            raise ValueError("not json")
        return self._body


class FakeHttp:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("website.chat_client.time.sleep", lambda _seconds: None)


def test_an_html_startup_page_is_retried_until_json() -> None:
    http = FakeHttp([
        Response(502, "<html>waking</html>"),
        Response(201, {"conversation": {"id": "app_abc"}}),
    ])
    client = ChatClient("https://copilot.example", "token", http)

    status, body = client.create_conversation("google:1", bearer="access")

    assert http.calls == 2
    assert status == 201
    assert body["conversation"]["id"] == "app_abc"


def test_a_dropped_connection_is_retried() -> None:
    http = FakeHttp([
        requests.ConnectionError("asleep"),
        Response(200, {"conversations": []}),
    ])
    client = ChatClient("https://copilot.example", "token", http)

    status, body = client.list_conversations("google:1", bearer="access")

    assert status == 200
    assert body == {"conversations": []}


def test_a_json_error_is_not_retried() -> None:
    http = FakeHttp([Response(401, {"error": {"code": "unauthorized", "message": "Please sign in again."}})])
    client = ChatClient("https://copilot.example", "token", http)

    status, body = client.create_conversation("google:1", bearer="access")

    assert http.calls == 1
    assert status == 401
    assert body["error"]["code"] == "unauthorized"


def test_a_service_that_stays_down_says_it_is_waking() -> None:
    http = FakeHttp([Response(502, "<html>")] * 3)
    client = ChatClient("https://copilot.example", "token", http)

    status, body = client.create_conversation("google:1", bearer="access")

    assert http.calls == 3
    assert status == 503
    assert body == _WAKING


def test_a_connection_that_never_returns_is_unavailable() -> None:
    http = FakeHttp([requests.ConnectionError("asleep")] * 3)
    client = ChatClient("https://copilot.example", "token", http)

    with pytest.raises(ChatServiceDown):
        client.create_conversation("google:1", bearer="access")


def test_upload_bytes_can_be_sent_again(tmp_path) -> None:
    seen = []

    class UploadHttp(FakeHttp):
        def request(self, method, url, headers=None, timeout=None, **kw):
            handle = kw["files"]["file"][1]
            seen.append(handle.read())
            handle.seek(0)
            return super().request(method, url, headers=headers, timeout=timeout, **kw)

    http = UploadHttp([
        Response(502, "<html>"),
        Response(200, {"conversation": {"id": "app_abc"}}),
    ])
    client = ChatClient("https://copilot.example", "token", http)
    path = tmp_path / "proof.pdf"
    path.write_bytes(b"pdf")

    with path.open("rb") as handle:
        status, body = client.upload_file("google:1", "app_abc", "bank_proof", "proof.pdf", handle, "application/pdf")

    assert status == 200
    assert seen == [b"pdf", b"pdf"]
    assert body["conversation"]["id"] == "app_abc"
    json.dumps(body)
