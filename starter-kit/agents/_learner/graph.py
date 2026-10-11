"""One decide() as a LangGraph state graph (with a plain sequential runner as fallback).

observe -> route -> close (reflection follows the confirmed result in the harness)
                 -> menu -> negotiator | strategist -> writer | det -> critic
"""

import math
import time
from typing import Any, TypedDict

from arena_kit.cost import closing_terms, must_haves_ok, total_cost
from arena_kit.docs import standard_terms
from arena_kit.harness.validation import close_action, validate

from . import claims as claims_mod
from . import dossier as dossier_mod
from . import ledger as ledger_mod
from . import memory, offers, policy, preferences, prompts, writer
from .priors import advice_for

REFRESH_MOVE = 1.5
ASK_TOL = 0.001  # an offer "meets our ask" only if it also costs us no more than that ask
# Its offer is this close to our last ask, as a share of the way from the opening to that ask: another round
# can gain at most that little but risks a final offer or a walk-out (practice P05: a bidder 0.7 % above our
# ask, the cheapest in the RFQ, walked away after our next small step).
NEAR_ASK = 0.05
MAX_LATENCY = 120  # a supplier answers within about a minute
# suppliers issue final offers in rounds 2-5; pacing over 6 rounds avoids missing the deal:
DEFAULT_PACING_HORIZON = 6


class Ctx(TypedDict, total=False):
    obs: Any
    case: Any
    threads: Any
    ready: Any
    state: Any
    services: Any
    p: Any
    cc: Any
    budget: Any
    gw: Any
    ledgers: Any
    c_alt: Any
    mem: Any
    route: Any
    menus: Any
    fulls: Any
    mirrors: Any
    explore: Any
    claims: Any
    shares: Any
    vals: Any
    mode: Any
    choices: Any
    actions: Any


def tags_of(state):
    return {sid: s.get("tags") or [] for sid, s in (state.get("sup") or {}).items()}


def _cost_fn(services):
    return services.cost if services is not None else total_cost


# --- observe ---------------------------------------------------------------------------------------


def observe(ctx):
    case, threads, state = ctx["case"], ctx["threads"], ctx["state"]
    leds = ledger_mod.build(case, threads)
    dossier = ctx["cc"]["dossier"]
    models = {
        sid: offers.supplier_model(
            case,
            led,
            state["sup"].get(sid, {}).get("tags") or [],
            dossier["suppliers"].get(sid) or {},
            dossier["parts"],
        )
        for sid, led in leds.items()
    }
    leds = ledger_mod.build(case, threads, models)
    now = time.time()
    for sid, led in leds.items():
        s = state["sup"].setdefault(sid, {"tags": []})
        s["preferences"] = preferences.observe(case, threads[sid], s.get("preferences"))
        seen = len(led["prices"])
        if s.get("seen", 1) < seen:
            lat = now - s["sent_at"] if s.get("sent_at") else None
            if lat is not None and 0 <= lat <= MAX_LATENCY:  # longer = a restart or outage, not the supplier
                s["lat"] = (s.get("lat") or [])[-4:] + [round(lat, 1)]
            for entry in reversed(state["log"]):
                if entry.get("sup") == sid and "out" not in entry:
                    pred = entry.get("pred") or {}
                    entry["out"] = {
                        "status": led["status"],
                        "price": led["price"],
                        "move_pct": led["move_pct"],
                        "claims": led["claim_results"],
                        "pred_ok": pred.get("status") == led["status"] if pred.get("status") else None,
                    }
                    break
        s["seen"] = seen
        if led["prices"] and led["prices"][0] is not None:
            s.setdefault("open_price", led["prices"][0])
    c_alt = policy.alternative_cost(case, _cost_fn(ctx["services"]))
    mem = memory.refresh(case, ctx["p"], tags_of(state), ctx["cc"], ctx["services"])
    state["mem"] = {
        "run": mem.get("run"),
        "episodes": mem.get("episodes", 0),
        "lessons": [x["key"] for x in mem.get("lessons", [])],
    }
    if "c_open" not in state:
        state["c_open"] = _opening_cost(case, threads)
    return {"ledgers": leds, "c_alt": c_alt, "mem": mem}


def _opening_cost(case, threads):
    best = None
    for events in threads.values():
        first = next((m for m in events if m.get("from") != "buyer"), None)
        sheets = (first or {}).get("term_sheets") or []
        if sheets:
            try:
                c = total_cost(case, sheets[0]["terms"], standard_terms(events))
                if math.isfinite(c):
                    best = c if best is None else min(best, c)
            except Exception:
                pass
    return best


# --- route / close / reflect ---------------------------------------------------------------------


def route(ctx):
    state = ctx["state"]
    lat = [x for s in state["sup"].values() for x in s.get("lat") or []]
    last = ctx["budget"].last_round(max(lat) if lat else 45)
    knobs = ctx["mem"].get("knobs") or {}
    r = policy.route(ctx["case"], ctx["ledgers"], ctx["ready"], ctx["c_alt"], knobs, last)
    return {"route": r}


def close(ctx):
    case, threads, r, state = ctx["case"], ctx["threads"], ctx["route"], ctx["state"]
    action = {"supplier_id": r["supplier_id"], "message": {"action": r["action"]}}
    try:
        validate(case, threads, [action])
    except Exception:
        action = close_action(case, threads)
        r = {**r, "reason": r.get("reason", "") + " (harness close)"}
    led = ctx["ledgers"].get(action["supplier_id"]) or {}
    kind = action["message"]["action"]["type"]
    state["close_requested"] = True
    state["close"] = {
        "type": kind,
        "supplier_id": action["supplier_id"],
        "reason": r.get("reason"),
        "c": led.get("c") if kind == "accept" else ctx["c_alt"],
        "c_open": state.get("c_open"),
        "c_alt": ctx["c_alt"],
    }
    return {"actions": [action], "route": r}


# --- menu -----------------------------------------------------------------------------------------


