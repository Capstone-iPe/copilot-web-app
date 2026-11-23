"""The website's side of the contracts: every call to the chat service goes through here.

Each call sends the merchant's Google access token when they signed in with Google, or the
shared development token for the development sign-in. The website never decides anything about
the application; it only passes messages and files along and shows what comes back.

A free Render service sleeps on its own. The first call often gets an HTML start-up page, or
the connection drops, while copilot and ONB are still booting. Those are retried.

A JSON error from the copilot is the copilot's answer and is returned as itself, so the page can
show the right message (corpus_unavailable, unauthorized, ...). Only "still waking" codes are
retried, and only for calls that are safe to repeat: reads, and writes that carry a
client_message_id (the copilot replays the stored reply instead of running the turn twice).
"""
import time

import requests

# Three attempts stay inside the website process timeout (serve.py uses 180s).
# 50s covers the copilot's own retries while ONB or the corpus is still booting.
_TIMEOUT = 50
_DELAYS = (0, 8, 15)
# JSON error codes that mean "a service behind the copilot is still starting; the same request
# will work in a moment". Everything else (corpus_unavailable, unauthorized, not_found, ...) is final.
_WAKING_CODES = {"onb_unavailable", "history_unavailable", "conversation_busy"}
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

    def _call(self, method, path, merchant_id, bearer=None, repeatable=None, **kw):
        headers = {
            "Authorization": f"Bearer {bearer or self.token}",
            "X-Merchant-Id": merchant_id,
        }
        if repeatable is None:
            repeatable = method == "GET"
        last_error = None
        last_json = None
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
            body = _json(response)
            if body is None:
                # An HTML start-up page or an empty reply: the service is not up yet.
                continue
            if repeatable and _error_code(body) in _WAKING_CODES:
                last_json = (response.status_code, body)
                continue
            return response.status_code, body
        if last_json is not None:
            return last_json
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
        return self._call("POST", f"/v1/conversations/{cid}/messages", merchant_id, bearer,
                          repeatable=bool(client_message_id), json=body)

    def upload_file(self, merchant_id, cid, document, filename, stream, mimetype, bearer=None,
                    client_message_id=None):
        data = {"document": document}
        if client_message_id:
            data["client_message_id"] = client_message_id
        return self._call(
            "POST",
            f"/v1/conversations/{cid}/files",
            merchant_id,
            bearer,
            repeatable=bool(client_message_id),
            data=data,
            files={"file": (filename, stream, mimetype)},
        )


def _error_code(body):
    error = body.get("error") if isinstance(body, dict) else None
    return error.get("code") if isinstance(error, dict) else None


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
