"""The website: sign-in (development and Google), and passing calls to the chat service."""
import io
import time
import urllib.parse

from conftest import FILES


def dev_signed_in(app):
    c = app.test_client()
    r = c.post("/dev-login", data={"email": "asha@example.com"})
    assert r.status_code == 302
    return c


def test_signin_page_and_api_needs_sign_in(make_website):
    c = make_website().test_client()
    page = c.get("/").get_data(as_text=True)
    assert "Continue as test merchant" in page and "Sign in with Google" not in page
    assert c.get("/api/conversations").status_code == 401


def test_conversation_through_the_website(make_website):
    c = dev_signed_in(make_website())
    assert "Your shops" in c.get("/").get_data(as_text=True)
    H = {"X-IPE-Client": "web"}
    conv = c.post("/api/conversations", headers=H).get_json()["conversation"]
    r = c.post(f"/api/conversations/{conv['id']}/messages", headers=H, json={"text": "Lotus Tea House"})
    assert r.status_code == 200 and r.get_json()["conversation"]["shop_name"] == "Lotus Tea House"
    # a refresh loads the same conversation
    again = c.get(f"/api/conversations/{conv['id']}").get_json()["conversation"]
    assert len(again["messages"]) == 3
    assert c.get("/api/conversations").get_json()["conversations"][0]["id"] == conv["id"]
    # the page at /c/<id> is the app
    assert c.get(f"/c/{conv['id']}").status_code == 200


def test_uploads_are_checked_and_passed_on(make_website):
    c = dev_signed_in(make_website())
    H = {"X-IPE-Client": "web"}
    cid = c.post("/api/conversations", headers=H).get_json()["conversation"]["id"]
    for t in ["Lotus Tea House", "Pune", "Restaurant", "₹12,00,000", "UPI", "No"]:
        c.post(f"/api/conversations/{cid}/messages", headers=H, json={"text": t})
    r = c.post(f"/api/conversations/{cid}/files", headers=H, content_type="multipart/form-data",
               data={"document": "business_registration", "file": (io.BytesIO(b"x"), "scan.docx")})
    assert r.status_code == 415
    with open(f"{FILES}/business_registration.pdf", "rb") as f:
        r = c.post(f"/api/conversations/{cid}/files", headers=H, content_type="multipart/form-data",
                   data={"document": "business_registration", "file": (f, "business_registration.pdf")})
    assert r.status_code == 200 and r.get_json()["conversation"]["prompt"]["field"] == "owner_id"


def test_cross_site_posts_and_other_merchants_are_blocked(make_website):
    app = make_website()
    a = dev_signed_in(app)
    cid = a.post("/api/conversations", headers={"X-IPE-Client": "web"}).get_json()["conversation"]["id"]
    assert a.post(f"/api/conversations/{cid}/messages", json={"text": "hi"}).status_code == 403   # no client header
    b = app.test_client()
    b.post("/dev-login", data={"email": "someone@example.com"})
    assert b.get(f"/api/conversations/{cid}").status_code == 404


class FakeGoogle:
    """Stands in for Google's servers."""

    def __init__(self):
        self.calls, self.refresh_ok = [], True

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    def post(self, url, data=None, timeout=None):
        self.calls.append(data["grant_type"])
        assert data["client_secret"] == "SECRET"          # the secret is only ever sent server-to-server
        if data["grant_type"] == "authorization_code":
            return self.R(200, {"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600})
        if not self.refresh_ok:
            return self.R(400, {"error": "invalid_grant"})
        return self.R(200, {"access_token": "at2", "expires_in": 3600})

    def get(self, url, headers=None, timeout=None):
        return self.R(200, {"sub": "123", "email": "asha@gmail.com", "name": "Asha"})


def test_google_sign_in_refresh_and_expiry(make_website):
    g = FakeGoogle()
    app = make_website(GOOGLE_CLIENT_ID="CID", GOOGLE_CLIENT_SECRET="SECRET", GOOGLE_HTTP=g)
    c = app.test_client()
    page = c.get("/").get_data(as_text=True)
    assert "Sign in with Google" in page and "Continue as test merchant" not in page and "SECRET" not in page
    r = c.get("/login")
    url = urllib.parse.urlparse(r.headers["Location"])
    q = urllib.parse.parse_qs(url.query)
    assert url.netloc == "accounts.google.com" and q["client_id"] == ["CID"] and "client_secret" not in q
    assert c.get("/auth/callback?code=abc&state=wrong").status_code == 302      # bad state is refused
    assert c.get("/api/me").status_code == 401
    c.get("/login")
    with c.session_transaction() as s:
        state = s["oauth_state"]
    c.get(f"/auth/callback?code=abc&state={state}")
    assert c.get("/api/me").get_json()["email"] == "asha@gmail.com"
    # the access token expires: the site refreshes it
    _expire_tokens(app)
    assert c.get("/api/me").status_code == 200 and g.calls[-1] == "refresh_token"
    # Google refuses the refresh: signed out
    _expire_tokens(app)
    g.refresh_ok = False
    assert c.get("/api/me").status_code == 401


def _expire_tokens(app):
    import sqlite3
    path = app.config["USERS_DB"]
    with sqlite3.connect(path) as db:
        db.execute("UPDATE users SET expires_at=?", (time.time() - 10,))
