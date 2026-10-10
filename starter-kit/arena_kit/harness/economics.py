"""Shared decision and emergency-close economics. Estimates are never asserted as facts."""

import math

from ..cost import closing_terms, supplier_value, total_cost


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Economics:
    def __init__(self, case, data=None, parameters=None):
        self.case = case
        parameters = parameters or {}
        overrides = parameters.get("case_limits", {}).get(case.get("case_id"), {})
        self.parameters = parameters | overrides
        for key in ("max_unit_price", "max_total_cost", "target_unit_price"):
            value = self.parameters.get(key)
            if key in self.parameters and (not number(value) or value <= 0):
                raise ValueError(f"{key} must be a positive finite number")
        self.estimates = {}
        facts = dict(getattr(data, "facts", {})) | case.get("facts", {})
        # A bundle's weighting is unspecified: do not invent an average part cost.
        parts = case.get("parts", [])
        if len(parts) == 1:
            part = parts[0].split(":")[-1]
            for sid in case.get("suppliers", []):
                specific = f"cost:{part}-{sid.split(':')[-1]}"
                ref = specific if specific in facts else f"cost:{part}"
                fact = facts.get(ref, {})
                cost, unit, width = fact.get("unit_cost"), fact.get("price_unit"), fact.get("confidence_pct")
                if number(cost) and cost > 0 and number(unit) and unit > 0 and number(width) and 0 <= width < 100:
                    # Package docs describe confidence_pct as an error band, not a probability.
                    self.estimates[sid] = {"ref": ref, "cost": cost / unit, "width": width / 100}
        alt = case["buyer"].get("alternative") or {}
        self.alternative = (
            total_cost(case, alt["terms"]) if alt.get("kind") not in (None, "none") and alt.get("terms") else math.inf
        )

    def scenarios(self, sid, standard):
        estimate = self.estimates.get(sid)
        if not estimate or self.case["buyer"].get("category_goal") not in ("stabilise", "secure_supply"):
            return []
        std = closing_terms(self.case, {}, standard)
        # Financing rates are a disclosed sensitivity range, not inferred supplier facts.
        rate = self.case["buyer"]["interest_rate"]
        return [
            {
                "standard": std,
                "full_cost": estimate["cost"] * (1 + delta),
                "interest_rate": interest,
                "usual_rate": rate,
            }
            for delta in (-estimate["width"], estimate["width"])
            for interest in (max(0, rate * 0.5), min(0.5, rate * 2))
        ]

    def cost(self, sid, terms, standard=None):
        try:
            values = [total_cost(self.case, terms, standard)]
            for scenario in self.scenarios(sid, standard):
                if supplier_value(self.case, terms, scenario) <= 0:
                    return math.inf
                values.append(total_cost(self.case, terms, standard, supplier=scenario))
            return max(values) if all(math.isfinite(x) for x in values) else math.inf
        except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
            return math.inf

    def within_limits(self, sid, terms, standard=None):
        price = closing_terms(self.case, terms, standard).get("unit_price")
        if not number(price) or price <= 0:
            return False
        cost = self.cost(sid, terms, standard)
        return (
            math.isfinite(cost)
            and price <= self.parameters.get("max_unit_price", math.inf)
            and cost <= min(self.alternative, self.parameters.get("max_total_cost", math.inf))
        )