def _llm_ok(ctx):
    st = ctx["state"]
    return bool(ctx["p"].get("llm")) and ctx["services"] is not None and st["llm"].get("fails", 0) < 2


def _logged_ids(state):
    """IDs we cited, from our own log: covers messages whose reply has not arrived yet (the engine adds
    a message to the thread only with its reply). Returns (case-wide IDs, {supplier: quote/offer IDs})."""
    case_wide, per_sup = set(), {}
    for e in state.get("log") or []:
        ids = set(e.get("refs") or []) | {i for c in e.get("claims") or [] for i in c.get("ids") or []}
        case_wide |= {i for i in ids if not i.startswith(claims_mod.PER_SUPPLIER)}
        per_sup.setdefault(e.get("sup"), set()).update(i for i in ids if i.startswith(claims_mod.PER_SUPPLIER))
    return case_wide, per_sup


def _holds(led):
    """Its replies in a row (after the opening quote) that kept the previous price."""
    prices = [p for p in (led.get("prices") or [])[1:] if p is not None]
    n = 0
    for a, b in zip(reversed(prices[:-1]), reversed(prices[1:]), strict=False):
        if abs(a - b) > 1e-9:
            break
        n += 1
    return n


def _stalled(led, rounds=2):
    prices = [p for p in led.get("prices") or [] if p is not None]
    return len(prices) > rounds and len(set(prices[-(rounds + 1) :])) == 1


def menu(ctx):
    case, state, cc, r = ctx["case"], ctx["state"], ctx["cc"], ctx["route"]
    leds, knobs = ctx["ledgers"], ctx["mem"].get("knobs") or {}
    dossier = cc["dossier"]
    used = claims_mod.used_ids(ctx["threads"])
    logged, logged_per_sup = _logged_ids(state)
    menus, claims, shares, vals, fulls, mirrors, explore = {}, {}, {}, {}, {}, {}, {}
    trig = dossier.get("trigger") or {}
    holds = r.setdefault("hold", {})

    def accept_ready(reason):
        best = policy.best_acceptable({x: leds[x] for x in ctx["ready"] if not leds[x].get("ended")})
        if not best:
            return None
        return {
            "route": policy._close("accept", best[1], leds[best[1]]["offer_id"], reason),
            "menus": {},
            "claims": {},
            "shares": {},
        }

    for sid in r["suppliers"]:
        led, s = leds[sid], state["sup"][sid]
        if holds.get(sid):  # its final offer stands as our fallback: a holding note, no sheets, no claims
            menus[sid], shares[sid], claims[sid] = [], {}, []
            continue
        tags = s.get("tags") or []
        final_push = r["final_push"].get(sid, False)
        model = led["model"]
        std = led["std"]
        v_stand = offers._value(case, led["terms"], model) if led["terms"].get("unit_price") else None
        if v_stand is not None and s.get("v_asp") is None:
            drop = ctx["p"]["anchor_drop"] * (knobs.get("drop_mult") or {}).get(sid, 1.0)
            rivals = [o["price"] for x, o in leds.items() if x != sid and o.get("acceptable") and o.get("price")]
            anchor = offers.anchor_price(case, led, tags, dossier, drop, rivals, model, std)
            s["v_asp"] = offers._value(case, {"unit_price": anchor}, model)
            s["anchor"] = anchor
        v_asp = s.get("v_asp")
        tol = 1e-6 * max(1.0, abs(v_stand or 0.0))
        # its offer is already worth no more to it than what we last asked for: take it
        if (
            led.get("acceptable")
            and led["status"] == "open"
            and led["rounds"] > 0
            and v_stand is not None
            and s.get("v_ask") is not None
            and (
                v_stand <= s["v_ask"] + tol
                or any(
                    closing_terms(case, asked, std) == closing_terms(case, led["terms"], std)
                    for asked in (led.get("our_sheets") or [[]])[-1]
                )
            )
            # Equal supplier value can still cost the buyer more when nonprice terms change.
            and (s.get("c_ask") is None or (led["c"] is not None and led["c"] <= s["c_ask"] * (1 + ASK_TOL)))
        ):
            out = accept_ready("its offer meets our last ask")
            if out:
                return out
        if (
            led.get("acceptable")
            and led["status"] == "open"
            and led["rounds"] >= 2
            and led["c"] is not None
            and s.get("c_ask") is not None
            and state.get("c_open")
            and state["c_open"] > s["c_ask"]
            and led["c"] - s["c_ask"] <= NEAR_ASK * (state["c_open"] - s["c_ask"])
        ):
            out = accept_ready("its offer is within reach of our last ask")
            if out:
                return out
        alt = led.get("alt_better")
        if alt and led["moves_left"] >= 1 and (led["status"] == "final_offer" or final_push):
            # Its cheaper alternative cannot be accepted by ID: send its own terms back, it answers "agreed".
            mirror = _mirror(case, alt, std)
            if mirror:
                menus[sid], shares[sid], claims[sid] = [mirror], {"trade": 1.0}, []
                r["final_push"][sid] = True
                mirrors[sid] = True
                continue
        if v_asp is not None and v_stand is not None and v_asp >= v_stand:
            v_asp = v_stand * 0.995
        k = led["rounds"]
        horizon = min(
            ctx["budget"].horizon(k, led["moves_left"], s.get("lat") or []),
            (knobs.get("horizon") or {}).get(sid, DEFAULT_PACING_HORIZON),
        )
        levels = {}
        if v_asp is not None and v_stand is not None:
            values = concession_values(
                s, k, horizon, v_asp, v_stand, (knobs.get("drop_mult") or {}).get(sid, 1.0), _holds(led)
            )
            if final_push or led["status"] == "final_offer":
                values["trade"] = v_stand
            span = v_stand - v_asp
            levels = {name: (v - v_asp) / span if span > 0 else 1.0 for name, v in values.items()}
            vals[sid] = (v_asp, v_stand)
            s["v_stand_prev"] = v_stand
        must_beat = (offers.cost(case, led["terms"], std, model) if led["terms"] else None) or led["c"]
        must_beat = must_beat if must_beat is not None else math.inf
        if alt:  # never ask for more than a package it already offers us
            must_beat = min(must_beat, alt["cost"])
        pref = _with_quoted_index(case, led, _with_alt_levers(case, led, s.get("preferences") or {}))
        full = offers.build_menu(case, led, model, std, v_asp, v_stand, levels, must_beat)
        fulls[sid] = full
        banded = within_band(full, case, std, model, pref, led.get("price"))
        m = preferences.rank(_robust(case, banded, std, model, led.get("price"), pref), pref)
        probe = _index_probe(case, led, full, m, std, model, pref) if led["rounds"] == 0 and not final_push else None
        if probe:
            m.append(probe)
            explore[sid] = probe["mid"]
        rival_c = _best_rival_cost(leds, sid)
        if rival_c is not None and m and not final_push:
            m = _undercut(case, m, std, model, rival_c * (1 - RIVAL_UNDERCUT))
        if not m and levels and led["terms"].get("unit_price") and not final_push:
            m = _fallback_sheet(case, led, std, model, must_beat)
        if final_push and not [x for x in m if x["level"] == "trade"]:
            r["final_push"][sid] = False  # nothing to trade: do not push a final offer
            others_open = [
                x for x, o in leds.items() if x != sid and not o.get("ended") and o["status"] not in ("final_offer",)
            ]
            if others_open and led["moves_left"] >= 2:
                holds[sid] = True
                menus[sid], shares[sid], claims[sid] = [], {}, []
                continue
            if led.get("acceptable"):
                out = accept_ready("final offer (no trade left)")
                if out:
                    return out
            m = [x for x in m if x["level"] in ("hold", "step")]
        late = k >= horizon - 1
        nothing_new = not [x for x in m if x["level"] in ("step", "big", "trade")]
        if led.get("acceptable") and k > 0 and not final_push:
            # the end game has nothing left to ask for, or it has stopped moving after our budget
            if (late and nothing_new) or (late and _stalled(led)):
                out = accept_ready("nothing left to gain" if nothing_new else "supplier stopped moving")
                if out:
                    return out
        menus[sid], shares[sid] = m, levels
        asked = trig.get("asked_pct")
        if asked is None and case["buyer"].get("current_unit_price") and led.get("price"):
            asked = (led["price"] / case["buyer"]["current_unit_price"] - 1) * 100
        catalog = claims_mod.build_catalog(case, cc["facts"], dossier, sid, led.get("price"), asked)
        # Index falls stay in the catalogue for suppliers hedged forward: they answer "not our reality", yet their
        # first counter came lower with them (two stainless-bar suppliers, 21 runs each: 5.36-5.46 with,
        # 5.42-5.69 without; 5.4-5.6 with, 5.5-5.9 without).
        catalog += claims_mod.rival_claims(case, ctx["threads"], sid, leds, len(catalog) + 1)
        seen = used.get(sid, set()) | logged | logged_per_sup.get(sid, set())
        claims[sid] = claims_mod.available(catalog, seen, knobs.get("blocked_claims") or [])
    refresh = any(
        leds[sid]["rounds"] == 0
        or (leds[sid].get("statuses") or [None])[-1] != (state["sup"][sid].get("last_status"))
        or "contradicted" in (leds[sid].get("claim_results") or [])
        or leds[sid].get("attachments")
        or abs(leds[sid].get("move_pct") or 0) > REFRESH_MOVE
        for sid in r["suppliers"]
    )
    pending = [s for s, led in leds.items() if not led.get("ended") and s not in ctx["ready"]]
    mode = ctx["budget"].mode(_llm_ok(ctx), refresh, ctx["p"].get("two_call") and not pending)
    if len(ctx["ready"]) >= SERIAL_READY and mode in ("merged", "two"):
        # The arena answers the messages of one session one after another (~18 s each): with three suppliers
        # to answer at once, a 15 s model call let the third supplier's 60 s window run out (practice RFQ:
        # replies after 36, 54 and 72 s, the third recorded as our timeout). Decide without the model.
        mode = "det"
    if mirrors and all(mirrors.get(sid) or holds.get(sid) for sid in r["suppliers"]):
        mode = "det"  # sending its own package back needs no model
    return {
        "menus": menus,
        "claims": claims,
        "shares": shares,
        "vals": vals,
        "mode": mode,
        "fulls": fulls,
        "mirrors": mirrors,
        "explore": explore,
    }


