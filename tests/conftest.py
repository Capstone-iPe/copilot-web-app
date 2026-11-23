"""Shared test setup. pytest runs this automatically before the tests.

Nothing here needs a network, Google keys, or an API key: the website is wired straight into the
chat service in memory, and Google is replaced by a fake.
"""
import io
import json
import os
import sys

import jsonschema
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from chat_service.app import create_app as create_chat  # noqa: E402
from chat_service.google_auth import GoogleIdentity  # noqa: E402
from website.app import create_app as create_website  # noqa: E402

TOKEN = "test-token"
SCHEMA = json.load(open(os.path.join(ROOT, "contracts", "chat_service.schema.json"), encoding="utf-8"))
FILES = os.path.join(ROOT, "evaluation", "scenarios", "IPE-001", "files")


class FakeGoogleChecker:
    """Accepts the FakeGoogle access tokens used in website sign-in tests."""

    def inspect(self, token: str):
        if token in {"at1", "at2"}:
            return GoogleIdentity(email="asha@gmail.com", subject="123", audience="CID")
        return None


def check(obj, name):
    """Fail the test if obj doesn't match the named contract in contracts/chat_service.schema.json."""
    jsonschema.validate(obj, {"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]})


class FakeResponse:
    def __init__(self, r):
        self.status_code, self._r = r.status_code, r

    def json(self):
        return self._r.get_json()


class InMemoryHttp:
    """Lets the website's ChatClient call the chat service without a real network."""

    def __init__(self, client):
        self.client = client

    def request(self, method, url, headers=None, timeout=None, json=None, data=None, files=None):
        path = "/" + url.split("/", 3)[3]
        kw = {"headers": headers}
        if json is not None:
            kw["json"] = json
        if files:
            form = dict(data or {})
            for k, (name, stream, mime) in files.items():
                form[k] = (io.BytesIO(stream.read()), name, mime)
            kw["data"] = form
            kw["content_type"] = "multipart/form-data"
        return FakeResponse(self.client.open(path, method=method, **kw))


@pytest.fixture
def chat(tmp_path):
    app = create_chat({
        "DB": str(tmp_path / "chat.db"),
        "UPLOADS": str(tmp_path / "up"),
        "TOKEN": TOKEN,
        "GOOGLE_CHECKER": FakeGoogleChecker(),
        "TODAY": "2026-10-28",  # the date of the IPE-001 test files
    })
    app.testing = True
    return app


@pytest.fixture
def chat_client(chat):
    c = chat.test_client()
    c.H = {"Authorization": f"Bearer {TOKEN}", "X-Merchant-Id": "merchant-1"}
    return c


@pytest.fixture
def make_website(chat, tmp_path):
    def make(**extra):
        cfg = {"SECRET_KEY": "test", "USERS_DB": str(tmp_path / "web.db"), "CHAT_SERVICE_TOKEN": TOKEN,
               "CHAT_SERVICE_URL": "http://chat", "CHAT_HTTP": InMemoryHttp(chat.test_client()),
               "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}
        cfg.update(extra)
        app = create_website(cfg)
        app.testing = True
        return app
    return make


class Conversation:
    """Small helper to talk to the chat service in tests."""

    def __init__(self, client):
        self.c = client
        r = client.post("/v1/conversations", headers=client.H)
        assert r.status_code == 201, r.get_json()
        check(r.get_json(), "CreateConversationResponse")
        self.view = r.get_json()["conversation"]
        self.id = self.view["id"]

    def say(self, text, action=None, cmid=None):
        body = {"text": text, "action": action}
        if cmid:
            body["client_message_id"] = cmid
        r = self.c.post(f"/v1/conversations/{self.id}/messages", headers=self.c.H, json=body)
        assert r.status_code == 200, r.get_json()
        check(r.get_json(), "SendMessageResponse")
        self.view = r.get_json()["conversation"]
        return r.get_json()["reply"]

    def upload(self, document, filename, folder=FILES):
        with open(os.path.join(folder, filename), "rb") as f:
            r = self.c.post(f"/v1/conversations/{self.id}/files", headers=self.c.H,
                            data={"document": document, "file": (f, filename)}, content_type="multipart/form-data")
        assert r.status_code == 200, r.get_json()
        check(r.get_json(), "UploadFileResponse")
        self.view = r.get_json()["conversation"]
        return r.get_json()["reply"]

    @property
    def asking(self):
        return self.view["prompt"]["field"]

    def pills(self):
        return [p["label"] for p in self.view["prompt"]["pills"]]
