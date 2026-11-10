"""The website's side of the contracts: every call to the chat service goes through here.

Each call sends the shared service token and the signed-in merchant's id. The website never
decides anything about the application; it only passes messages and files along and shows
what comes back (web app requirements, "What the website does not do").
"""
import requests


class ChatServiceDown(Exception):
    pass


class ChatClient:
    def __init__(self, base_url, token, http=None):
        self.base = base_url.rstrip("/")
        self.token = token
        self.http = http or requests.Session()

    def _call(self, method, path, merchant_id, **kw):
        headers = {"Authorization": f"Bearer {self.token}", "X-Merchant-Id": merchant_id}
        try:
            r = self.http.request(method, self.base + path, headers=headers, timeout=60, **kw)
        except requests.RequestException as e:
            raise ChatServiceDown(str(e)) from e
        try:
            body = r.json()
        except ValueError:
            body = {"error": {"code": "bad_reply", "message": "The chat service sent an unreadable reply."}}
        return r.status_code, body

    # Contract helpers: create / list conversations
    def create_conversation(self, merchant_id):
        return self._call("POST", "/v1/conversations", merchant_id)

    def list_conversations(self, merchant_id):
        return self._call("GET", "/v1/conversations", merchant_id)

    # Contract 2: load the past conversation
    def load_conversation(self, merchant_id, cid):
        return self._call("GET", f"/v1/conversations/{cid}", merchant_id)

    # Contract 1: send a chat message
    def send_message(self, merchant_id, cid, text, action=None, client_message_id=None):
        body = {"text": text or "", "action": action}
        if client_message_id:
            body["client_message_id"] = client_message_id
        return self._call("POST", f"/v1/conversations/{cid}/messages", merchant_id, json=body)

    # Contract 3: upload a file
    def upload_file(self, merchant_id, cid, document, filename, stream, mimetype):
        return self._call("POST", f"/v1/conversations/{cid}/files", merchant_id,
                          data={"document": document}, files={"file": (filename, stream, mimetype)})
