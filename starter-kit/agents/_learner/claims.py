"""Claim Cataloguer: data arguments that pass the arena's check, with the figure computed in code.

Rules follow brief/api.md "What counts": one argument per sentence, the figure in the same sentence
as its IDs, tolerance ±0.5 pp / ±0.5 %, at most 3 checked per message, each ID once per case.
Only claims that help the buyer are listed; the model chooses among them by `cid`.
"""

import re

from .offers import should_cost_row

ID_RE = re.compile(r"\b(?:po|idx|sc|ctr|doc|quote|offer|qn|fc|cost|news|rfq|sup|part):[\w.\-@#]+")
FAVOURABLE_NEWS = {"free_capacity", "short_time_work"}
MAX_PER_MESSAGE = 3


def ids_in(text):
    return [m.rstrip(".,;)") for m in ID_RE.findall(text or "")]


PER_SUPPLIER = ("quote:", "offer:")  # a rival's quote counts once per supplier, every other ID once per case


def used_ids(threads):
    """IDs already cited, as the arena counts them: {supplier: IDs that would be a duplicate there}."""
    per_sup, case_wide = {}, set()
    for sid, events in threads.items():
        seen = per_sup.setdefault(sid, set())
        for m in events:
            if m.get("from") == "buyer":
                ids = set(m.get("refs") or []) | set(ids_in(m.get("body")))
                seen.update(i for i in ids if i.startswith(PER_SUPPLIER))
                case_wide.update(i for i in ids if not i.startswith(PER_SUPPLIER))
    return {sid: seen | case_wide for sid, seen in per_sup.items()}


def series_index(facts):
    """{series: sorted months} from the idx facts."""
    out = {}
    for ref, fact in facts.items():
        if ref.startswith("idx:") and isinstance(fact, dict) and fact.get("series") and fact.get("month"):
            out.setdefault(fact["series"], []).append(fact["month"])
    for months in out.values():
        months.sort()
    return out


def _minus_year(month):
    y, m = month.split("-")
    return f"{int(y) - 1}-{m}"


def _pct(x):
    return f"{x:+.1f}%"


def _eur(x):
    return f"EUR {x:.2f}"


