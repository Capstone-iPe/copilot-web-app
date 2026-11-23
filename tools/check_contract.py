"""Check a running chat service against the frozen contract (contracts/chat_service.schema.json).

Works against the in-repo mock or the live copilot-svc. It opens one new conversation, lists and
reloads it, and asks "Why is this needed?" (which changes nothing in the application).

    # the mock (python start.py running):
    python tools/check_contract.py --url http://127.0.0.1:5001 --token dev-token --merchant dev:me@example.com

    # live copilot-svc: a Google access token for an allowed account, and google:<sub> as merchant
    python tools/check_contract.py --url https://copilot-svc.onrender.com --token <access token> --merchant google:<sub>

Exit code 0 = every response matched. Each mismatch is printed with the field that broke it.
Note: this creates one real conversation on the service you point it at.
"""
import argparse
import json
import os
import sys
import uuid

import jsonschema
import requests

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts", "chat_service.schema.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True, help="Bearer token: CHAT_SERVICE_TOKEN for the mock, a Google access token for live")
    ap.add_argument("--merchant", required=True, help="X-Merchant-Id, e.g. google:1234 or dev:me@example.com")
    args = ap.parse_args()
    defs = json.load(open(SCHEMA, encoding="utf-8"))["$defs"]
    base = args.url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}", "X-Merchant-Id": args.merchant}
    failures = 0

    def call(label, method, path, shape, ok=(200, 201), **kw):
        nonlocal failures
        r = requests.request(method, base + path, headers=headers, timeout=90, **kw)
        try:
            body = r.json()
        except ValueError:
            print(f"FAIL {label}: HTTP {r.status_code}, not JSON")
            failures += 1
            return None
        if r.status_code not in ok:
            shape = "Error"
        errors = sorted(jsonschema.Draft202012Validator({"$ref": f"#/$defs/{shape}", "$defs": defs}).iter_errors(body),
                        key=lambda e: list(e.absolute_path))
        if errors:
            failures += 1
            print(f"FAIL {label} (HTTP {r.status_code}, {shape}):")
            for e in errors[:10]:
                where = "/".join(str(p) for p in e.absolute_path) or "(top)"
                print(f"     {where}: {e.message[:160]}")
        else:
            print(f"ok   {label} (HTTP {r.status_code})")
        return body if r.status_code in ok else None

    created = call("create conversation", "POST", "/v1/conversations", "CreateConversationResponse")
    if not created:
        sys.exit(1)
    cid = created["conversation"]["id"]
    call("list conversations", "GET", "/v1/conversations", "ListConversationsResponse")
    call("load conversation", "GET", f"/v1/conversations/{cid}", "LoadConversationResponse")
    call("send a message", "POST", f"/v1/conversations/{cid}/messages", "SendMessageResponse",
         json={"text": "Why is this needed?", "action": None, "client_message_id": uuid.uuid4().hex})
    call("unknown conversation", "GET", "/v1/conversations/app_000000000000", "Error", ok=())
    print(f"\n{'All responses match' if not failures else str(failures) + ' response(s) do not match'} "
          f"contract {json.load(open(SCHEMA, encoding='utf-8'))['x-contract-version']} (conversation {cid}).")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
