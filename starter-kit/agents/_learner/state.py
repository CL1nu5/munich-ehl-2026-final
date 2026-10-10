"""JSON state hygiene: the harness rejects NaN/inf and caps the reply line."""

import json
import math

STATE_VERSION = 1
STATE_LIMIT = 48 * 1024
LOG_KEEP = 40


def sanitize(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [sanitize(v) for v in value]
    if value is None or isinstance(value, str | int | bool):
        return value
    return str(value)


def size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def compact(state, limit=STATE_LIMIT):
    """Sanitized state within `limit` bytes: oldest log entries go first, then long free text."""
    state = sanitize(state)
    log = state.get("log") or []
    state["log"] = log[-LOG_KEEP:]
    while size(state) > limit and state["log"]:
        state["log"] = state["log"][1:]
    if size(state) > limit:
        for sup in (state.get("sup") or {}).values():
            sup.pop("read", None)
            sup.pop("plan", None)
    if size(state) > limit:
        state.pop("reflection", None)
    return state