SERIAL_READY = 3  # this many suppliers to answer at once: no model call (see menu)
MENU_BAND = 0.003  # sheets of one level may cost us at most this much more than the level's cheapest


def within_band(menu, case=None, std=None, model=None, pref=None, standing_price=None):
    """Of equal-value sheets the supplier takes the one dearest for us (practice P05: 14 days' payment,
    P08/P18: the index clause, P10/P11: lead time and ppm): offer only sheets that cost us about the same.
    A lever the supplier has insisted on (`pref`) is kept - repriced so that it, too, costs us the level's
    cheapest C: an index clause then comes with a lower base price."""
    floor = {}
    for m in menu:
        floor[m["level"]] = min(floor.get(m["level"], m["c"]), m["c"])
    out = [m for m in menu if m["c"] <= floor[m["level"]] * (1 + MENU_BAND)]
    fields = (pref or {}).get("fields") or {}
    if not fields or case is None:
        return out
    hi = standing_price or max((m["price"] or 0) for m in menu) if menu else 0
    for level, c_floor in floor.items():
        if any(all(m["terms"].get(f) in p["values"] for f, p in fields.items()) for m in out if m["level"] == level):
            continue
        wanted = [
            m for m in menu if m["level"] == level and all(m["terms"].get(f) in p["values"] for f, p in fields.items())
        ]
        best = min(wanted, key=lambda m: m["c"]) if wanted else _with_fields(case, menu, level, fields, std)
        if best is None:
            continue
        levers = {k: v for k, v in best["terms"].items() if k != "unit_price"}
        price = offers.price_for_cost(case, levers, std, model, c_floor, 0.5 * (hi or best["price"]), best["price"])
        if price and price > 0:
            terms = {**best["terms"], "unit_price": price}
            c = offers.cost(case, terms, std, model)
            if c is not None:
                # A sheet built from the level's cheapest one would share its ID, and the critic, which
                # maps IDs to sheets, then sent the cheapest sheet twice instead of the repriced one.
                mid = best["mid"] if best["mid"] not in {m["mid"] for m in out} else f"R{len(out) + 1}"
                out.append({**best, "terms": terms, "price": price, "c": round(c, 2), "mid": mid})
    return out


