"""The published cost function: what a term sheet costs the buyer in €, plus term helpers.

C covers the buyer's fixed need over a fixed horizon: need the deal does not cover is bought at
the case's fallback price (plus the fallback risk of the category goal), committed volume is bought
needed or not, and units bought before a new price takes effect cost the current price. In
strategic and bottleneck cases a deal that leaves the supplier below a sustainable margin costs
too - pass your estimate of the supplier (`supplier_value`) to price that. Do the arithmetic here,
not in the language model.
"""

import math
import operator
from datetime import date

# Strategic and bottleneck goals: leaving the supplier below a sustainable margin costs.
RELATIONSHIP_GOALS = {"stabilise", "secure_supply"}
OPS = {
    "<=": operator.le,
    "<": operator.lt,
    ">=": operator.ge,
    ">": operator.gt,
    "==": operator.eq,
    "!=": operator.ne,
}


def closing_terms(case, terms, standard=None):
    """The terms a deal would close on: the term sheet, then the supplier's standard terms (from
    its quote PDF) for anything left out, then the case's fixed terms; gaps from the buyer's plan.
    """
    b = case["buyer"]
    freight = b["freight_eur_per_unit"]
    plan = {
        "volume_per_year": b["need_per_year"],
        "contract_months": round(12 * b["horizon_years"]),
        "payment_days": b["reference_payment_days"],
        "lead_time_days": b["need_lead_time_days"],
        # an unknown Incoterm counts as the costliest: a cautious buyer does not assume DAP
        "incoterm": max(freight, key=freight.get),
    }
    t = dict(plan)
    for layer in (standard or {}, case.get("fixed_terms", {}), terms):
        t.update({k: v for k, v in layer.items() if v is not None or k not in plan})
    return t


def horizon_start(case):
    """The horizon starts at the fixed or the earliest possible effective date."""
    fixed = case.get("fixed_terms", {}).get("effective_date")
    options = [o for o in case.get("term_options", {}).get("effective_date", []) if o]
    first = fixed or (min(options) if options else None)
    return date.fromisoformat(first) if first else None


def _years(case, t):
    """Years of the horizon a deal covers; a single order (0 months) counts as one year's lot.
    A commitment beyond the horizon neither costs nor earns."""
    months = t["contract_months"]
    return min(months / 12 if months else 1.0, case["buyer"]["horizon_years"])


def _units_before(case, t, years):
    """Units bought between the horizon start and the date a new price takes effect."""
    start = horizon_start(case)
    if not t.get("effective_date") or start is None:
        return 0.0
    lag = (date.fromisoformat(t["effective_date"]) - start).days / 365
    return t["volume_per_year"] * min(max(lag, 0.0), years)


def supplier_value(case, terms, supplier):
    """What a deal is worth to the supplier per unit, in cash: the price at its standard terms that
    would pay it the same. `supplier` is your estimate of it:
      standard       its standard terms (quote PDF, `docs.standard_terms`)
      interest_rate  what money now is worth to it per year (more to a supplier short of cash)
      usual_rate     its usual cost of money (default: interest_rate); tooling paid up front is
                     worth more to it when cash is dearer than usual
      ppm            the defect rate it really achieves (default: its standard ppm commitment)
      extra          EUR per unit it gains (+) or spends (-) beyond these items, e.g. overtime for
                     a faster delivery or idle capacity a larger volume fills (default 0)
    Contract length and an index clause are security, not cash: they do not count here."""
    b, std = case["buyer"], {**supplier["standard"], **case.get("fixed_terms", {})}
    t = closing_terms(case, terms, std)
    years = _years(case, t)
    sold = t["volume_per_year"] * years
    old = _units_before(case, t, years) / sold  # sold at the current price
    rate = supplier["interest_rate"]
    pay = 1 - rate * (t["payment_days"] - std["payment_days"]) / 360
    cash_now = 1 + (rate - supplier.get("usual_rate", rate)) * years / 2
    freight = b["freight_eur_per_unit"]
    ppm = supplier.get("ppm", std.get("ppm_commitment") or 0)
    return (
        t["unit_price"] * (1 - old) * pay
        + old * (b.get("current_unit_price") or 0.0) * pay
        + freight[t["incoterm"]]
        - freight[std["incoterm"]]  # it carries the freight leg
        + ((t.get("tooling_eur") or 0) - (std.get("tooling_eur") or 0)) / sold * cash_now
        - ppm / 1e6 * ((t.get("chargeback_eur_per_complaint") or 0) - (std.get("chargeback_eur_per_complaint") or 0))
        + supplier.get("extra", 0.0)
    )


def _soft(gap):
    """Margin points below the sustainable margin, as they cost: none above it, quadratic over
    the first 5 points, linear beyond - no kink."""
    if gap <= 0:
        return 0.0
    return gap * gap / 10 if gap <= 5 else gap - 2.5


