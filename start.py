"""Start everything with one command:   python start.py

It starts the chat service (port 5001) and the website (port 5000) together.
Press Ctrl+C to stop both. Settings come from the .env file (copy .env.example to .env first).
"""
import os
import secrets
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    print("Tip: pip install -r requirements.txt (python-dotenv is missing, so .env was not read).")

if not os.path.exists(os.path.join(ROOT, ".env")):
    print("No .env file found: using development defaults (test sign-in, rules agent).")

env = dict(os.environ)
# The test sign-in reaches the in-repo chat service with a shared token. Without one in .env,
# make a throwaway token for this run so "Continue as test merchant" works out of the box.
# (Live copilot-svc uses Google tokens instead; this only matters for the local chat service.)
if not env.get("CHAT_SERVICE_TOKEN") and env.get("CHAT_SERVICE_URL", "http://127.0.0.1:5001").startswith(
        ("http://127.0.0.1", "http://localhost")):
    env["CHAT_SERVICE_TOKEN"] = secrets.token_hex(16)
procs = [
    subprocess.Popen([sys.executable, "-m", "chat_service.app"], cwd=ROOT, env=env),
    subprocess.Popen([sys.executable, "-m", "website.app"], cwd=ROOT, env=env),
]
time.sleep(1.5)
print(f"\nOpen http://localhost:{env.get('WEBSITE_PORT', '5000')} in your browser. Ctrl+C stops everything.\n")
try:
    while all(p.poll() is None for p in procs):
        time.sleep(0.5)
    print("One of the services stopped; check the messages above.")
except KeyboardInterrupt:
    pass
finally:
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