ROBUST_BAND = 0.003


def _robust(case, menu, std, model, standing_price, pref):
    """Keep the sheets whose levers are also cheap for us at the supplier's own price, cheapest first.

    Suppliers mostly answer with their own price on the levers we sent: in practice P03 a sheet with 30 days'
    payment at a lower price came back as a final offer with 30 days' payment at almost its old price - dearer
    for us than its 90-day offer before. A lever the supplier insists on (`pref`) stays."""
    if not standing_price or not menu:
        return menu
    fields = (pref or {}).get("fields") or {}
    scored = []
    for m in menu:
        c = offers.cost(case, {**m["terms"], "unit_price": standing_price}, std, model)
        scored.append((math.inf if c is None else c, m))
    floor = {}
    for c, m in scored:
        floor[m["level"]] = min(floor.get(m["level"], c), c)
    out = [
        (c, m)
        for c, m in scored
        if c <= floor[m["level"]] * (1 + ROBUST_BAND)
        or (fields and all(m["terms"].get(f) in p["values"] for f, p in fields.items()))
    ]
    out.sort(key=lambda x: x[0])
    return [m for _, m in out]


def _with_alt_levers(case, led, pref):
    """Levers of the supplier's own alternative package when that package is cheaper for us than its
    standing offer: the price gap between its two packages shows what the lever is worth to it, so our next
    packages carry that lever too (repriced to the same cost for us) instead of only its standing levers.
    Practice: a capacity-constrained supplier offered a longer lead time for 6 % less, we kept asking for the
    short one, and its final offer came without the alternative."""
    alt = led.get("alt_better")
    if not alt or led.get("status") != "open":
        return pref
    fields = dict((pref or {}).get("fields") or {})
    standing = led.get("terms") or {}
    for f in case["negotiable"]:
        v = (alt.get("terms") or {}).get(f)
        if f == "unit_price" or f in fields or v is None:
            continue
        # Our sheets may only carry allowed values (term options or its standard): a supplier's own value
        # (practice: 54 days' lead time, options 35/42/56) becomes the nearest allowed one.
        allowed = [o for o in case.get("term_options", {}).get(f) or [] if o is not None] + [led["std"].get(f)]
        if v not in allowed:
            nums = [o for o in allowed if isinstance(o, int | float) and not isinstance(o, bool)]
            if not isinstance(v, int | float) or not nums:
                continue
            v = min(nums, key=lambda o: (abs(o - v), -o))
        if v == standing.get(f):
            continue
        fields[f] = {"values": [v], "source": alt.get("term_sheet_id"), "basis": "its alternative package"}
    return {**(pref or {}), "fields": fields}


def _with_quoted_index(case, led, pref):
    """When the supplier's opening terms include an index clause, recognize it immediately so our
    menu builds and reprices the allowed clause with the widest deadband right from round 0.
    Practice P08/P18: ignoring the supplier's opening clause in round 0 led to supplier stonewalling
    and costly 0.0% deadband clauses."""
    if "index_clause" not in case.get("negotiable", []):
        return pref
    clause = (led.get("terms") or {}).get("index_clause")
    if not clause:
        return pref
    fields = dict((pref or {}).get("fields") or {})
    if "index_clause" not in fields:
        fields["index_clause"] = {
            "values": preferences._widen(case, "index_clause", [clause]),
            "source": led.get("offer_id"),
            "basis": "quoted by supplier",
        }
    return {**(pref or {}), "fields": fields}


# RFQ: a bidder is never asked for more than a rival already offers us (practice: we cited a rival's EUR 27.61
# to another bidder while asking it for EUR 29.40-29.81 - the rival quote only moves a bidder if our ask is
# below it).
RIVAL_UNDERCUT = 0.01


def _best_rival_cost(leds, sid):
    costs = [
        o["c"]
        for x, o in leds.items()
        if x != sid and not o.get("ended") and o.get("acceptable") and o.get("c") is not None
    ]
    return min(costs) if costs else None


def _undercut(case, menu, std, model, c_max):
    """Sheets dearer for us than `c_max` repriced (same levers, lower price) to cost us `c_max`."""
    out = []
    for m in menu:
        if m["c"] <= c_max:
            out.append(m)
            continue
        levers = {k: v for k, v in m["terms"].items() if k != "unit_price"}
        price = offers.price_for_cost(case, levers, std, model, c_max, 0.5 * m["price"], m["price"])
        if not price or price <= 0:
            continue
        terms = {**m["terms"], "unit_price": price}
        c = offers.cost(case, terms, std, model)
        if c is not None:
            out.append({**m, "terms": terms, "price": price, "c": round(c, 2)})
    return out


def _with_fields(case, menu, level, fields, std):
    """The level's cheapest sheet with the insisted values put in: the menu keeps only a few combinations per
    level, and the one the supplier insists on may not be among them (practice P18: an index clause without
    deadband, demanded twice, never came back into our packages)."""
    base = [m for m in menu if m["level"] == level]
    if not base:
        return None
    best = min(base, key=lambda m: m["c"])
    terms = dict(best["terms"])
    for f, p in fields.items():
        if terms.get(f) not in p["values"]:
            if f == "index_clause" and p["values"]:
                terms[f] = max(p["values"], key=lambda o: (o or {}).get("deadband_pct") or 0)
            else:
                terms[f] = p["values"][0]
    if not must_haves_ok(case, terms, std):
        return None
    return {**best, "terms": terms}


