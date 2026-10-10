"""Value every current supplier sheet; historical offers are never actionable."""

import math

from ..cost import cost_breakdown, must_haves_ok, total_cost
from ..docs import standard_terms
from ..play import ENDED, latest


def current_offers(events):
    message = latest(events)
    if message.get("status") in ENDED:
        return []
    return [s for s in message.get("term_sheets") or [] if s.get("term_sheet_id") and isinstance(s.get("terms"), dict)]


def value(case, terms, standard=None, supplier=None):
    """Known costs and separately labelled relationship estimate (never a hidden supplier fact)."""
    known = cost_breakdown(case, terms, standard)
    model = {**supplier, "extra": supplier.get("extra", 0.0) - effort(terms, supplier)} if supplier else None
    estimate = cost_breakdown(case, terms, standard, model)["relationship"] if model else 0.0
    return {
        "known_cost": round(sum(known.values()), 2),
        "relationship_estimate": round(estimate, 2) if supplier else None,
        "cost": round(sum(known.values()) + estimate, 2),
    }


def effort(terms, model):
    """Estimated supplier effort for tighter lead time/quality; explicitly a prior."""
    std, ref = model.get("standard") or {}, model.get("ref_price") or 0.0
    days = (
        max(0, std["lead_time_days"] - terms["lead_time_days"])
        if std.get("lead_time_days") is not None and terms.get("lead_time_days") is not None
        else 0
    )
    ppm = (
        max(0, std["ppm_commitment"] - terms["ppm_commitment"])
        if std.get("ppm_commitment") is not None and terms.get("ppm_commitment") is not None
        else 0
    )
    return ref * (0.001 * days + 0.00001 * ppm)


def ranked_offers(case, events, supplier=None):
    std = standard_terms(events)
    alt = case["buyer"].get("alternative") or {}
    limit = total_cost(case, alt["terms"]) if alt.get("kind") not in (None, "none") and alt.get("terms") else math.inf
    out = []
    for index, sheet in enumerate(current_offers(events)):
        terms = sheet["terms"]
        if any(f not in terms and f not in std and f not in case.get("fixed_terms", {}) for f in case["negotiable"]):
            continue
        try:
            v = value(case, terms, std, supplier)
            if math.isfinite(v["cost"]) and must_haves_ok(case, terms, std):
                # Only the standing sheet (the first) can be accepted; the arena rejects an alternative's ID
                # with 422 (practice P18). Alternatives are information: mirror their terms to get "agreed".
                out.append({**sheet, **v, "beats_alternative": v["cost"] <= limit, "primary": index == 0})
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            continue
    return sorted(out, key=lambda s: (s["cost"], s["term_sheet_id"]))