def relationship_cost(case, terms, standard=None, supplier=None, value=None):
    """Strategic and bottleneck goals only: EUR for every margin point the deal leaves the supplier
    below `sustainable_margin_pct`, per year covered. `supplier["full_cost"]` (EUR per unit) is your
    estimate of its full cost; `value` overrides `supplier_value`. Without an estimate: 0."""
    b = case["buyer"]
    per_point, sustainable = b.get("relationship_eur_per_point"), b.get("sustainable_margin_pct")
    if b.get("category_goal") not in RELATIONSHIP_GOALS or not per_point or not sustainable or not supplier:
        return 0.0
    t = closing_terms(case, terms, standard)
    worth = value if value is not None else supplier_value(case, t, supplier)
    margin = (worth - supplier["full_cost"]) / worth * 100
    return per_point * _soft(sustainable - margin) * _years(case, t)


def cost_breakdown(case, terms, standard=None, supplier=None):
    """C by part. `standard`: the supplier's standard terms for anything the term sheet leaves out.
    `supplier`: your estimate of the supplier for the relationship term (`supplier_value`)."""
    b, t = case["buyer"], closing_terms(case, terms, standard)
    if t.get("unit_price") is None:
        raise ValueError("no unit_price: a term sheet without a price has no cost")
    years = _years(case, t)
    committed = t["volume_per_year"] * years  # bought, needed or not
    covered = min(t["volume_per_year"], b["need_per_year"]) * years
    pre = _units_before(case, t, years)
    at_new_price = (committed - pre) * t["unit_price"]
    spend = pre * (b.get("current_unit_price") or t["unit_price"]) + at_new_price
    per_defect = b.get("cost_per_defect", 0)
    defect_cost = per_defect - min(t.get("chargeback_eur_per_complaint") or 0, per_defect)
    index = index_risk = 0.0
    if clause := t.get("index_clause"):
        path = b.get("index_paths", {}).get(clause["series"], 0.0)
        moved = max(0.0, abs(path) - clause["deadband_pct"] / 100)
        index = at_new_price * clause["material_share"] * moved * math.copysign(1, path)
        index_risk = at_new_price * clause["material_share"] * b.get("index_risk_pct", 0) / 100
    uncovered = (b["need_per_year"] * b["horizon_years"] - covered) * b["fallback_unit_price"]
    need = b["need_lead_time_days"]
    return {
        "price": spend,
        "uncovered_need": uncovered,
        "fallback_risk": uncovered * b.get("fallback_risk_pct", 0) / 100,
        "payment_terms": spend * b["interest_rate"] * (b["reference_payment_days"] - t["payment_days"]) / 360,
        "freight": committed * b["freight_eur_per_unit"][t["incoterm"]],
        "late_delivery": b.get("late_cost_per_day", 0) * max(0, t["lead_time_days"] - need),
        "quality": committed * (t.get("ppm_commitment") or 0) / 1e6 * defect_cost,
        "tooling": t.get("tooling_eur") or 0.0,
        "index_clause": index,
        "index_risk": index_risk,
        "relationship": relationship_cost(case, t, supplier=supplier),
    }


def total_cost(case, terms, standard=None, supplier=None):
    """C in EUR; infinite for terms without a price (e.g. a supplier that quotes none), so such a
    sheet is never the cheapest and never beats an alternative."""
    if closing_terms(case, terms, standard).get("unit_price") is None:
        return math.inf
    return round(sum(cost_breakdown(case, terms, standard, supplier).values()), 2)


def score(c_open, c_deal, c_best):
    if c_deal is None:
        return 0.0
    if c_open - c_best <= 0.01:  # no room to negotiate: matching the best is full marks
        return 1.0 if c_deal <= c_best + 0.01 else 0.0
    return min(1.0, max(0.0, (c_open - c_deal) / (c_open - c_best)))


def must_haves_ok(case, terms, standard=None):
    t = closing_terms(case, terms, standard)
    for m in case["buyer"].get("must_haves", []):
        value = t.get(m["field"])
        if value is None and m["op"] not in ("==", "!="):
            return False
        if not OPS[m["op"]](value, m["value"]):
            return False
    return True


def snap(case, field, value):
    """The allowed value closest to `value` (case `term_options`), else `value` unchanged."""
    options = case.get("term_options", {}).get(field)
    if not options or value in options:
        return value
    if isinstance(value, int | float) and all(isinstance(o, int | float) for o in options):
        return min(options, key=lambda o: (abs(o - value), o))
    return options[0]


def complete_offer(case, terms):
    """A valid term sheet: exactly the negotiable fields, gaps from the buyer's plan, snapped."""
    plan = closing_terms(case, {"unit_price": None})
    freight = case["buyer"]["freight_eur_per_unit"]
    plan["incoterm"] = min(freight, key=freight.get)  # when asking, ask for the cheapest freight
    out = {}
    for field in case["negotiable"]:
        value = terms.get(field, plan.get(field))
        if value is None and case.get("term_options", {}).get(field):
            value = case["term_options"][field][0]
        value = snap(case, field, value)
        out[field] = round(value, 2) if field == "unit_price" and value is not None else value
    return out
