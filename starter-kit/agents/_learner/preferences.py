"""Evidence-bound preferences, distinct from binding terms and estimated cash value."""

import re

BREAKDOWN = re.compile(r"cost (?:breakdown|build|bridge)|calculation|internal cost|Kostenaufstellung|Kalkulation", re.I)
REFUSAL = re.compile(
    r"(?:do not|don't|cannot|can't|not) (?:share|provide|disclose)|proprietary|internal|vertraulich", re.I
)


def observe(case, events, previous=None):
    out = dict(previous or {})
    out["fields"] = dict(out.get("fields") or {})
    out["questions"] = dict(out.get("questions") or {})
    buyer, counters = None, {}
    for index, msg in enumerate(events):
        text = msg.get("body") or ""
        source = msg.get("message_id") or f"message:{index}"
        if msg.get("from") == "buyer":
            buyer = msg
            if BREAKDOWN.search(text):
                out["questions"]["breakdown"] = {"status": "asked", "source": source}
            continue
        if BREAKDOWN.search(text) and REFUSAL.search(text):
            out["questions"]["breakdown"] = {"status": "declined", "source": source}
        if any("breakdown" in a.get("name", "").lower() and a.get("text") for a in msg.get("attachments") or []):
            out["questions"]["breakdown"] = {"status": "answered", "source": source}
        ours = (buyer or {}).get("term_sheets") or []
        theirs = msg.get("term_sheets") or []
        if not ours or not theirs:
            continue
        for field in case["negotiable"]:
            if field == "unit_price" or any(field not in s["terms"] for s in theirs):
                continue
            values = []
            for sheet in theirs:
                if sheet["terms"][field] not in values:
                    values.append(sheet["terms"][field])
            old = counters.get(field, {})
            if all(s["terms"].get(field) not in values for s in ours):
                count = old.get("count", 0) + 1 if old.get("values") == values else 1
                counters[field] = {"values": values, "count": count, "source": source}
            elif old.get("values") == values:
                # it countered with this before and keeps it now that we offered it: still insisting (practice
                # P18: a reset here let us drop the index clause again after the supplier had required it)
                counters[field] = {"values": values, "count": old["count"] + 1, "source": source}
            else:
                counters.pop(field, None)
                if msg.get("status") == "agreed":
                    out["fields"].pop(field, None)
                continue
            if counters[field]["count"] >= 2:
                out["fields"][field] = {
                    "values": _widen(case, field, values),
                    "source": source,
                    "basis": "repeated counteroffers",
                }
    return out


def _widen(case, field, values):
    """A supplier that keeps sending an index clause insists on being indexed, not on one deadband: every
    clause the case allows qualifies (each is repriced to the same cost for us)."""
    if field != "index_clause" or not values or not all(values):
        return values
    options = [o for o in case.get("term_options", {}).get(field) or [] if o]
    return values + [o for o in options if o not in values]


def update(case, led, preferences, signals):
    """An LLM interpretation needs a verbatim evidence span and allowed field values."""
    out = {**preferences, "fields": dict(preferences.get("fields") or {})}
    for signal in signals if isinstance(signals, list) else []:
        if not isinstance(signal, dict):
            continue
        field, evidence, values = signal.get("field"), signal.get("evidence"), signal.get("values")
        if field not in case["negotiable"] or field == "unit_price":
            continue
        if not isinstance(evidence, str) or len(evidence) < 12 or evidence not in (led.get("body") or ""):
            continue
        if not isinstance(values, list) or not values or len(values) > 8:
            continue
        allowed = case.get("term_options", {}).get(field, []) + [led["std"].get(field)]
        if all(v in allowed for v in values):
            out["fields"][field] = {
                "values": values,
                "source": led.get("message_id"),
                "evidence": evidence,
                "basis": "supplier statement (interpretation)",
            }
    return out


def rank(menu, preferences):
    fields = preferences.get("fields") or {}
    return sorted(
        menu, key=lambda m: (sum(m["terms"].get(f) not in p["values"] for f, p in fields.items()), m["c"], m["mid"])
    )


def can_ask_breakdown(preferences):
    return not preferences.get("questions", {}).get("breakdown")


def filter_questions(text, preferences):
    if can_ask_breakdown(preferences):
        return text
    # Drop repeated requests, including requests hidden in the model-written body. Lines stay lines: joining
    # sentences with newlines split "Dear Mr. Seidl," into two lines.
    return "\n".join(
        " ".join(
            sentence
            for sentence in re.split(r"(?<=[.!?])\s+", line)
            if not (
                BREAKDOWN.search(sentence)
                and ("?" in sentence or re.search(r"\b(?:please|could|share|send)\b", sentence, re.I))
            )
        )
        for line in text.split("\n")
    )
