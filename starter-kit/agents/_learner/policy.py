"""Router, Closer and the deterministic policy. Closing is never left to the model."""

import math

from .claims import MAX_PER_MESSAGE

WARM = {"relationship", "cash_short", "full_order_book", "not_strategic", "unpaid"}
FIRM = {"spare_capacity", "weak_costing"}


def alternative_cost(case, services_cost):
    alt = case["buyer"].get("alternative") or {}
    if alt.get("kind") in (None, "none") or not (alt.get("terms") or {}).get("unit_price"):
        return None
    try:
        c = services_cost(case, alt["terms"])
        return c if math.isfinite(c) else None
    except Exception:
        return None


def _close(kind, sid, offer_id=None, reason=""):
    action = {"type": "accept", "term_sheet_id": offer_id} if kind == "accept" else {"type": "walk_away"}
    return {"type": "close", "supplier_id": sid, "action": action, "reason": reason}


def best_acceptable(ledgers):
    acc = [(led["c"], sid) for sid, led in ledgers.items() if led.get("acceptable") and led.get("c") is not None]
    return min(acc) if acc else None


def route(case, ledgers, ready, c_alt, knobs, last_round):
    """Close now (accept/walk) or negotiate with the ready suppliers; `final_push` marks trade-only pushes."""
    live = [s for s, led in ledgers.items() if not led.get("ended")]
    # Only a ready supplier can receive our closing action. If a busy one has a better offer, the
    # engine sees our close while that request is in flight and itself accepts the cheapest acceptable
    # offer once the reply has settled (engine.py: close with pending -> close_action).
    best = best_acceptable({s: ledgers[s] for s in ready if s in live})
    fallback_exists = c_alt is not None

    def accept_best(reason):
        if best:
            sid = best[1]
            return _close("accept", sid, ledgers[sid]["offer_id"], reason)
        return _close("walk", ready[0], reason=reason + " (nothing acceptable)")

    if last_round:
        return accept_best("wave time")
    for sid in ready:
        if ledgers[sid]["moves_left"] <= 0:
            return accept_best("no moves left")
    pushes, holds = {}, {}

    def open_rivals(sid):
        return [
            s
            for s in live
            if s != sid and ledgers[s]["status"] not in ("final_offer", "agreed") and ledgers[s]["moves_left"] >= 1
        ]

    # The cheapest acceptable final offer is our fallback when there is no alternative: never push it.
    finals = sorted(
        (ledgers[s]["c"], s)
        for s in live
        if ledgers[s]["status"] == "final_offer" and ledgers[s].get("acceptable") and ledgers[s]["c"] is not None
    )
    protected = finals[0][1] if finals else None
    for sid in sorted(ready, key=lambda s: s == protected):  # the protected fallback is decided last
        led = ledgers[sid]
        if led["status"] == "agreed" and led.get("acceptable"):
            return accept_best("agreed")
        if led["status"] != "final_offer":
            continue
        others_open = open_rivals(sid)
        pushable = knobs.get("allow_final_push", True) and led["pushes_after_final"] == 0 and led["moves_left"] >= 2
        # a push may only risk this supplier when something else is left if it walks away
        safe = fallback_exists or others_open or (protected not in (None, sid) and not pushes)
        if pushable and safe:
            pushes[sid] = True
            continue
        if (others_open or pushes) and led["moves_left"] >= 2:
            holds[sid] = True  # its final offer stands as our fallback while the others negotiate
            continue
        if led.get("acceptable"):
            return accept_best("final offer")
        if fallback_exists and not [s for s in live if s != sid]:
            return _close("walk", sid, reason="final offer worse than our alternative")
    return {"type": "negotiate", "suppliers": list(ready), "final_push": pushes, "hold": holds}


def tone_for(tags, lessons_tone=None):
    if lessons_tone:
        return lessons_tone
    if set(tags) & WARM:
        return "warm"
    if set(tags) & FIRM:
        return "firm"
    return "neutral"


def det_choose(sid, led, menu, claims, tags, final_push, first, knobs):
    """The deterministic move: same menu and claims the model would see, a fixed schedule."""
    level = "trade" if final_push else "step"
    sheets = [m for m in menu if m["level"] == level] or [m for m in menu if m["level"] == "hold"] or menu
    sheets = sheets[:2]
    n_claims = 2 if first else 1
    chosen = [c for c in claims if not c.get("reveals")][:n_claims] or claims[:n_claims]
    ask = first or (
        not led.get("breakdown") and led.get("rounds", 0) <= 1 and bool(set(tags) & {"open_books", "weak_costing"})
    )
    tactic = "trade" if final_push else ("anchor" if first else "concede")
    return {
        "tactic": tactic,
        "sheet_ids": [m["mid"] for m in sheets],
        "claim_ids": [c["cid"] for c in chosen][:MAX_PER_MESSAGE],
        "ask_breakdown": ask,
        "tone": tone_for(tags, knobs.get("tone", {}).get(sid)),
        "questions": [],
        "prediction": {"status": "open", "price_move_pct": None, "note": "deterministic"},
        "read": "",
    }
