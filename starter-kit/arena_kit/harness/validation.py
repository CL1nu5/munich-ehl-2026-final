"""One validation/closing policy for every agent, timeout and error path."""

import math

from ..docs import quote_terms, standard_terms
from ..play import live_threads
from .state import encode
from .valuation import ranked_offers


def documented(events):
    for msg in reversed(events):
        for att in msg.get("attachments") or []:
            if att.get("name", "").startswith("quote"):
                try:
                    return bool(quote_terms(att.get("text", "")).get("stated")) and not att.get("error")
                except (ValueError, TypeError):
                    return False
    return False


def acceptable(case, events, offer_id=None):
    if not documented(events):
        return False
    try:
        return any(
            s["beats_alternative"] and (offer_id is None or s["term_sheet_id"] == offer_id)
            for s in ranked_offers(case, events)
        )
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        return False


def close_action(case, threads):
    live = live_threads(threads)
    candidates = []
    for sid in live:
        if documented(threads[sid]):
            candidates.extend(
                (s["cost"], sid, s["term_sheet_id"])
                for s in ranked_offers(case, threads[sid])
                if s["beats_alternative"]
            )
    if candidates:
        _, sid, offer_id = min(candidates)
        return {"supplier_id": sid, "message": {"action": {"type": "accept", "term_sheet_id": offer_id}}}
    sid = (live or list(threads))[0]
    return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}


def validate(case, threads, actions):
    if not isinstance(actions, list) or not actions or len(actions) > len(threads):
        raise ValueError("Agent must return a nonempty list of actions")
    seen = set()
    for action in actions:
        if set(action) != {"supplier_id", "message"}:
            raise ValueError("Action must contain supplier_id and message")
        sid, message = action["supplier_id"], action["message"]
        if sid not in live_threads(threads) or sid in seen:
            raise ValueError("Unknown, closed or duplicate supplier")
        seen.add(sid)
        if not isinstance(message, dict) or set(message) - {
            "subject",
            "body",
            "term_sheets",
            "refs",
            "attachments",
            "action",
            "thread_id",
            "message_id",
            "in_reply_to",
        }:
            raise ValueError("Invalid message fields")
        if len(encode({"message": message}).encode()) > 64 * 1024:
            raise ValueError("Message too large")
        if message.get("action"):
            cmd = message["action"]
            if len(actions) != 1 or cmd.get("type") not in ("accept", "walk_away"):
                raise ValueError("Closing must be the only action")
            if cmd["type"] == "accept":
                if set(cmd) != {"type", "term_sheet_id"}:
                    raise ValueError("Invalid accept")
                if not acceptable(case, threads[sid], cmd["term_sheet_id"]):
                    raise ValueError("Unsafe or stale acceptance")
            elif set(cmd) != {"type"}:
                raise ValueError("Invalid walk_away")
            continue
        for field, size in (("subject", 300), ("body", 8000)):
            if not isinstance(message.get(field, ""), str) or len(message.get(field, "")) > size:
                raise ValueError(f"Invalid {field}")
        refs = message.get("refs", [])
        if not isinstance(refs, list) or len(refs) > 20 or any(not isinstance(x, str) for x in refs):
            raise ValueError("Invalid refs")
        attachments = message.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > 5:
            raise ValueError("Invalid attachments")
        import base64

        for att in attachments:
            if set(att) != {"name", "type", "content_b64"} or att["type"] not in ("md", "csv", "json", "pdf", "xlsx"):
                raise ValueError("Invalid attachment")
            base64.b64decode(att["content_b64"], validate=True)
        sheets = message.get("term_sheets", [])
        if not isinstance(sheets, list) or len(sheets) > 3:
            raise ValueError("At most three term sheets")
        std = standard_terms(threads[sid])
        for sheet in sheets:
            if set(sheet) != {"terms"} or set(sheet["terms"]) != set(case["negotiable"]):
                raise ValueError("Term sheets require exactly the negotiable fields")
            for field, value in sheet["terms"].items():
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("Non-finite term")
                if field == "unit_price":
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, int | float)
                        or value <= 0
                        or round(value, 2) != value
                    ):
                        raise ValueError("Price must be positive with at most two decimals")
                elif value not in case.get("term_options", {}).get(field, []) and value != std.get(field):
                    raise ValueError(f"Term outside allowed options: {field}")
    return actions
