"""The naive buyer: anchors a little low, splits the difference, accepts early.

A baseline to beat, not a strategy: it ignores the data and the documents, never trades, never
asks and never walks away. One term sheet per e-mail.
"""

from ..cost import complete_offer, total_cost
from ..play import latest, live_threads, moves_left, standing


class NaiveAgent:
    def __init__(self, anchor=0.10, close_gap=0.01):
        self.anchor, self.close_gap = anchor, close_gap

    def decide(self, case, threads):
        # Talk to the supplier whose standing term sheet costs least.
        quoting = [s for s in live_threads(threads) if standing(threads[s])[1].get("unit_price") is not None]
        if not quoting:
            return {
                "supplier_id": live_threads(threads)[0],
                "message": {"action": {"type": "walk_away"}},
            }
        sid = min(quoting, key=lambda s: total_cost(case, standing(threads[s])[1]))
        events = threads[sid]
        sheet_id, terms = standing(events)
        accept = {
            "supplier_id": sid,
            "message": {"action": {"type": "accept", "term_sheet_id": sheet_id}},
        }
        theirs = terms["unit_price"]
        mine = [
            m["term_sheets"][0]["terms"]["unit_price"]
            for m in events
            if m.get("from") == "buyer" and m.get("term_sheets")
        ]
        if latest(events).get("status") in {"agreed", "final_offer"} or moves_left(case, events) <= 0:
            return accept
        if not mine:
            price = theirs * (1 - self.anchor)
        elif theirs - mine[-1] <= theirs * self.close_gap:
            return accept
        else:
            price = (theirs + mine[-1]) / 2
        return {
            "supplier_id": sid,
            "message": {
                "subject": case["title"],
                "body": "Thank you for your offer. We need a better price to go ahead.",
                "term_sheets": [{"terms": complete_offer(case, {**terms, "unit_price": price})}],
            },
        }
