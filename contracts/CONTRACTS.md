# Contract: web chat ↔ copilot (version 1.0, frozen)

The full decision record is **[LLD-0002](https://github.com/Capstone-iPe/analysis/blob/main/docs/LLD-0002-web-chat-contract.md)** in `Capstone-iPe/analysis`. This page is the quick reference for people working in this repository. The machine-checkable form is [`chat_service.schema.json`](chat_service.schema.json): if this page and the schema ever disagree, the schema wins, and both must be fixed together.

The same contract is spoken by the live `copilot-svc` and by the in-repo mock chat service (`chat_service/`).

## Calls

| Call | Request | Success |
|---|---|---|
| Create | `POST /v1/conversations` (body empty, or `{"application_id"}` to reopen your own) | `201 {conversation}`; `200` when reopening |
| List | `GET /v1/conversations` | `200 {conversations: [{id, shop_name, status, review_id, updated_at}]}`, newest first |
| Load | `GET /v1/conversations/{id}` | `200 {conversation}` |
| Send a message | `POST /v1/conversations/{id}/messages` `{"text", "action": "submit"\|null, "client_message_id"}` | `200 {conversation, reply}` |
| Upload a file | `POST /v1/conversations/{id}/files` multipart `document`, `file`, `client_message_id` | `200 {conversation, reply}` |

Every call (except `GET /v1/health`) sends two headers:
- `Authorization: Bearer <Google access token>`. With the development sign-in against the mock, it's `Bearer <CHAT_SERVICE_TOKEN>` instead.
- `X-Merchant-Id: google:<sub>`, or `dev:<email>` for the development sign-in.

`client_message_id` makes a retry safe: the same id returns the stored reply instead of running the turn twice. The page sends one with every message and upload.

## What the browser receives

| Object | Fields (exactly these) |
|---|---|
| ConversationView | `id`, `shop_name`, `status`, `review_id`, `steps`, `messages`, `prompt`, `result`, `created_at`, `updated_at` |
| Message | `id` (string), `role`, `text`, `attachments` (`[{document, filename}]`), `card`, `created_at` |
| Result / result card | `outcome`, `missing_items`, `assignee`, `review_id`, `citations`, `volume_inr` (+ `type: "result"` on the card) |
| Prompt | `field`, `kind`, `input`, `pills`, `accept` |
| Pill | `label`, `action` (`send`/`upload`/`submit`), `value` for send, `document` for upload |

**Not in the contract**, and never sent to the browser:
- **Exam-harness scoring fields:** `injection_flagged`, `policy_conflict_noted`, `business_type_allowed`, `addendum_required`, `invalid_items`.
- **ONB file states and verifier `evidence`.**

The rule agent still computes the exam fields for `evaluation/`. `chat_service/contract.py` strips them before anything goes over HTTP.

## Errors

`{"error": {"code", "message"}}`. The page picks its own wording **by code**: `message` may hold developer detail. The full table, with what the page does for each code, is LLD-0002 §6. The codes the page handles specially:

| Code | What the merchant sees |
|---|---|
| `unauthorized` / `forbidden` (copilot) | The website refreshes the Google token once. If the copilot still says no, the merchant is signed out and sees the sign-in page with a plain reason. |
| `corpus_unavailable` | "Policy search is unavailable for a moment…". The conversation is reloaded to show its true state, and the typed text is kept. Not retried automatically. |
| `onb_unavailable`, `history_unavailable`, `chat_unavailable`, `conversation_busy` | "Waking up / busy", with **Try again**. The website first retries reads, and writes that carry a `client_message_id`. |
| `turn_interrupted` | The conversation is reloaded; the copilot has added an explanation. |
| `not_found` | The merchant's latest conversation opens instead. |

## Checking a service against the contract

```
python tools/check_contract.py --url http://127.0.0.1:5001 --token <CHAT_SERVICE_TOKEN> --merchant dev:me@example.com
python tools/check_contract.py --url https://copilot-svc.onrender.com --token <Google access token> --merchant google:<sub>
```

This creates one conversation and checks create, list, load, a message, and a not-found error. The tests in `tests/` check every mock response the same way.

## Changing the contract

Add an optional field: update LLD-0002 and the schema in one pull request, and bump the minor version. Rename, remove, or change a type: that's `/v2`.