def _index_probe(case, led, full, menu, std, model, pref):
    """Opening round: an index clause the supplier did not quote, at the same cost for us as the other sheets.

    `supplier_value` counts cash only, so an index clause always looked dearer for us at equal value and was
    never offered unless the supplier demanded it (practice P02: copper +48 %, no clause ever tried; P08/P18:
    the suppliers then insisted on one). The clause with the widest deadband is the cheapest for us (index risk
    and expected index move are computed exactly from the case); its base price is lowered until it costs us
    what the cheapest sheet costs, so whichever sheet the supplier picks costs us the same - and its choice
    tells us what security is worth to it."""
    if "index_clause" not in case["negotiable"] or (led.get("terms") or {}).get("index_clause"):
        return None
    if "index_clause" in ((pref or {}).get("fields") or {}) or any(m["terms"].get("index_clause") for m in menu):
        return None
    options = [o for o in case.get("term_options", {}).get("index_clause") or [] if isinstance(o, dict)]
    if not options or not menu:
        return None
    widest = max(options, key=lambda o: o.get("deadband_pct") or 0)
    wanted = {"fields": {"index_clause": {"values": [widest]}}}
    level = menu[0]["level"]
    repriced = [
        x
        for x in within_band([x for x in full if x["level"] == level], case, std, model, wanted, led.get("price"))
        if x["terms"].get("index_clause") == widest
    ]
    if not repriced:
        return None
    return {**repriced[0], "mid": "X1"}


def _mirror(case, alt, std):
    """Our term sheet with exactly the terms of the supplier's alternative package."""
    terms = {f: alt["terms"].get(f, std.get(f)) for f in case["negotiable"]}
    if any(v is None and f in ("unit_price",) for f, v in terms.items()):
        return None
    return {
        "mid": "M1",
        "level": "trade",
        "terms": terms,
        "c": alt["cost"],
        "saving": 0.0,
        "price": terms.get("unit_price"),
        "changes": {},
    }


def _fallback_sheet(case, led, std, model, must_beat):
    """No package fits the plan (e.g. the cost model rejects every lower price): never send prose only -
    ask for its own levers at a slightly lower price, if that is cheaper for us."""
    terms = {f: led["terms"].get(f, std.get(f)) for f in case["negotiable"]}
    if any(v is None for v in terms.values()):
        return []
    for cut in (0.02, 0.01, 0.005):
        t = {**terms, "unit_price": round(terms["unit_price"] * (1 - cut), 2)}
        c = offers.cost(case, t, std, model)
        if c is not None and c < must_beat - 0.5:
            return [
                {
                    "mid": "M1",
                    "level": "step",
                    "terms": t,
                    "c": round(c, 2),
                    "saving": round(must_beat - c, 2),
                    "price": t["unit_price"],
                    "changes": {},
                }
            ]
    return []


# Concession: we always move a little (a supplier gives nothing without a move from us - practice runs in
# which we held our position ran into its final offer at its own price: P07, P11, P18), more when it moves,
# most in the end game; we never jump to its offer.
STALL_SHARE = 0.10  # share of the remaining gap we concede when it did not move
MOVE_SHARE = 0.10  # share of the remaining gap we concede when it moved ...
RECIPROCITY = 0.5  # ... plus this share of its concession (in value to it)
MAX_SHARE = 0.4  # never more than this share of the remaining gap before the end game
# It held its price twice in a row: its final offer at that price usually follows (practice P02, P03, P11 in
# several runs: holds, then "final offer" at the held price, while our small steps stayed below it). Any deal
# below the held price beats that final offer, so we step further towards it - still below it.
HOLD_SHARE = 0.3
END_SHARE = 0.3  # share of the remaining gap per round in the end game


def concession_values(s, k, horizon, v_asp, v_stand, drop_mult=1.0, holds=0):
    """Supplier-value target of our next ask ("step"); `holds`: rounds it has kept its price in a row."""
    prev = s.get("v_ask", v_asp)
    their = max(0.0, (s.get("v_stand_prev") or v_stand) - v_stand)
    remaining = max(0.0, v_stand - prev)
    soft = 1.0 / max(drop_mult, 0.5)  # a learned "concede less" knob shrinks our moves
    if k == 0:
        step = v_asp
    elif k >= horizon - 2:
        step = prev + END_SHARE * remaining
    elif their <= 1e-6 and holds >= 2:
        step = prev + HOLD_SHARE * remaining
    elif their <= 1e-6:
        step = prev + STALL_SHARE * soft * remaining
    else:
        step = prev + min(MAX_SHARE * remaining, MOVE_SHARE * soft * remaining + RECIPROCITY * soft * their)
    step = min(max(step, prev), v_stand)
    return {"step": step}


def after_menu(ctx):
    if ctx["route"]["type"] == "close":
        return "close"
    return {"merged": "negotiator", "two": "strategist"}.get(ctx["mode"], "det")


# --- choose ---------------------------------------------------------------------------------------


def _det_choices(ctx):
    out = {}
    knobs = ctx["mem"].get("knobs") or {}
    for sid in ctx["route"]["suppliers"]:
        led = ctx["ledgers"][sid]
        out[sid] = policy.det_choose(
            sid,
            led,
            ctx["menus"].get(sid) or [],
            ctx["claims"].get(sid) or [],
            ctx["state"]["sup"][sid].get("tags") or [],
            ctx["route"]["final_push"].get(sid, False),
            led["rounds"] == 0,
            knobs,
        )
        out[sid]["source"] = "det"
        if (ctx.get("mirrors") or {}).get(sid):
            out[sid].update(tactic="conclude", ask_breakdown=False, claim_ids=[], questions=[])
    return out


