"""The website's side of the contracts: every call to the chat service goes through here.

Each call sends the merchant's Google access token when they signed in with Google, or the
shared development token for the development sign-in. The website never decides anything about
the application; it only passes messages and files along and shows what comes back.

A free Render service sleeps on its own. The first call often gets an HTML start-up page, or
the connection drops, while copilot and ONB are still booting. Retry those. A JSON error is
returned as itself.
"""
import time

import requests

# Three attempts stay inside the website process timeout (serve.py uses 180s).
# 50s covers the copilot's own retries while ONB or the corpus is still booting.
_TIMEOUT = 50
_DELAYS = (0, 8, 15)
_RETRY_STATUSES = {502, 503, 504}
_WAKING = {
    "error": {
        "code": "chat_unavailable",
        "message": "The chat service is waking up. Try again in a moment.",
    }
}


class ChatServiceDown(Exception):
    pass


class ChatClient:
    def __init__(self, base_url, token, http=None):
        self.base = base_url.rstrip("/")
        self.token = token
        self.http = http or requests.Session()

    def _call(self, method, path, merchant_id, bearer=None, **kw):
        headers = {
            "Authorization": f"Bearer {bearer or self.token}",
            "X-Merchant-Id": merchant_id,
        }
        last_error = None
        saw_response = False
        for delay in _DELAYS:
            if delay:
                time.sleep(delay)
            _rewind(kw.get("files"))
            try:
                response = self.http.request(
                    method, self.base + path, headers=headers, timeout=_TIMEOUT, **kw
                )
            except requests.RequestException as exc:
                last_error = exc
                continue
            saw_response = True
            if response.status_code in _RETRY_STATUSES:
                continue
            body = _json(response)
            if body is None:
                continue
            return response.status_code, body
        if last_error is not None and not saw_response:
            raise ChatServiceDown(str(last_error)) from last_error
        return 503, _WAKING

    def create_conversation(self, merchant_id, bearer=None, application_id=None):
        extra = {"json": {"application_id": application_id}} if application_id else {}
        return self._call("POST", "/v1/conversations", merchant_id, bearer, **extra)

    def list_conversations(self, merchant_id, bearer=None):
        return self._call("GET", "/v1/conversations", merchant_id, bearer)

    def load_conversation(self, merchant_id, cid, bearer=None):
        return self._call("GET", f"/v1/conversations/{cid}", merchant_id, bearer)

    def send_message(self, merchant_id, cid, text, action=None, client_message_id=None, bearer=None):
        body = {"text": text or "", "action": action}
        if client_message_id:
            body["client_message_id"] = client_message_id
        return self._call("POST", f"/v1/conversations/{cid}/messages", merchant_id, bearer, json=body)

    def upload_file(self, merchant_id, cid, document, filename, stream, mimetype, bearer=None):
        return self._call(
            "POST",
            f"/v1/conversations/{cid}/files",
            merchant_id,
            bearer,
            data={"document": document},
            files={"file": (filename, stream, mimetype)},
        )


def _json(response):
    try:
        return response.json()
    except ValueError:
        return None


def _rewind(files) -> None:
    if not files:
        return
    for value in files.values():
        stream = value[1] if isinstance(value, tuple) and len(value) > 1 else None
        if hasattr(stream, "seek"):
            stream.seek(0)
