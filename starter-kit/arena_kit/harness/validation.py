"""One validation/closing policy for every agent, timeout and error path."""

import math

from ..cost import OPS, must_haves_ok, total_cost
from ..docs import quote_terms, standard_terms
from ..play import live_threads, standing
from .state import encode


def documented(events):
    for msg in reversed(events):
        for att in msg.get("attachments") or []:
            if att.get("name", "").lower().startswith("quote"):
                try:
                    return bool(quote_terms(att.get("text", "")).get("stated")) and not att.get("error")
                except (ValueError, TypeError, OverflowError):
                    return False
    return False


def acceptable(case, events, policy=None, supplier_id=None):
    offer_id, terms = standing(events)
    std = standard_terms(events)
    if not offer_id or not documented(events):
        return False
    if any(f not in terms and f not in std and f not in case.get("fixed_terms", {}) for f in case["negotiable"]):
        return False
    try:
        if policy is not None and not policy.within_limits(supplier_id, terms, std):
            return False
        cost = total_cost(case, terms, std)
        alt = case["buyer"].get("alternative") or {}
        limit = (
            total_cost(case, alt["terms"]) if alt.get("kind") not in (None, "none") and alt.get("terms") else math.inf
        )
        return math.isfinite(cost) and must_haves_ok(case, terms, std) and cost <= limit
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        return False


def close_action(case, threads, policy=None):
    live = live_threads(threads)
    candidates = []
    for sid in live:
        if acceptable(case, threads[sid], policy, sid):
            offer_id, terms = standing(threads[sid])
            cost = (
                policy.cost(sid, terms, standard_terms(threads[sid]))
                if policy
                else total_cost(case, terms, standard_terms(threads[sid]))
            )
            candidates.append((cost, sid, offer_id))
    if candidates:
        _, sid, offer_id = min(candidates)
        return {"supplier_id": sid, "message": {"action": {"type": "accept", "term_sheet_id": offer_id}}}
    sid = (live or list(threads))[0]
    return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}


def validate(case, threads, actions, policy=None):
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
                if cmd["term_sheet_id"] != standing(threads[sid])[0] or not acceptable(case, threads[sid], policy, sid):
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
            if policy is not None and (
                not must_haves_ok(case, sheet["terms"], std) or not policy.within_limits(sid, sheet["terms"], std)
            ):
                raise ValueError("Proposal exceeds buyer limits or mandatory requirements")
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


def validate_case(case):
    """Validate essentials, not known case IDs/types. Reject unknown binding operators.

    Additional descriptive fields and new negotiable fields remain supported.
    The published economics must still be computable without invented buyer inputs.
    """
    if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
        raise ValueError("Case needs an ID")
    for key in ("suppliers", "negotiable"):
        items = case.get(key)
        if (
            not isinstance(items, list)
            or not items
            or any(not isinstance(x, str) for x in items)
            or len(set(items)) != len(items)
        ):
            raise ValueError(f"Invalid case {key}")
    buyer = case.get("buyer")
    if not isinstance(buyer, dict):
        raise ValueError("Missing buyer requirements")
    for key in (
        "need_per_year",
        "horizon_years",
        "fallback_unit_price",
        "interest_rate",
        "reference_payment_days",
        "need_lead_time_days",
    ):
        value = buyer.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"Missing or invalid buyer {key}")
    if buyer["need_per_year"] <= 0 or buyer["horizon_years"] <= 0:
        raise ValueError("Buyer volume and horizon must be positive")
    freight = buyer.get("freight_eur_per_unit")
    if (
        not isinstance(freight, dict)
        or not freight
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in freight.values())
    ):
        raise ValueError("Missing freight cost mapping")
    for key in ("fixed_terms", "term_options", "facts"):
        if not isinstance(case.get(key, {}), dict):
            raise ValueError(f"Invalid {key}")
    requirements = buyer.get("must_haves", [])
    if not isinstance(requirements, list):
        raise ValueError("Invalid mandatory conditions")
    for rule in requirements:
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("field"), str)
            or rule.get("op") not in OPS
            or "value" not in rule
        ):
            raise ValueError("Unsupported mandatory condition")
    for options in case.get("term_options", {}).values():
        if not isinstance(options, list):
            raise ValueError("Term options must be lists")
    rounds = case.get("round_limit", 10)
    if type(rounds) is not int or rounds < 1:
        raise ValueError("Invalid round limit")
    return case