def det(ctx):
    return {"choices": _det_choices(ctx)}


def _case_summary(ctx):
    case, b = ctx["case"], ctx["case"]["buyer"]
    return {
        "case_id": case.get("case_id"),
        "type": case.get("type"),
        "title": case.get("title"),
        "briefing": (case.get("briefing") or "")[:800],
        "goal": b.get("category_goal"),
        "need_per_year": b.get("need_per_year"),
        "horizon_years": b.get("horizon_years"),
        "current_unit_price": b.get("current_unit_price"),
        "our_alternative_cost_eur": ctx.get("c_alt"),
        "negotiable": case.get("negotiable"),
        "must_haves": b.get("must_haves"),
        "round_limit": case.get("round_limit", 10),
    }


def _payload(ctx, with_plan=True):
    state, dossier = ctx["state"], ctx["cc"]["dossier"]
    suppliers = {}
    for sid in ctx["route"]["suppliers"]:
        s, led = state["sup"][sid], ctx["ledgers"][sid]
        suppliers[sid] = {
            "dossier": dossier_mod.digest(dossier, sid),
            "advice": advice_for(s.get("tags") or []),
            "ledger": ledger_mod.digest(led),
            "preferences": s.get("preferences") or {},
            "final_offer_push": ctx["route"]["final_push"].get(sid, False),
            "menu": [
                {
                    "mid": m["mid"],
                    "level": m["level"],
                    "terms": offers.describe(ctx["case"], m["terms"]),
                    "c": m["c"],
                    "saving": m["saving"],
                }
                for m in ctx["menus"].get(sid) or []
            ],
            "claims": [
                {"cid": c["cid"], "kind": c["kind"], "sentence": c["sentence"], "reveals_private_data": c["reveals"]}
                for c in ctx["claims"].get(sid) or []
            ][:8],
            "our_previous_read": s.get("read"),
            **({"our_plan": s.get("plan")} if with_plan else {}),
        }
    return {
        "case": _case_summary(ctx),
        "trigger_letter": (dossier.get("trigger") or {}).get("text"),
        "lessons": [
            {
                "key": x["key"],
                "scope": f"{x['scope']['level']}:{x['scope']['value']}",
                "do_or_avoid": x["polarity"],
                "lesson": x["text"],
                "confidence": x["confidence"],
            }
            for x in ctx["mem"].get("lessons") or []
        ],
        "suppliers": suppliers,
    }


def _merge_choices(ctx, raw):
    """Model choices per supplier, each validated; the deterministic choice fills every gap. A reply
    that gives no usable supplier entry counts as a model failure (two turn the model off)."""
    det_c = _det_choices(ctx)
    try:
        entries = prompts.supplier_entries(raw, ctx["route"]["suppliers"])
    except Exception:
        entries = {}
    out, used = {}, 0
    for sid in ctx["route"]["suppliers"]:
        try:
            raw_choice = entries.get(sid)
            s = ctx["state"]["sup"][sid]
            pref = preferences.update(
                ctx["case"],
                ctx["ledgers"][sid],
                s.get("preferences") or {},
                (raw_choice or {}).get("signals") if isinstance(raw_choice, dict) else [],
            )
            s["preferences"] = pref
            out[sid] = prompts.clean_choice(
                entries.get(sid), ctx["menus"].get(sid) or [], ctx["claims"].get(sid) or [], det_c[sid]
            )
            choice = out[sid]
            if (ctx.get("mirrors") or {}).get(sid):
                out[sid] = dict(det_c[sid], source="det")
                continue
            candidates = ctx["menus"].get(sid) or []
            fields = pref.get("fields") or {}
            if (
                fields
                and candidates
                and not any(all(m["terms"].get(f) in p["values"] for f, p in fields.items()) for m in candidates)
            ):
                # A countercondition the model just evidenced: add it, repriced to the same cost for us.
                led = ctx["ledgers"][sid]
                extra = [
                    {**m, "mid": f"P{i}"}
                    for i, m in enumerate(
                        within_band(
                            (ctx.get("fulls") or {}).get(sid) or [],
                            ctx["case"],
                            led["std"],
                            led["model"],
                            pref,
                            led.get("price"),
                        ),
                        1,
                    )
                    if all(m["terms"].get(f) in p["values"] for f, p in fields.items())
                ]
                candidates.extend(extra)
                ctx["menus"][sid] = candidates
            selected = [m for m in candidates if m["mid"] in choice["sheet_ids"]]
            if selected and pref.get("fields"):
                same_level = [m for m in candidates if m["level"] == selected[0]["level"]]
                ranked = preferences.rank(same_level, pref)
                # A current, evidenced countercondition outranks a stale model choice.
                if ranked:
                    best = ranked[0]
                    if any(m["terms"].get(f) not in p["values"] for m in selected for f, p in pref["fields"].items()):
                        choice["sheet_ids"] = [best["mid"]]
                        choice["body"] = ""  # render fresh prose when the evidenced terms changed
            _proposal(ctx, sid, raw_choice, choice)
            used += out[sid].get("source") == "llm"
        except Exception:
            out[sid] = dict(det_c[sid], source="det")
    if not used:
        ctx["state"]["llm"]["fails"] = ctx["state"]["llm"].get("fails", 0) + 1
    return out


