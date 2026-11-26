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

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python start.py
```

Open http://localhost:5000. With no `.env`, the site shows **Continue as test merchant** and the mock rules agent answers.

To point the same website at the live copilot, copy `.env.example` to `.env` and set:

```bash
CHAT_SERVICE_URL=https://copilot-svc.onrender.com
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:5000/auth/callback
```

Sign-in then uses Google, and the page forwards that access token. The copilot and ONB must accept the same Google client.

```bash
python -m pytest -q tests
```

## Live

Render runs `serve.py`. The website's `CHAT_SERVICE_URL` is the copilot. Free services sleep after about 15 minutes; open the site and wait about a minute. A burst of more than 30 messages or uploads in one UTC minute comes back from the copilot as HTTP 429 (`rate_limited`).
