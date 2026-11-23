"""What the chat service may send to the website: the product contract (LLD-0002, frozen).

The agent works out a full decision for the evaluation harness (injection_flagged,
policy_conflict_noted, invalid_items, ...). Those are exam fields. They stay inside the agent and
the evaluation; only the fields below go over HTTP. The live copilot-svc sends the same six
verdict fields (copilot/adapters/web.py, result_for).
"""

RESULT_FIELDS = ("outcome", "missing_items", "assignee", "review_id", "citations", "volume_inr")


def public_result(decision):
    """The verdict as the website sees it, or None before submit."""
    if not decision:
        return None
    return {
        "outcome": decision["outcome"],
        "missing_items": list(decision.get("missing_items") or []),
        "assignee": decision.get("assignee"),
        "review_id": decision.get("review_id"),
        "citations": list(decision.get("citations") or []),
        "volume_inr": int(decision.get("volume_inr") or 0),
    }


def public_card(card):
    """A message card: the read-back summary, or the result card (verdict fields only)."""
    if not card:
        return None
    if card.get("type") == "result":
        return {"type": "result", **public_result(card)}
    if card.get("type") == "summary":
        return {"type": "summary", "rows": [{"label": r["label"], "value": r["value"]} for r in card["rows"]]}
    return None


def message_id(n):
    """Message ids are opaque strings, like the copilot's m_<12 hex>."""
    return f"m_{int(n):012d}"
