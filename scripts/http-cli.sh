#!/usr/bin/env bash
# FE smoke client: Google sign-in / refresh, then chat against the copilot HTTP API.
#
# Default: Render FastAPI. Local override:
#   ./scripts/http-cli.sh http://127.0.0.1:8080
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${1:-${COPILOT_BASE_URL:-https://copilot-svc.onrender.com}}"
BASE="${BASE%/}"

if ! command -v curl >/dev/null || ! command -v python3 >/dev/null; then
  echo "Need curl and python3." >&2
  exit 1
fi

if [[ -n "${COPILOT_BEARER:-}" ]]; then
  BEARER="$COPILOT_BEARER"
else
  echo "Signing in (Google)…" >&2
  BEARER="$(python3 "$ROOT/scripts/fe_bearer.py")"
fi
AUTH_HEADER=(-H "Authorization: Bearer $BEARER")

json_get() {
  local key="$1"
  python3 -c 'import json,sys; d=json.load(sys.stdin); v=d.get(sys.argv[1]);
print("" if v is None else v if not isinstance(v, (dict, list)) else json.dumps(v))' "$key"
}

print_turn() {
  python3 -c '
import json, sys
d = json.load(sys.stdin)
msgs = d.get("messages") or []
if msgs:
    for m in msgs:
        if m:
            print(m)
elif d.get("text"):
    print(d["text"])
'
}

echo "copilot HTTP CLI → $BASE" >&2
health="$(curl -sf "$BASE/v1/health" || true)"
if [[ -z "$health" ]]; then
  echo "Cannot reach $BASE/v1/health — is the API running?" >&2
  exit 1
fi

opened="$(curl -sf -X POST "$BASE/v1/conversations" "${AUTH_HEADER[@]}")"
CID="$(printf '%s' "$opened" | json_get conversation_id)"
if [[ -z "$CID" ]]; then
  echo "Open failed: $opened" >&2
  exit 1
fi
echo "conversation $CID" >&2
printf '%s' "$opened" | print_turn

while true; do
  if ! IFS= read -r -p "> " line; then
    echo
    break
  fi
  low="$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [[ -z "$low" ]]; then
    continue
  fi
  if [[ "$low" == "quit" || "$low" == "exit" ]]; then
    break
  fi

  if [[ -z "${COPILOT_BEARER:-}" ]]; then
    BEARER="$(python3 "$ROOT/scripts/fe_bearer.py")"
    AUTH_HEADER=(-H "Authorization: Bearer $BEARER")
  fi

  if [[ "$line" =~ \.(pdf|png|jpe?g|PDF|PNG|JPE?G)$ ]]; then
    body="$(python3 -c 'import json,sys; print(json.dumps({"file": sys.argv[1]}))' "$line")"
    path="files"
  else
    body="$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$line")"
    path="messages"
  fi

  if ! out="$(curl -sf -X POST "$BASE/v1/conversations/$CID/$path" \
      "${AUTH_HEADER[@]}" \
      -H 'Content-Type: application/json' \
      -d "$body")"; then
    echo "Request failed." >&2
    continue
  fi
  printf '%s' "$out" | print_turn
done
