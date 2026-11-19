"""The website the merchant uses.

    python -m website.app        # http://localhost:5000

It does three jobs and nothing more:
  1. Google sign-in (website/auth.py), remembered with a signed cookie and refreshed when it expires
  2. the pages (templates/ and static/)
  3. passing chat messages and files to the chat service through the contracts (website/chat_client.py)

Point CHAT_SERVICE_URL at the in-repo mock (default) or at live copilot-svc.
"""
import os
import re
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from .auth import GoogleAuth, NotAllowed, SignInRequired, Users, allowed_emails
from .chat_client import ChatClient, ChatServiceDown

HERE = os.path.dirname(os.path.abspath(__file__))
ACCEPTED = (".pdf", ".jpg", ".jpeg", ".png")
MAX_UPLOAD_MB = 10
# Mock chat_service uses c_…; live ONB / copilot-svc uses app_… + 12 hex.
CID_RX = re.compile(r"^(c_[a-z0-9]{12}|app_[0-9a-f]{12})$")


def create_app(config=None):
    cfg = {
        "SECRET_KEY": os.environ.get("SECRET_KEY", ""),
        "GOOGLE_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "GOOGLE_REDIRECT_URI": os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/auth/callback"),
        "DEV_LOGIN": os.environ.get("DEV_LOGIN", ""),
        "GOOGLE_ALLOWED_EMAILS": os.environ.get("GOOGLE_ALLOWED_EMAILS", ""),
        # mock chat_service: http://127.0.0.1:5001
        # live copilot:     https://copilot-svc.onrender.com
        "CHAT_SERVICE_URL": os.environ.get("CHAT_SERVICE_URL", "http://127.0.0.1:5001"),
        "CHAT_SERVICE_TOKEN": os.environ.get("CHAT_SERVICE_TOKEN", ""),
        "USERS_DB": os.environ.get("WEBSITE_DB", os.path.join(HERE, "data", "website.db")),
        "COOKIE_SECURE": os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
        "CHAT_HTTP": None, "GOOGLE_HTTP": None,
    }
    cfg.update(config or {})
    app = Flask(__name__, template_folder="templates", static_folder="static")
    if not cfg["SECRET_KEY"]:
        print("WARNING: SECRET_KEY is not set; using a throwaway key (everyone is signed out on restart).")
        cfg["SECRET_KEY"] = os.urandom(32).hex()
    app.config.update(SECRET_KEY=cfg["SECRET_KEY"], PERMANENT_SESSION_LIFETIME=timedelta(days=30),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=cfg["COOKIE_SECURE"], MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
                      USERS_DB=cfg["USERS_DB"])

    google = GoogleAuth(cfg["GOOGLE_CLIENT_ID"], cfg["GOOGLE_CLIENT_SECRET"], cfg["GOOGLE_REDIRECT_URI"],
                         cfg["GOOGLE_HTTP"], allowed_emails(cfg["GOOGLE_ALLOWED_EMAILS"]))
    dev_login = cfg["DEV_LOGIN"] == "1" or (cfg["DEV_LOGIN"] == "" and not google.configured)
    users = Users(cfg["USERS_DB"])
    chat = ChatClient(cfg["CHAT_SERVICE_URL"], cfg["CHAT_SERVICE_TOKEN"], cfg["CHAT_HTTP"])

    # ------------------------------------------------------------ who is signed in
    def current_user():
        uid = session.get("uid")
        user = users.get(uid) if uid else None
        if not user or not google.email_allowed(user.get("email") or ""):
            if user:
                session.clear()
            return None
        try:
            return google.ensure_fresh(user, users)
        except SignInRequired:
            session.clear()
            return None

    def api_error(code, message, status):
        return jsonify({"error": {"code": code, "message": message}}), status

    def relay(status_body):
        status, body = status_body
        return jsonify(body), status

    def bearer_for(user):
        """Google users send Google's access token. Development sign-in uses the shared token."""
        if user.get("provider") == "google" and user.get("access_token"):
            return user["access_token"]
        return cfg["CHAT_SERVICE_TOKEN"]

    def api(fn):
        """Wrap a /api route: require sign-in, block cross-site posts, map chat-service outages."""
        def wrapper(*a, **kw):
            user = current_user()
            if not user:
                return api_error("sign_in_required", "Please sign in again.", 401)
            if request.method != "GET" and request.headers.get("X-IPE-Client") != "web":
                return api_error("forbidden", "Missing client header.", 403)
            if "cid" in kw and not CID_RX.match(kw["cid"]):
                return api_error("not_found", "No such conversation.", 404)
            try:
                return fn(user, *a, **kw)
            except ChatServiceDown:
                return api_error("chat_unavailable", "The chat service isn't reachable. Is it running?", 503)
        wrapper.__name__ = fn.__name__
        return wrapper

    # ------------------------------------------------------------ pages
    @app.get("/")
    @app.get("/c/<cid>")
    def home(cid=None):
        user = current_user()
        if not user:
            return render_template("signin.html", google=google.configured, dev_login=dev_login,
                                   error=session.pop("signin_error", None))
        return render_template("app.html", user=user, dev=user["provider"] == "dev")

    @app.get("/login")
    def login():
        if not google.configured:
            return redirect(url_for("home"))
        session.permanent = True
        return redirect(google.login_url(session))

    @app.get("/auth/callback")
    def callback():
        try:
            uid = google.finish(session, request.args, users)
        except NotAllowed:
            session.clear()
            return "This Google account is not allowed.", 403
        except (SignInRequired, KeyError, ValueError) as e:
            session["signin_error"] = str(e) if isinstance(e, SignInRequired) else "Google sign-in failed."
            return redirect(url_for("home"))
        session.clear()
        session.permanent = True
        session["uid"] = uid
        return redirect(url_for("home"))

    @app.post("/dev-login")
    def dev_signin():
        if not dev_login:
            return api_error("forbidden", "Development sign-in is turned off.", 403)
        email = (request.form.get("email") or "merchant@example.com").strip().lower()[:120]
        if not google.email_allowed(email):
            return api_error("forbidden", "This account is not allowed.", 403)
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            session["signin_error"] = "Enter an email address."
            return redirect(url_for("home"))
        uid = "dev:" + email
        users.save(uid, email, email.split("@")[0], "dev")
        session.clear()
        session.permanent = True
        session["uid"] = uid
        return redirect(url_for("home"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("home"))

    # ------------------------------------------------------------ API used by static/app.js
    @app.get("/api/me")
    @api
    def me(user):
        return jsonify({"name": user["name"], "email": user["email"], "dev": user["provider"] == "dev"})

    @app.get("/api/conversations")
    @api
    def list_conversations(user):
        return relay(chat.list_conversations(user["id"], bearer_for(user)))

    @app.post("/api/conversations")
    @api
    def create_conversation(user):
        body = request.get_json(silent=True) or {}
        application_id = str(body.get("application_id") or "").strip() or None
        return relay(chat.create_conversation(user["id"], bearer_for(user), application_id))

    @app.get("/api/conversations/<cid>")
    @api
    def load_conversation(user, cid):
        return relay(chat.load_conversation(user["id"], cid, bearer_for(user)))

    @app.post("/api/conversations/<cid>/messages")
    @api
    def send_message(user, cid):
        body = request.get_json(silent=True) or {}
        return relay(chat.send_message(
            user["id"],
            cid,
            body.get("text", ""),
            body.get("action"),
            body.get("client_message_id"),
            bearer_for(user),
        ))

    @app.post("/api/conversations/<cid>/files")
    @api
    def upload_file(user, cid):
        f = request.files.get("file")
        if not f or not f.filename:
            return api_error("no_file", "Choose a file.", 400)
        if not f.filename.lower().endswith(ACCEPTED):
            return api_error("unsupported_type", "Only PDF, JPG and PNG files are accepted.", 415)
        return relay(chat.upload_file(
            user["id"],
            cid,
            request.form.get("document", ""),
            f.filename,
            f.stream,
            f.mimetype or "application/octet-stream",
            bearer_for(user),
        ))

    @app.errorhandler(413)
    def too_big(_e):
        return api_error("file_too_large", f"Files can be up to {MAX_UPLOAD_MB} MB.", 413)

    return app


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(HERE), ".env"))
    except ImportError:
        pass
    port = int(os.environ.get("WEBSITE_PORT", 5000))
    print(f"Website on http://localhost:{port}")
    create_app().run(host="127.0.0.1", port=port)