def build_catalog(case, facts, dossier, sid, standing_price=None, asked_pct=None):
    """Static claims for one supplier (figures pre-computed and within tolerance)."""
    facts = {**facts, **(case.get("facts") or {})}
    parts = set(case.get("parts") or [])
    out = []

    def add(kind, ids, figure, unit, sentence, supports, strength, reveals=False):
        out.append(
            {
                "kind": kind,
                "ids": ids,
                "figure": figure,
                "unit": unit,
                "sentence": sentence,
                "supports": supports,
                "strength": round(strength, 2),
                "reveals": reveals,
            }
        )

    # Index pairs: series that drive this part's cost, the case's index paths, or IDs in the case refs.
    series = set()
    for part in dossier.get("parts", {}).values():
        series.update(d["series_id"] for d in part.get("cost_drivers", []) if d.get("series_id"))
    series.update((case.get("buyer") or {}).get("index_paths", {}) or {})
    series.update(r.split(":", 1)[1].split("@")[0] for r in case.get("refs") or [] if r.startswith("idx:"))
    months = series_index(facts)
    weights = {}
    for part in dossier.get("parts", {}).values():
        for d in part.get("cost_drivers", []):
            weights[d.get("series_id")] = max(weights.get(d.get("series_id"), 0), d.get("weight") or 0)
    for s in sorted(series):
        later = (months.get(s) or [None])[-1]
        if not later or later < "2025-10":
            continue
        earlier = _minus_year(later)
        a, b = facts.get(f"idx:{s}@{earlier}"), facts.get(f"idx:{s}@{later}")
        if not a or not b or not a.get("value"):
            continue
        change = (b["value"] / a["value"] - 1) * 100
        rounded = round(change, 1)
        # helps the buyer when it fell, or rose by at most half of what the supplier asks for
        bar = max(0.0, asked_pct / 2) if asked_pct is not None else 0.0
        if rounded > bar:
            continue
        strength = 0.4 + min(0.5, (bar - rounded + 1) / 10) + min(0.1, weights.get(s, 0) / 3)
        add(
            "idx",
            [f"idx:{s}@{earlier}", f"idx:{s}@{later}"],
            rounded,
            "%",
            f"The {s} index changed {_pct(rounded)} from {earlier} to {later} (idx:{s}@{earlier}, idx:{s}@{later}).",
            "price_down",
            strength,
        )

    # Scorecard: weak delivery or quality of this supplier, latest quarter.
    sc = [(ref, f) for ref, f in facts.items() if ref.startswith(f"sc:{sid.split(':')[-1]}@")]
    if sc:
        ref, f = max(sc, key=lambda x: x[1].get("quarter") or "")
        if f.get("otd_pct") is not None and f["otd_pct"] < 92:
            add(
                "sc",
                [ref],
                round(f["otd_pct"], 1),
                "%",
                f"Your on-time delivery last quarter was {f['otd_pct']:.1f}% ({ref}).",
                "performance",
                0.35 + min(0.4, (92 - f["otd_pct"]) / 40),
            )
        elif f.get("ppm") is not None and f["ppm"] > 500:
            add(
                "sc",
                [ref],
                round(f["ppm"]),
                "ppm",
                f"Your quality last quarter was {round(f['ppm'])} ppm ({ref}).",
                "performance",
                0.35 + min(0.4, f["ppm"] / 10000),
            )

    # Quotes of this case (refs or the alternative): a cheaper price for the same part.
    alt = (case.get("buyer") or {}).get("alternative") or {}
    quote_refs = [r for r in case.get("refs") or [] if r.startswith("quote:")]
    if alt.get("ref") and str(alt["ref"]).startswith("quote:"):
        quote_refs.append(alt["ref"])
    for ref in dict.fromkeys(quote_refs):
        f = facts.get(ref) or {}
        price = f.get("unit_price")
        if price is None or (f.get("part") and f["part"] not in parts) or f.get("supplier") == sid:
            continue
        if f.get("price_unit") not in (None, 1):
            continue
        if standing_price is not None and price >= standing_price:
            continue
        add(
            "quote",
            [ref],
            price,
            "EUR",
            f"We hold a quote for this part at {_eur(price)} per unit ({ref}).",
            "price_down",
            0.7,
            reveals=True,
        )

    # Should-cost: offer far above the estimate.
    for part in parts:
        row = should_cost_row({part: (dossier.get("parts") or {}).get(part) or {}}, sid)
        ref = (row or {}).get("id") or f"cost:{part.split(':')[-1]}"
        f = facts.get(ref) or {}
        if f.get("unit_cost") and f.get("price_unit") in (None, 1) and standing_price:
            if standing_price > f["unit_cost"] * 1.12:
                add(
                    "cost",
                    [ref],
                    f["unit_cost"],
                    "EUR",
                    f"Our should-cost estimate for this part is {_eur(f['unit_cost'])} per unit ({ref}).",
                    "price_down",
                    0.45,
                    reveals=True,
                )

    # Forecast: rising volume is something to offer.
    for part in parts:
        num = part.split(":")[-1]
        fcs = sorted(r for r in facts if r.startswith(f"fc:{num}@"))
        if fcs:
            f = facts[fcs[-1]]
            if (f.get("change_pct") or 0) >= 3:
                add(
                    "fc",
                    [fcs[-1]],
                    round(f["change_pct"], 1),
                    "%",
                    f"Our forecast for this part is up {f['change_pct']:.1f}% ({fcs[-1]}).",
                    "volume",
                    0.4,
                )

    # Order history: 2-8 recent orders of this part from this supplier at one price, below the offer.
    for part in parts:
        rows = sorted(
            (
                (f.get("date") or "", ref, f)
                for ref, f in facts.items()
                if ref.startswith("po:") and f.get("part") == part and f.get("supplier") == sid
            ),
            reverse=True,
        )
        if len(rows) >= 2 and rows[0][2].get("price_unit") in (None, 1):
            price = rows[0][2].get("unit_price")
            same = [r for r in rows[:8] if r[2].get("unit_price") == price]
            if len(same) >= 2 and price and standing_price and price < standing_price:
                ids = [r[1] for r in same[:4]]
                add(
                    "po",
                    ids,
                    price,
                    "EUR",
                    f"Our recent orders of this part were at {_eur(price)} per unit ({', '.join(ids)}).",
                    "price_down",
                    0.4,
                )

    # News about this supplier that weakens its position.
    for n in (dossier.get("suppliers", {}).get(sid) or {}).get("news", []):
        ref = n.get("id")
        if n.get("topic") in FAVOURABLE_NEWS and ref and (facts.get(ref) or {}).get("supplier") == sid:
            add("news", [ref], None, None, f"We read that you have spare capacity ({ref}).", "volume", 0.3)

    # Notice period: the letter gives less notice than the contract's price clause.
    trig = dossier.get("trigger") or {}
    contract = (dossier.get("suppliers", {}).get(sid) or {}).get("contract_id")
    if contract and not str(contract).startswith("ctr:"):
        contract = f"ctr:{contract}"
    if not contract:  # e.g. a supplier that exists only in a hidden case's own facts
        contract = next(
            (r for r, f in facts.items() if r.startswith("ctr:") and "#" not in r and f.get("supplier") == sid), None
        )
    if trig.get("id") and trig.get("days") is not None and contract:
        doc = facts.get(trig["id"]) or {}
        if doc.get("supplier") == sid:
            clauses = [
                (ref, f)
                for ref, f in facts.items()
                if ref.startswith(f"{contract}#") and f.get("type") == "price" and f.get("days")
            ]
            ref, f = clauses[0] if clauses else (contract, facts.get(contract) or {})
            if f.get("days") and trig["days"] < f["days"]:
                add(
                    "notice",
                    [trig["id"], ref],
                    trig["days"],
                    "days",
                    f"Your letter gives {trig['days']} days' notice ({trig['id']}), but our contract requires "
                    f"{f['days']} days ({ref}).",
                    "timing",
                    0.8,
                )

    out.sort(key=lambda c: -c["strength"])
    for i, c in enumerate(out, 1):
        c["cid"] = f"C{i}"
    return out


def rival_claims(case, threads, sid, ledgers, start):
    """RFQ: another bidder's standing price below this supplier's (reveals it - use deliberately)."""
    out = []
    mine = (ledgers.get(sid) or {}).get("price")
    for other, led in ledgers.items():
        if other == sid or not led.get("offer_id") or led.get("price") is None or mine is None:
            continue
        if led["price"] < mine:
            out.append(
                {
                    "kind": "offer",
                    "ids": [led["offer_id"]],
                    "figure": led["price"],
                    "unit": "EUR",
                    "sentence": f"Another bidder offers this part at {_eur(led['price'])} per unit "
                    f"({led['offer_id']}).",
                    "supports": "price_down",
                    "strength": 0.75,
                    "reveals": True,
                }
            )
    out.sort(key=lambda c: c["figure"])
    for i, c in enumerate(out[:1], start):
        c["cid"] = f"C{i}"
    return out[:1]


def available(catalog, used, blocked_kinds=()):
    """Claims not yet cited in this thread and not of a kind the run learned to avoid."""
    return [c for c in catalog if not (set(c["ids"]) & used) and c["kind"] not in blocked_kinds]
