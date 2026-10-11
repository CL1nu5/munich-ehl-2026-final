"""Offer Builder: valid term sheets of equal value to the supplier, cheapest for us first.

For every combination of the non-price levers the price is solved so that the package is worth a
target amount to the supplier (`cost.supplier_value`: a cash-short supplier values early payment).
The target walks from our aspiration towards the supplier's standing offer along a concession
schedule; `trade` packages are worth exactly as much to it as its own offer but cost us less.
"""

import itertools
import math

from arena_kit.cost import RELATIONSHIP_GOALS, must_haves_ok, supplier_value
from arena_kit.harness.valuation import effort, value

from . import priors

MAX_COMBOS = 300
PER_LEVEL = 8
# Own-plant should-cost estimates ran about 2 % above the full cost the arena's relationship term implied
# (practice P01, P02, P03 results: 130.0 vs 132.6, 367.4 vs 375.0, 394.7 vs 403.2).
SHOULD_COST_BIAS = 0.02
ANCHOR_FLOOR = 0.65  # never ask below this share of the standing price


def lever_domains(case, std):
    """{field: [allowed values]} for the non-price negotiable fields, or None if one has none."""
    out = {}
    for field in case["negotiable"]:
        if field == "unit_price":
            continue
        values = list(case.get("term_options", {}).get(field) or [])
        if field in std and std[field] not in values:
            values.append(std[field])
        if not values:
            return None
        out[field] = values
    return out