def _proposal(ctx, sid, raw, choice):
    """The model can request allowed non-price terms; code solves the price and checks cost."""
    proposal = raw.get("proposal") if isinstance(raw, dict) else None
    if not isinstance(proposal, dict) or not proposal or sid not in ctx.get("vals", {}):
        return
    case, led = ctx["case"], ctx["ledgers"][sid]
    if any(
        f == "unit_price" or f not in case["negotiable"] or v not in case.get("term_options", {}).get(f, [])
        for f, v in proposal.items()
    ):
        return
    s = ctx["state"]["sup"][sid]
    if any(
        f in proposal and proposal[f] not in p["values"] for f, p in s.get("preferences", {}).get("fields", {}).items()
    ):
        return
    model = led["model"]
    restricted = {**case, "term_options": {**case.get("term_options", {}), **{f: [v] for f, v in proposal.items()}}}
    # Include proposed terms in the base so lever_domains does not re-add the old value.
    std = {**led["std"], **proposal}
    v_asp, v_stand = ctx["vals"][sid]
    level = raw.get("level") if raw.get("level") in ctx["shares"][sid] else "step"
    menu = offers.build_menu(
        restricted, led, model, std, v_asp, v_stand, {level: ctx["shares"][sid].get(level, 0)}, led["c"] or math.inf
    )
    if menu:
        selected = preferences.rank(menu, s.get("preferences") or {})[0]
        selected["mid"] = "proposal"
        ctx["menus"][sid].append(selected)
        choice["sheet_ids"] = [selected["mid"]]
        choice["body"] = ""


def negotiator(ctx):
    raw = ctx["gw"].json(prompts.negotiator(_payload(ctx), write=True))
    return {"choices": _merge_choices(ctx, raw)}


def strategist(ctx):
    raw = ctx["gw"].json(prompts.negotiator(_payload(ctx), write=False))
    return {"choices": _merge_choices(ctx, raw)}


def write(ctx):
    choices = ctx["choices"]
    if not ctx["budget"].can_call() or not any(c.get("source") == "llm" for c in choices.values()):
        return {}
    payload = {
        "case": _case_summary(ctx),
        "suppliers": {
            sid: {
                "dossier": dossier_mod.digest(ctx["cc"]["dossier"], sid),
                "latest_email": ctx["ledgers"][sid].get("body"),
                "decision": {k: c.get(k) for k in ("read", "tactic", "tone", "ask_breakdown", "questions", "plan")},
                "packages": [
                    offers.describe(ctx["case"], m["terms"])
                    for m in ctx["menus"].get(sid) or []
                    if m["mid"] in c.get("sheet_ids", [])
                ],
                "claims": [x["cid"] for x in ctx["claims"].get(sid) or [] if x["cid"] in c.get("claim_ids", [])],
            }
            for sid, c in choices.items()
        },
    }
    raw = ctx["gw"].json(prompts.writer(payload))
    emails = (raw or {}).get("emails") if isinstance((raw or {}).get("emails"), dict) else {}
    for sid, c in choices.items():
        e = emails.get(sid) if isinstance(emails.get(sid), dict) else {}
        c["body"] = str(e.get("body") or c.get("body") or "")[:3000]
    return {"choices": choices}


# --- critic ---------------------------------------------------------------------------------------


def critic(ctx):
    case, threads, state = ctx["case"], ctx["threads"], ctx["state"]
    actions, batch = [], set()  # IDs cited earlier in this batch count as used for the next supplier
    for sid in ctx["route"]["suppliers"]:
        choice = ctx["choices"][sid]
        menu_by_id = {m["mid"]: m for m in ctx["menus"].get(sid) or []}
        probe = (ctx.get("explore") or {}).get(sid)
        ids = list(choice.get("sheet_ids") or [])
        if probe in menu_by_id and probe not in ids and ids and len(ids) < 3:
            if menu_by_id[probe]["level"] == (menu_by_id.get(ids[0]) or {}).get("level"):
                choice = {**choice, "sheet_ids": ids + [probe]}
        sheets = [menu_by_id[m]["terms"] for m in choice.get("sheet_ids") or [] if m in menu_by_id]
        chosen = [
            c
            for c in ctx["claims"].get(sid) or []
            if c["cid"] in (choice.get("claim_ids") or []) and not (set(c["ids"]) & batch)
        ]
        # One argument per kind and message: a supplier answers a second argument of the same kind (three
        # parts' volume forecasts, say) with "duplicate" or "we have already taken that into account", and
        # it takes one of the three checked slots (practice: 8 of 19 same-kind repeats came back duplicate).
        chosen = [c for i, c in enumerate(chosen) if c["kind"] not in {x["kind"] for x in chosen[:i]}]
        wanted = len(choice.get("claim_ids") or [])
        for c in ctx["claims"].get(sid) or []:  # replace claims another supplier of this batch just got
            if len(chosen) >= wanted:
                break
            if (
                c not in chosen
                and not c.get("reveals")
                and not (set(c["ids"]) & batch)
                and c["kind"] not in {x["kind"] for x in chosen}
            ):
                chosen.append(c)
        rival = next((c for c in ctx["claims"].get(sid) or [] if c["kind"] == "offer"), None)
        if rival and rival not in chosen:
            # RFQ: a cheaper rival bid leads every message it is new in. A bidder shown a much lower rival quote
            # gives half the step at once and the rest after checking it (practice RFQ back office, two bidders
            # in three runs: 36.00 -> 30.50 -> 27.61 agreed); the model left it out of most opening messages.
            chosen = [rival] + [c for c in chosen if c["kind"] != "offer"][:2]
        batch.update(i for c in chosen for i in c["ids"] if not i.startswith(claims_mod.PER_SUPPLIER))
        questions = []
        pref = state["sup"][sid].get("preferences") or {}
        for q in choice.get("questions") or []:
            q = preferences.filter_questions(q, pref)
            text, issues = writer.lint(q)
            if text and not issues:
                questions.append(text)
        if (
            choice.get("ask_breakdown")
            and preferences.can_ask_breakdown(pref)
            and not ctx["ledgers"][sid].get("breakdown")
            and not any("breakdown" in q.lower() for q in questions)
            and not preferences.BREAKDOWN.search(choice.get("body") or "")
        ):
            questions.append("Could you send us a cost breakdown for this part?")
        led = ctx["ledgers"][sid]
        hold = (ctx["route"].get("hold") or {}).get(sid, False)
        if hold:  # no sheets, no arguments: a polite holding reply that does not push its final offer
            sheets, chosen, questions = [], [], []
            choice = {**choice, "body": "", "tactic": "review", "sheet_ids": []}
        sheets = _valid_sheets(case, threads, sid, sheets)
        latest = next((m.get("body") or "" for m in reversed(threads[sid]) if m.get("from") != "buyer"), "")
        notes = [writer.volume_answer(case, sheets, latest)] if sheets else []
        body_src, issues = writer.lint(preferences.filter_questions(choice.get("body") or "", pref))
        if len(body_src) < 40:
            body_src = writer.finish_template(
                writer.template(
                    case,
                    choice.get("tone"),
                    choice.get("tactic"),
                    bool(sheets),
                    False,
                    led["rounds"] == 0,
                    writer.contact_name(threads[sid]),
                ),
                chosen,
            )
        body, refs, emitted = writer.compose(case, body_src, chosen, sheets, questions, notes)
        by_cid = {c["cid"]: c for c in chosen}
        emitted = [by_cid[c] for c in emitted if c in by_cid]
        subject = writer.subject(case, _thread_subject(threads[sid]))
        message = {"subject": subject, "body": body, "term_sheets": [{"terms": t} for t in sheets], "refs": refs}
        action = {"supplier_id": sid, "message": message}
        try:
            validate(case, threads, [action])
        except Exception:
            message = {"subject": subject, "body": writer.SIGNATURE_FALLBACK, "term_sheets": [], "refs": []}
            action = {"supplier_id": sid, "message": message}
            sheets, emitted, refs = [], [], []
        actions.append(action)
        s = state["sup"][sid]
        level = (menu_by_id.get((choice.get("sheet_ids") or [None])[0]) or {}).get("level")
        if sheets and level in (ctx["shares"].get(sid) or {}) and sid in (ctx.get("vals") or {}):
            v_asp, v_stand = ctx["vals"][sid]
            s["f"] = ctx["shares"][sid][level]
            s["v_ask"] = v_asp + s["f"] * (v_stand - v_asp)
            s["c_ask"] = _dearest(case, sheets, led["std"], led.get("model"))
        s["sent_at"] = time.time()
        s["last_status"] = led["status"]
        if choice.get("read"):
            s["read"] = choice["read"]
        if choice.get("plan"):
            s["plan"] = choice["plan"]
        state["log"].append(
            {
                "r": led["rounds"],
                "sup": sid,
                "mode": ctx.get("mode"),
                "src": choice.get("source"),
                "tactic": choice.get("tactic"),
                "level": level,
                "share": round(s.get("f", 0.0), 3),
                "sheets": [
                    {"price": m["price"], "c": m["c"]}
                    for m in menu_by_id.values()
                    if m["mid"] in (choice.get("sheet_ids") or [])
                ][:3]
                if sheets
                else [],
                "refs": refs,
                "claims": [{"kind": c["kind"], "ids": c["ids"]} for c in emitted],
                "claim_kinds": [c["kind"] for c in emitted],
                "tone": choice.get("tone"),
                "pred": choice.get("prediction"),
                "lessons": state.get("mem", {}).get("lessons", [])[:10],
            }
        )
    return {"actions": actions}


