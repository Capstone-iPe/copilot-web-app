"""The website's side of the contracts: every call to the chat service goes through here.

Each call sends the merchant's Google access token when they signed in with Google, or the
shared development token for the development sign-in. The website never decides anything about
the application; it only passes messages and files along and shows what comes back.
"""
import requests


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
        try:
            r = self.http.request(method, self.base + path, headers=headers, timeout=60, **kw)
        except requests.RequestException as e:
            raise ChatServiceDown(str(e)) from e
        try:
            body = r.json()
        except ValueError:
            body = {"error": {"code": "bad_reply", "message": "The chat service sent an unreadable reply."}}
        return r.status_code, body

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
