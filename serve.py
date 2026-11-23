"""Run the chat service and the website in one host.

The website listens on 0.0.0.0:$PORT. The chat service listens only on this machine.
"""
import os
import secrets
import subprocess
import sys

port = os.environ.get("PORT", "5000")
os.environ.setdefault("CHAT_SERVICE_URL", "http://127.0.0.1:5001")
# Test sign-in -> in-repo chat service needs a shared token; make a throwaway one if none is set.
if not os.environ.get("CHAT_SERVICE_TOKEN") and os.environ["CHAT_SERVICE_URL"].startswith(("http://127.0.0.1", "http://localhost")):
    os.environ["CHAT_SERVICE_TOKEN"] = secrets.token_hex(16)
public = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
if public and not os.environ.get("GOOGLE_REDIRECT_URI"):
    os.environ["GOOGLE_REDIRECT_URI"] = public + "/auth/callback"
if os.environ.get("RENDER") == "true":
    os.environ.setdefault("SESSION_COOKIE_SECURE", "1")

chat = subprocess.Popen(
    [
        sys.executable, "-m", "gunicorn",
        "--bind", "127.0.0.1:5001",
        "--workers", "1",
        "--timeout", "180",
        "chat_service.app:create_app()",
    ]
)
try:
    raise SystemExit(subprocess.call(
        [
            sys.executable, "-m", "gunicorn",
            "--bind", f"0.0.0.0:{port}",
            "--workers", "1",
            "--timeout", "180",
            "website.app:create_app()",
        ]
    ))
finally:
    chat.terminate()
