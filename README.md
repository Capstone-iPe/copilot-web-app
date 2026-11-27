# iPe merchant website

The shop owner signs in with Google and applies in one chat. This repository is the website only. It decides nothing about the application.

The live site is [https://copilot-web-n05u.onrender.com](https://copilot-web-n05u.onrender.com). It sends every message and file to [copilot-svc](https://github.com/Capstone-iPe/copilot-svc) (`CHAT_SERVICE_URL=https://copilot-svc.onrender.com`). The copilot calls [ONB](https://github.com/Capstone-iPe/onb-svc) over MCP and [onb-corpus](https://github.com/Capstone-iPe/onb-corpus) for policy passages. The chat model is `Qwen/Qwen3-8B`, inside the copilot, not in this repo.

The HLD is [ADR-0001](https://github.com/Capstone-iPe/analysis/blob/main/ADR-0001-architecture.md). The browser contract is [LLD-0002](https://github.com/Capstone-iPe/analysis/blob/main/docs/LLD-0002-web-chat-contract.md), frozen at ConversationView v1.0. The machine form is [contracts/chat_service.schema.json](contracts/chat_service.schema.json).

`GOOGLE_ENFORCE_ALLOWED_EMAILS` is off, so any verified Google account can sign in. The list in `GOOGLE_ALLOWED_EMAILS` is ignored until that flag is `1`, `true`, `yes`, or `on`.

## What is in this repo

| Path | Role |
|---|---|
| `website/` | Pages, Google sign-in, and the HTTP client that forwards the bearer token |
| `chat_service/` | In-repo mock of the copilot, for laptop runs and contract tests. The rules agent is not the live product |
| `contracts/` | Frozen web chat ↔ copilot contract |
| `evaluation/` | Scripted runs of the mock agent. The 71-shop gold set is in [analysis/eval](https://github.com/Capstone-iPe/analysis/tree/main/eval) and is replayed by copilot-svc |
| `serve.py` | Production entry: gunicorn for the website on `0.0.0.0:$PORT`, and the mock chat service on localhost only when `CHAT_SERVICE_URL` points there |
| `start.py` | Laptop entry: Flask website plus the mock chat service |

Pills on the live copilot are upload and submit. The mock agent still offers answer chips so the local page can be exercised without the model.

## Run on a laptop

Python 3.12. Two ways to open the page. The mock needs nothing else. The real copilot needs the other three services already running.

```bash
git clone https://github.com/Capstone-iPe/copilot-web-app.git
cd copilot-web-app
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Mock chat, no keys

```bash
python start.py
```

Open http://localhost:5000. With no `.env`, the site shows **Continue as test merchant** and the in-repo rules agent answers on port 5001. That agent is for exercising the page. It is not the product.

### This website in front of a local copilot

Start [onb-corpus](https://github.com/Capstone-iPe/onb-corpus) on port 8081, [onb-svc](https://github.com/Capstone-iPe/onb-svc) on port 8000, and [copilot-svc](https://github.com/Capstone-iPe/copilot-svc) on port 8080. The [analysis README](https://github.com/Capstone-iPe/analysis#run-the-services-locally) has the clone layout, Postgres, the hosted keys, and the Google client. Register `http://localhost:5000/auth/callback` as an authorized redirect URI on that client.

```bash
cp .env.example .env
```

Set these in `.env`. Generate `SECRET_KEY` with `python -c "import secrets; print(secrets.token_hex(32))"`.

```bash
SECRET_KEY=<32-byte hex>
WEBSITE_PORT=5000
GOOGLE_CLIENT_ID=<same client id as the copilot and ONB>
GOOGLE_CLIENT_SECRET=<same client secret as ONB>
GOOGLE_REDIRECT_URI=http://localhost:5000/auth/callback
GOOGLE_ENFORCE_ALLOWED_EMAILS=0
SESSION_COOKIE_SECURE=0
CHAT_SERVICE_URL=http://127.0.0.1:8080
DEV_LOGIN=0
```

`python -m website.app` reads `.env` and serves http://localhost:5000. Sign in with Google. The page sends that access token to the copilot. The copilot and ONB must be configured with this same client id, or the chat answers that the sign-in was rejected.

To use the Render copilot instead of a local one, set `CHAT_SERVICE_URL=https://copilot-svc.onrender.com` and keep the Google values. The Render services accept the Google client configured on that deployment. A client you create for localhost is the one the local copilot and local ONB check.

## Tests

```bash
python -m pytest -q tests
```

## Live

Render runs `serve.py`. The website's `CHAT_SERVICE_URL` is the copilot. Free services sleep after about 15 minutes; open the site and wait about a minute. A burst of more than 30 messages or uploads in one UTC minute comes back from the copilot as HTTP 429 (`rate_limited`).
