# Contracts: website ↔ chat service (version 1)

A **contract** is a written agreement about the exact messages two programs send each other. The website team and the chat-service team can work separately as long as both keep to it. The machine-checkable version is [`chat_service.schema.json`](chat_service.schema.json) (JSON Schema). The tests in `tests/` fail if either side breaks it.

The requirements name three contracts: **send a chat message**, **load the past conversation** and **upload a file**. Two small helpers (create and list conversations) are added because "one shop, one conversation" needs them.

## Every request

| Header | Value |
|---|---|
| `Authorization` | `Bearer <Google access token>` when the merchant signed in with Google. The chat service asks Google whether that token is still valid. For development sign-in only: `Bearer <CHAT_SERVICE_TOKEN>`. |
| `X-Merchant-Id` | The signed-in merchant's id. For Google this is `google:` plus Google's `sub`, and it must match the bearer. |

A merchant only ever sees their own conversations. Someone else's conversation id returns `404`.

Base URL: `CHAT_SERVICE_URL` (default `http://127.0.0.1:5001`). All bodies are JSON, except file uploads.

## 1. Send a chat message

`POST /v1/conversations/{conversation_id}/messages`

```json
{ "text": "Lotus Tea House", "action": null, "client_message_id": "7f1c…" }
```

- `text`: what the merchant typed or the pill they tapped (up to 2000 characters).
- `action`: `"submit"` when the merchant presses Submit, otherwise `null`.
- `client_message_id` (optional): a random id made by the page. If the same id arrives twice (for example after a network retry), the message is not processed again.

Response `200`:

```json
{ "conversation": { …ConversationView… }, "reply": { …Message… } }
```

## 2. Load the past conversation

`GET /v1/conversations/{conversation_id}`

Response `200`: `{ "conversation": { …ConversationView… } }`. The page calls this on every load, which is why a refresh shows the same conversation.

## 3. Upload a file

`POST /v1/conversations/{conversation_id}/files` as `multipart/form-data`:

| Field | Value |
|---|---|
| `document` | `business_registration`, `owner_id`, `bank_proof`, `shopfront_photo` or `dpa_addendum` |
| `file` | The file: PDF, JPG or PNG, up to 10 MB |

Response `200`: same shape as sending a message. Before submit, only the document currently being asked for is accepted. After the result, any missing document can be uploaded and the result updates; the review number stays the same.

## Helpers

| Call | Purpose | Response |
|---|---|---|
| `POST /v1/conversations` | Start a new shop application | `201 { "conversation": … }` |
| `GET /v1/conversations` | This merchant's applications, newest first | `{ "conversations": [ {id, shop_name, status, review_id, updated_at} ] }` |
| `GET /v1/health` | Is the service up? (no headers needed) | `{ "ok": true, "agent": "…" }` |

## The objects

**ConversationView**: everything the page needs to draw the screen.

| Field | Meaning |
|---|---|
| `id` | `c_` + 12 characters |
| `shop_name` | Once given, else `null` |
| `status` | `DRAFT` before submit; then `IN_REVIEW`, `RECOMMEND_GO_LIVE` or `REFUSED` |
| `review_id` | `MR-…` once the result opens a review; `null` before that and for refused applications |
| `steps` | `shop_details`, `documents`, `submitted`: each `todo`, `current` or `done` |
| `messages` | Every message so far, oldest first |
| `prompt` | What to offer under the last message (below) |
| `result` | After submit: `outcome`, `missing_items`, `assignee`, `review_id`, `citations`, `volume_inr` |

**Message**: `id`, `role` (`merchant` or `assistant`), `text`, `attachments` (`[{document, filename}]`), `card` (`null`, a summary card or a result card), `created_at`.

**Prompt**: tells the page which pills and inputs to show.

| Field | Meaning |
|---|---|
| `field` | The question being asked (e.g. `city`, `bank_proof`, `summary`), or `null` after the result |
| `kind` | `text`, `choice`, `upload`, `summary` or `closed` (after the result) |
| `input` | Show the text box? |
| `pills` | Buttons: `{label, action}` where `action` is `send` (send `value` as text), `upload` (open a file picker for `document`) or `submit` |
| `accept` | File types allowed when uploading |

## Errors

Every error looks like `{"error": {"code": "…", "message": "…"}}`. The message is safe to show the merchant.

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request`, `empty_message`, `bad_document`, `no_file`, `no_merchant` | The request is malformed |
| 401 | `unauthorized` | Wrong or missing service token |
| 404 | `not_found` | No such conversation for this merchant |
| 413 | `file_too_large` | Over 10 MB |
| 415 | `unsupported_type` | Not PDF, JPG or PNG |

## Changing a contract

Add fields freely: both sides ignore fields they don't know. Renaming or removing a field is a breaking change: move to `/v2/…` and keep `/v1/…` running until the website has switched.
