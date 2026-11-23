"""The chat service: holds every conversation and runs the agent.

It implements the contracts in contracts/CONTRACTS.md. The website calls it with the merchant's
Google access token when Google sign-in is on, or with CHAT_SERVICE_TOKEN for development
sign-in. Every Google bearer is checked with Google before the request is handled.

    python -m chat_service.app        # listens on http://127.0.0.1:5001

Which agent answers is set by CHAT_AGENT (module:Class). The default is the rule-based stand-in,
because the chat model hasn't been chosen yet.
"""
import datetime as dt
import hmac
import importlib
import os
import threading
import uuid

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from .google_auth import GoogleTokenChecker, allowed_emails, audiences
from .contract import message_id, public_result
from .onboarding_client import OnboardingClient
from .store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DOCUMENTS = {"business_registration", "owner_id", "bank_proof", "shopfront_photo", "dpa_addendum"}
ACCEPTED = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_UPLOAD_MB = 10


def error(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def create_app(config=None):
    cfg = {
        "TOKEN": os.environ.get("CHAT_SERVICE_TOKEN", ""),
        "GOOGLE_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_ALLOWED_AUDIENCES": os.environ.get("GOOGLE_ALLOWED_AUDIENCES", ""),
        "GOOGLE_ALLOWED_EMAILS": os.environ.get("GOOGLE_ALLOWED_EMAILS", ""),
        "GOOGLE_HTTP": None,
        "GOOGLE_CHECKER": None,
        "DB": os.environ.get("CHAT_DB", os.path.join(HERE, "data", "chat.db")),
        "UPLOADS": os.environ.get("CHAT_UPLOADS", os.path.join(HERE, "data", "uploads")),
        "AGENT": os.environ.get("CHAT_AGENT", "chat_service.agent.rules_agent:RulesAgent"),
        "POLICY_DIR": os.environ.get("POLICY_DIR", os.path.join(REPO, "evaluation", "policy")),
        "TODAY": None,  # tests pin the date so document-age checks don't drift
    }
    cfg.update(config or {})
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    store = Store(cfg["DB"])
    onboarding = OnboardingClient(store)
    locks = {}
    google_checker = cfg["GOOGLE_CHECKER"]
    if google_checker is None and cfg["GOOGLE_CLIENT_ID"].strip():
        google_checker = GoogleTokenChecker(
            audiences(cfg["GOOGLE_CLIENT_ID"], cfg["GOOGLE_ALLOWED_AUDIENCES"]),
            allowed_emails(cfg["GOOGLE_ALLOWED_EMAILS"]),
            cfg["GOOGLE_HTTP"],
        )

    def make_agent():
        mod, cls = cfg["AGENT"].split(":")
        return getattr(importlib.import_module(mod), cls)()

    def load_agent(row):
        agent = make_agent()
        agent.set_state(row["agent_state"])
        return agent

    def view(row, agent=None):
        agent = agent or load_agent(row)
        v = agent.view()
        return {"id": row["id"], "shop_name": v["shop_name"], "status": v["status"], "review_id": v["review_id"],
                "steps": v["steps"], "messages": store.messages(row["id"]), "prompt": v["prompt"], "result": public_result(v["result"]),
                "created_at": row["created_at"], "updated_at": store.get_conversation(row["id"])["updated_at"]}

    def bearer_value(header: str) -> str:
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return ""

    # ------------------------------------------------------------ guards
    @app.before_request
    def check_caller():
        if request.path == "/v1/health":
            return None
        auth = request.headers.get("Authorization", "")
        token = bearer_value(auth)
        merchant = request.headers.get("X-Merchant-Id", "").strip()
        if not merchant:
            return error("no_merchant", "X-Merchant-Id header is required.", 400)
        if google_checker is not None:
            identity = google_checker.inspect(token)
            if identity is not None:
                expected = "google:" + identity.subject
                if merchant != expected:
                    return error("unauthorized", "Merchant id does not match the Google sign-in.", 401)
                return None
        service = cfg["TOKEN"]
        if service and hmac.compare_digest(auth, f"Bearer {service}"):
            return None
        return error("unauthorized", "Missing or wrong bearer token.", 401)

    def own(cid):
        row = store.get_conversation(cid)
        if not row or row["merchant_id"] != request.headers["X-Merchant-Id"]:
            return None
        return row

    @app.errorhandler(413)
    def too_big(_e):
        return error("file_too_large", f"Files can be up to {MAX_UPLOAD_MB} MB.", 413)

    # ------------------------------------------------------------ endpoints
    @app.get("/v1/health")
    def health():
        return jsonify({"ok": True, "agent": cfg["AGENT"]})

    @app.post("/v1/conversations")
    def create_conversation():
        body = request.get_json(silent=True) or {}
        if body.get("application_id"):                  # reopen one of this merchant's conversations
            row = own(str(body["application_id"]))
            if not row:
                return error("not_found", "No such conversation.", 404)
            return jsonify({"conversation": view(row)}), 200
        cid = "c_" + uuid.uuid4().hex[:12]
        agent = make_agent()
        agent.start({"conversation_id": cid, "policy_dir": cfg["POLICY_DIR"], "today": cfg["TODAY"] or dt.date.today().isoformat()})
        first = agent.respond({"text": "", "attachments": [], "action": None})
        store.create_conversation(cid, request.headers["X-Merchant-Id"], agent.get_state())
        store.add_message(cid, "assistant", first.get("text", ""), card=first.get("card"))
        return jsonify({"conversation": view(store.get_conversation(cid), agent)}), 201

    @app.get("/v1/conversations")
    def list_conversations():
        out = []
        for row in store.list_conversations(request.headers["X-Merchant-Id"]):
            v = load_agent(row).view()
            out.append({"id": row["id"], "shop_name": v["shop_name"], "status": v["status"],
                        "review_id": v["review_id"], "updated_at": row["updated_at"]})
        return jsonify({"conversations": out})

    @app.get("/v1/conversations/<cid>")
    def load_conversation(cid):
        row = own(cid)
        if not row:
            return error("not_found", "No such conversation.", 404)
        return jsonify({"conversation": view(row)})

    def run_turn(row, message, merchant_text, attachments_public, client_message_id=None):
        cid = row["id"]
        with locks.setdefault(cid, threading.Lock()):
            row = store.get_conversation(cid)          # re-read inside the lock
            agent = load_agent(row)
            store.add_message(cid, "merchant", merchant_text, attachments_public, client_message_id=client_message_id)
            reply = agent.respond(message) or {}
            onboarding.handle(cid, reply.get("actions"))
            store.save_state(cid, agent.get_state())
            mid = store.add_message(cid, "assistant", reply.get("text", ""), card=reply.get("card"))
        row = store.get_conversation(cid)
        conv = view(row, agent)
        return jsonify({"conversation": conv, "reply": next(m for m in conv["messages"] if m["id"] == message_id(mid))})

    @app.post("/v1/conversations/<cid>/messages")
    def send_message(cid):
        row = own(cid)
        if not row:
            return error("not_found", "No such conversation.", 404)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return error("bad_request", "Send a JSON body.", 400)
        text = body.get("text") or ""
        action = body.get("action")
        if not isinstance(text, str) or len(text) > 2000 or action not in (None, "submit"):
            return error("bad_request", "text must be a string of at most 2000 characters; action must be 'submit' or null.", 400)
        if not text.strip() and action != "submit":
            return error("empty_message", "Type a message.", 400)
        cmid = body.get("client_message_id")
        if cmid:
            prev = store.reply_after(cid, cmid)
            if prev:                                    # a retry of a message we already answered
                conv = view(row)
                return jsonify({"conversation": conv, "reply": next(m for m in conv["messages"] if m["id"] == message_id(prev))})
        msg = {"text": text, "attachments": [], "action": action}
        return run_turn(row, msg, text or "Submit application", [], cmid)

    @app.post("/v1/conversations/<cid>/files")
    def upload_file(cid):
        row = own(cid)
        if not row:
            return error("not_found", "No such conversation.", 404)
        document = request.form.get("document", "")
        f = request.files.get("file")
        if document not in DOCUMENTS:
            return error("bad_document", "document must be one of: " + ", ".join(sorted(DOCUMENTS)), 400)
        if not f or not f.filename:
            return error("no_file", "Attach a file in the 'file' field.", 400)
        name = secure_filename(f.filename) or "upload"
        ext = os.path.splitext(name)[1].lower()
        if ext not in ACCEPTED:
            return error("unsupported_type", "Only PDF, JPG and PNG files are accepted.", 415)
        cmid = (request.form.get("client_message_id") or "").strip()[:128] or None
        if cmid:
            prev = store.reply_after(cid, cmid)
            if prev:                                    # a retried upload we already answered
                conv = view(row)
                return jsonify({"conversation": conv, "reply": next(m for m in conv["messages"] if m["id"] == message_id(prev))})
        folder = os.path.join(cfg["UPLOADS"], cid)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{uuid.uuid4().hex[:8]}_{name}")
        f.save(path)
        att = {"document": document, "filename": name, "path": path, "mime": f.mimetype or "application/octet-stream"}
        msg = {"text": "", "attachments": [att], "action": None}
        return run_turn(row, msg, "", [{"document": document, "filename": name}], cmid)

    app.store = store  # handy in tests
    return app


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(REPO, ".env"))
    except ImportError:
        pass
    port = int(os.environ.get("CHAT_SERVICE_PORT", 5001))
    application = create_app()
    print(f"Chat service on http://127.0.0.1:{port}  (agent: {os.environ.get('CHAT_AGENT', 'rules agent')})")
    application.run(host="127.0.0.1", port=port)