def _valid_sheets(case, threads, sid, sheets):
    """Sheets the arena would accept: one invalid sheet used to cost the whole message its sheets."""
    ok = []
    for terms in sheets:
        message = {"subject": "check", "body": "check", "term_sheets": [{"terms": terms}]}
        probe = {"supplier_id": sid, "message": message}
        try:
            validate(case, threads, [probe])
            ok.append(terms)
        except Exception:
            continue
    return ok


def _dearest(case, sheets, std, model=None):
    """Our cost of the dearest sheet we sent, priced like the ledger prices its offers: any of them is a
    deal we asked for."""
    costs = []
    for t in sheets:
        try:
            c = offers.cost(case, t, std, model or {})
            if c is None:
                return None
            costs.append(c)
        except Exception:
            return None
    finite = [c for c in costs if math.isfinite(c)]
    return max(finite) if finite and len(finite) == len(costs) else None


def _thread_subject(events):
    for m in events:
        if m.get("from") != "buyer" and m.get("subject"):
            return m["subject"]
    return None


# --- runners --------------------------------------------------------------------------------------

NODES = {
    "observe": observe,
    "route": route,
    "close": close,
    "menu": menu,
    "negotiator": negotiator,
    "strategist": strategist,
    "writer": write,
    "det": det,
    "critic": critic,
}


def after_route(ctx):
    return "close" if ctx["route"]["type"] == "close" else "menu"


def build_langgraph():
    from langgraph.graph import END, StateGraph

    g = StateGraph(Ctx)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.set_entry_point("observe")
    g.add_edge("observe", "route")
    g.add_conditional_edges("route", after_route, {"close": "close", "menu": "menu"})
    g.add_edge("close", END)
    g.add_conditional_edges(
        "menu", after_menu, {"close": "close", "negotiator": "negotiator", "strategist": "strategist", "det": "det"}
    )
    g.add_edge("strategist", "writer")
    g.add_edge("writer", "critic")
    g.add_edge("negotiator", "critic")
    g.add_edge("det", "critic")
    g.add_edge("critic", END)
    return g.compile()


def run_sequential(ctx):
    """The same graph without LangGraph (used if it cannot be imported)."""

    def step(name):
        ctx.update(NODES[name](ctx) or {})

    step("observe")
    step("route")
    if after_route(ctx) == "menu":
        step("menu")
        nxt = after_menu(ctx)
        if nxt != "close":
            if nxt == "strategist":
                step("strategist")
                step("writer")
            else:
                step(nxt)
            step("critic")
            return ctx
    step("close")
    return ctx


_GRAPH = None


def run(ctx):
    global _GRAPH
    try:
        if _GRAPH is None:
            _GRAPH = build_langgraph()
        runner = _GRAPH
    except Exception:
        runner = None
    if runner is None:
        return run_sequential(ctx), "sequential"
    out = runner.invoke(ctx, {"recursion_limit": 20})
    return out, "langgraph"
