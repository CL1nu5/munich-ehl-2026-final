"""Observer: one ledger per supplier thread - what it offers, how it moved, how our claims landed."""

import math

from arena_kit.docs import quote_terms, standard_terms
from arena_kit.harness.validation import acceptable
from arena_kit.harness.valuation import ranked_offers, value
from arena_kit.play import ENDED, latest, moves_left, standing

TEXT_CHARS = 1200
ATTACH_CHARS = 1500


def _quote_sections(events):
    """Sections of the first readable quote PDF in the thread (the opening offer)."""
    for msg in events:
        for att in msg.get("attachments") or []:
            if att.get("name", "").startswith("quote") and att.get("text"):
                try:
                    return quote_terms(att["text"])
                except Exception:
                    return {}
    return {}


def build(case, threads, models=None):
    out = {}
    for sid, events in threads.items():
        led = {"supplier_id": sid}
        try:
            last = latest(events)
        except StopIteration:
            continue
        offer_id, terms = standing(events)
        model = (models or {}).get(sid)
        ranked = ranked_offers(case, events, model)
        eligible = [s for s in ranked if s["beats_alternative"]]
        if eligible or ranked:
            best = (eligible or ranked)[0]
            offer_id, terms = best["term_sheet_id"], best["terms"]
        led["offers"] = ranked
        led["model"] = model
        led.update(offer_id=offer_id, terms=terms, price=terms.get("unit_price"), status=last.get("status"))
        led["ended"] = last.get("status") in ENDED
        led["std"] = standard_terms(events)
        led["stated_open"] = _quote_sections(events).get("stated", {})
        try:
            valuation = value(case, terms, led["std"], model) if terms else {}
            c = valuation.get("cost", math.inf)
            led.update(
                known_cost=valuation.get("known_cost"), relationship_estimate=valuation.get("relationship_estimate")
            )
        except Exception:
            c = math.inf
        led["c"] = c if math.isfinite(c) else None
        supplier_msgs = [m for m in events if m.get("from") != "buyer"]
        buyer_msgs = [m for m in events if m.get("from") == "buyer"]
        led["prices"] = [(m.get("term_sheets") or [{}])[0].get("terms", {}).get("unit_price") for m in supplier_msgs]
        led["statuses"] = [m.get("status") for m in supplier_msgs]
        led["rounds"] = len(buyer_msgs)
        led["moves_left"] = moves_left(case, events)
        led["our_sheets"] = [[s.get("terms", {}) for s in m.get("term_sheets") or []] for m in buyer_msgs]
        led["missing_fields"] = last.get("missing_fields") or []
        led["claim_results"] = [r.get("result") for r in last.get("claim_results") or []]
        led["claims_total"] = {}
        for m in supplier_msgs:
            for r in m.get("claim_results") or []:
                led["claims_total"][r.get("result")] = led["claims_total"].get(r.get("result"), 0) + 1
        led["from"] = last.get("from")
        led["body"] = (last.get("body") or "")[:TEXT_CHARS]
        led["message_id"] = last.get("message_id")
        led["signals"] = last.get("signals") or {}
        prices = [p for p in led["prices"] if p]
        led["move_pct"] = round((prices[-2] - prices[-1]) / prices[-2] * 100, 2) if len(prices) >= 2 else None
        led["total_move_pct"] = round((prices[0] - prices[-1]) / prices[0] * 100, 2) if len(prices) >= 2 else 0.0
        first_final = next((i for i, s in enumerate(led["statuses"]) if s == "final_offer"), None)
        # supplier message i answers buyer message i-1 (message 0 is the opening quote)
        # only messages with term sheets push; a holding note without sheets does not
        led["pushes_after_final"] = (
            0 if first_final is None else sum(bool(m.get("term_sheets")) for m in buyer_msgs[first_final:])
        )
        led["attachments"] = [
            {"name": a.get("name"), "text": (a.get("text") or "")[:ATTACH_CHARS]}
            for a in last.get("attachments") or []
            if a.get("text") and not a.get("name", "").startswith("quote")
        ]
        led["breakdown"] = any(
            "breakdown" in (a.get("name") or "").lower() for m in supplier_msgs for a in m.get("attachments") or []
        )
        try:
            led["acceptable"] = bool(eligible) and not led["ended"] and acceptable(case, events, offer_id)
        except Exception:
            led["acceptable"] = False
        out[sid] = led
    return out


def digest(led):
    """Compact view for the prompt."""
    return {
        "status": led.get("status"),
        "from": led.get("from"),
        "standing_terms": led.get("terms"),
        "standing_cost_eur": led.get("c"),
        "known_cost_eur": led.get("known_cost"),
        "relationship_estimate_eur": led.get("relationship_estimate"),
        "supplier_cost_assumption": led.get("model"),
        "current_offers": led.get("offers"),
        "price_path": led.get("prices"),
        "status_path": led.get("statuses"),
        "our_messages": led.get("rounds"),
        "moves_left": led.get("moves_left"),
        "last_move_pct": led.get("move_pct"),
        "claims_last": led.get("claim_results"),
        "claims_total": led.get("claims_total"),
        "missing_fields": led.get("missing_fields"),
        "standard_terms": led.get("std"),
        "signals": led.get("signals"),
        "latest_email": led.get("body"),
        "attachments": led.get("attachments"),
    }