def _prune(domains, base):
    """Keep the product small: shrink the longest field to a spread (its ends, middle and the supplier's
    own value) until the product fits."""
    cap = 4
    orig = {f: list(v) for f, v in domains.items()}
    while math.prod(len(v) for v in domains.values()) > MAX_COMBOS and cap >= 1:
        field = max(domains, key=lambda f: len(domains[f]))
        if len(domains[field]) <= cap:
            cap -= 1
            continue
        values = orig[field]  # always spread over the full option list, not an already pruned one
        keep = [base.get(field)] if base.get(field) in values else []
        spread = [values[0], values[-1], values[len(values) // 2], *values[1:-1]]
        for v in spread:
            if len(keep) >= cap:
                break
            if v not in keep:
                keep.append(v)
        domains[field] = keep
    return domains


def supplier_standard(case, led):
    """Its standard terms for valuation: stated at the opening, then the standard section, plan gaps."""
    b = case["buyer"]
    std = {**{k: v for k, v in (led.get("stated_open") or {}).items() if k != "unit_price"}, **(led.get("std") or {})}
    std.setdefault("payment_days", b["reference_payment_days"])
    if "incoterm" not in std:
        std["incoterm"] = case.get("fixed_terms", {}).get("incoterm") or "EXW"
    return std


def should_cost_row(parts_dossier, sid):
    """The should-cost estimate of this supplier's own plant, else a generic one (no supplier), else None.
    Another supplier's plant estimate says nothing about this one's costs."""
    rows = [
        sc
        for part in (parts_dossier or {}).values()
        for sc in part.get("should_cost", [])
        if sc.get("unit_cost") and sc.get("price_unit") in (None, 1)
    ]
    for pick in ([r for r in rows if r.get("supplier_id") == sid], [r for r in rows if not r.get("supplier_id")]):
        if pick:
            return min(pick, key=lambda r: r["unit_cost"])
    return None


FULL_BOOK_LEAD_EFFORT = 0.003


def supplier_model(case, led, tags, dossier_sup, parts_dossier):
    """Our estimate of the supplier for `supplier_value` / the relationship term."""
    model = {
        "standard": supplier_standard(case, led),
        "interest_rate": priors.rate_for(tags),
        "usual_rate": 0.07,
        "ref_price": led.get("price") or 0.0,
    }
    if "full_order_book" in tags:
        # Sold out: a shorter lead time than its own means overtime (practice: two such suppliers charged 0.2-0.5 %
        # of the price per day, "a shorter Lieferzeit means overtime; that is priced in", against our 0.1 % prior).
        model["lead_effort"] = FULL_BOOK_LEAD_EFFORT
    b = case["buyer"]
    if b.get("category_goal") in RELATIONSHIP_GOALS:
        row = should_cost_row(parts_dossier, led.get("supplier_id"))
        # Generic plant estimates are a prior, not a supplier's revealed cost floor.
        own_plant = bool(row and row.get("supplier_id") == led.get("supplier_id"))
        full = row["unit_cost"] * (1 - SHOULD_COST_BIAS) if own_plant else None
        opening = next((p for p in led.get("prices", []) if p), led.get("price"))
        sustainable = b.get("sustainable_margin_pct") or 8
        if full is None and opening:
            full = opening * (1 - (sustainable + 2) / 100)
        if led.get("breakdown_total"):
            full = led["breakdown_total"]
        if full and opening:
            # A supplier quotes at least its sustainable margin at its own standard terms: an estimate
            # above that is a bad estimate (practice P02: should-cost above the quote emptied the menu).
            full = min(full, opening * (1 - sustainable / 100))
        if full:
            model["full_cost"] = full
            model["cost_uncertainty_pct"] = (row or {}).get("confidence_pct", 15)
            model["cost_source"] = row.get("id") if own_plant else "opening-price prior"
    return model


def _value(case, terms, model):
    try:
        return supplier_value(case, terms, {**model, "extra": model.get("extra", 0.0) - effort(terms, model)})
    except Exception:
        return None


def solve_price(case, levers, model, target):
    """Unit price (2 decimals) that makes the package worth `target` to the supplier."""
    v0 = _value(case, {**levers, "unit_price": 0.0}, model)
    v1 = _value(case, {**levers, "unit_price": 1.0}, model)
    if v0 is None or v1 is None or v1 - v0 <= 1e-9:
        return None
    price = (target - v0) / (v1 - v0)
    return round(price, 2) if price > 0 else None


def cost(case, terms, std, model):
    try:
        sup = None
        if case["buyer"].get("category_goal") in RELATIONSHIP_GOALS and model.get("full_cost"):
            sup = model
        c = value(case, terms, std, sup)["cost"]
        return c if math.isfinite(c) else None
    except Exception:
        return None


def anchor_price(case, led, tags, dossier, drop, rivals=None, model=None, std=None):
    """First ask: ambitious but defensible.

    - the standing price minus `drop` (scaled by what the supplier's situation allows);
    - in an increase/renewal: at or below what we pay today - the increase still has to be earned;
    - in an RFQ: below the cheapest rival bid (`rivals`: other bidders' standing prices);
    - never below this supplier's own should-cost band, nor below the price where squeezing it starts to
      cost us more than it saves (relationship goals, via our cost model), nor below ANCHOR_FLOOR.
    """
    standing = led["price"]
    b = case["buyer"]
    factor = 1.0
    for t in tags:
        factor *= {
            "full_order_book": 0.5,
            "not_strategic": 0.7,
            "price_frozen": 0.5,
            "spare_capacity": 1.3,
            "weak_costing": 1.2,
            "cash_short": 0.8,
            "hedged_forward": 1.2,
            "open_books": 1.1,
        }.get(t, 1.0)
    cut = min(0.35, drop * factor)
    target = standing * (1 - cut)
    current = b.get("current_unit_price")
    if current and case.get("type") in ("price_increase", "renewal", "raw_material") and current < standing:
        target = min(target, current * (1 - cut / 3))
    cheapest_rival = min((p for p in (rivals or []) if p), default=None)
    if cheapest_rival:
        target = min(target, cheapest_rival * (1 - cut))
    row = should_cost_row(dossier.get("parts"), led.get("supplier_id"))
    if row and row.get("supplier_id") == led.get("supplier_id"):
        lower = row["unit_cost"] * (1 - (row.get("confidence_pct") or 15) / 100)
        # Even an own-plant estimate cannot contradict the supplier's observed offer.
        target = max(target, min(lower, standing * (1 - drop)))
    if model and model.get("full_cost") and std is not None:
        target = max(target, cheapest_price(case, led, model, std, target, standing))
    return round(max(min(target, standing), standing * ANCHOR_FLOOR), 2)


def cheapest_price(case, led, model, std, low, high):
    """The price at the supplier's standing levers below which our modelled cost rises again (squeezing
    a relationship supplier below its sustainable margin costs more than it saves)."""
    terms = dict(led.get("terms") or {})
    if not terms or high <= low:
        return low

    def c(p):
        return cost(case, {**terms, "unit_price": round(p, 2)}, std, model) or math.inf

    best_p, best_c = high, c(high)
    for i in range(41):
        p = low + (high - low) * i / 40
        v = c(p)
        if v < best_c - 1e-6:
            best_p, best_c = p, v
    return best_p


def price_for_cost(case, levers, std, model, c_target, lo, hi):
    """Unit price (2 decimals) at which `levers` cost us `c_target` (bisection: the relationship term is
    not linear), or None if no price in [lo, hi] gets there."""

    def c(p):
        return cost(case, {**levers, "unit_price": round(p, 2)}, std, model)

    c_lo, c_hi = c(lo), c(hi)
    if c_hi is not None and (c_lo is None or c_lo > c_target):
        # With a relationship goal C is not monotonic: far below the supplier's cost the margin penalty makes
        # a lower price dearer for us. Start from the cheapest price on a grid (practice P02: a repriced index
        # clause was dropped because half the price cost more than the target).
        grid = [(c(lo + (hi - lo) * i / 40), lo + (hi - lo) * i / 40) for i in range(41)]
        grid = [(cg, p) for cg, p in grid if cg is not None]
        if grid:
            c_lo, lo = min(grid)
    if c_lo is None or c_hi is None or c_lo > c_target:
        return None
    if c_hi <= c_target:
        return round(hi, 2)
    for _ in range(40):
        mid = (lo + hi) / 2
        cm = c(mid)
        if cm is None:
            return None
        lo, hi = (mid, hi) if cm <= c_target else (lo, mid)
    return math.floor(lo * 100) / 100


def schedule(k, horizon):
    """Share of the way from our aspiration to its offer after k of our messages (slow early)."""
    if horizon <= 1:
        return 1.0
    return min(1.0, (k / (horizon - 1)) ** 2)


def build_menu(case, led, model, std, v_asp, v_stand, levels, must_beat):
    """Candidates [{mid, level, terms, c, saving, price, changes}] for the given {level: share} map."""
    domains = lever_domains(case, std)
    if domains is None or v_asp is None or v_stand is None:
        return []
    base = model["standard"]
    domains = _prune(domains, base)
    names = list(domains)
    combos = [dict(zip(names, values, strict=True)) for values in itertools.product(*domains.values())] or [{}]
    menu, seen = [], set()
    for level, share in levels.items():
        target = v_asp + (v_stand - v_asp) * share
        found = []
        for levers in combos:
            price = solve_price(case, levers, model, target)
            if price is None:
                continue
            terms = {**levers, "unit_price": price}
            terms = {f: terms[f] for f in case["negotiable"]}
            if not must_haves_ok(case, terms, std):
                continue
            c = cost(case, terms, std, model)
            if c is None or c >= must_beat - 0.5:
                continue
            changes = sum(levers.get(k) != base.get(k) for k in levers)
            found.append((c, changes, terms))
        found.sort(key=lambda x: (x[0], x[1]))  # ties: stay close to the supplier's own terms
        # Keep each lever's alternatives represented before filling with cheap near-duplicates.
        # Supplier security (indexation/duration) is deliberately outside supplier_value's cash formula.
        diverse, represented = [], set()
        for candidate in found:
            terms = candidate[2]
            features = {(f, str(terms.get(f))) for f in names}
            if not diverse or features - represented:
                diverse.append(candidate)
                represented |= features
        ordered = diverse + [x for x in found if x not in diverse]
        picked = 0
        for c, _, terms in ordered:
            key = tuple(sorted((k, str(v)) for k, v in terms.items()))
            profile = tuple(sorted((k, str(v)) for k, v in terms.items() if k != "unit_price"))
            same_level = [m for m in menu if m["level"] == level]
            if key in seen or profile in {m["profile"] for m in same_level}:
                continue
            seen.add(key)
            menu.append(
                {
                    "level": level,
                    "terms": terms,
                    "c": round(c, 2),
                    "saving": round(must_beat - c, 2),
                    "price": terms.get("unit_price"),
                    "changes": {k: v for k, v in terms.items() if k != "unit_price" and base.get(k) != v},
                    "profile": profile,
                }
            )
            picked += 1
            if picked >= PER_LEVEL:
                break
    for i, m in enumerate(menu, 1):
        m["mid"] = f"M{i}"
        m.pop("profile", None)
    return menu


def describe(case, terms):
    """One human line per sheet, written by code (figures never come from the model)."""
    parts = [f"unit price EUR {terms['unit_price']:.2f}"] if terms.get("unit_price") is not None else []
    labels = {
        "payment_days": lambda v: f"payment {v} days net",
        "lead_time_days": lambda v: f"lead time {v} days",
        "volume_per_year": lambda v: f"{v} pieces a year",
        "contract_months": lambda v: "single order" if v == 0 else f"contract {v} months",
        "effective_date": lambda v: f"valid from {v}",
        "incoterm": lambda v: f"Incoterm {v}",
        "tooling_eur": lambda v: f"tooling EUR {v:,.0f}",
        "ppm_commitment": lambda v: f"quality commitment {v} ppm",
        "chargeback_eur_per_complaint": lambda v: f"chargeback EUR {v} per complaint",
        "index_clause": lambda v: (
            "no index clause"
            if not v
            else f"index clause on {v.get('series')} ({round(100 * v.get('material_share', 0))}% material)"
        ),
    }
    for field in case["negotiable"]:
        if field != "unit_price" and field in terms:
            fmt = labels.get(field, lambda v, f=field: f"{f.replace('_', ' ')} {v}")
            try:
                parts.append(fmt(terms[field]))
            except Exception:
                parts.append(f"{field} {terms[field]}")
    return ", ".join(parts)
