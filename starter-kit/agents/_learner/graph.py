"""One decide() as a LangGraph state graph (with a plain sequential runner as fallback).

observe -> route -> close -> reflect
                 -> menu -> negotiator | strategist -> writer | det -> critic
"""

import math
import time
from typing import Any, TypedDict

from arena_kit.cost import total_cost
from arena_kit.docs import standard_terms
from arena_kit.harness.validation import close_action, validate

from . import claims as claims_mod
from . import dossier as dossier_mod
from . import ledger as ledger_mod
from . import memory, offers, policy, prompts, writer
from .priors import advice_for

REFRESH_MOVE = 1.5
MENU_BAND = 0.003  # sheets may cost us at most this much more than the level's cheapest
ASK_TOL = 0.001  # an offer "meets our ask" only if it also costs us no more than that ask
MAX_LATENCY = 120  # a supplier answers within about a minute


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
    now = time.time()
    for sid, led in leds.items():
        s = state["sup"].setdefault(sid, {"tags": []})
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
    mem = memory.refresh(case, ctx["p"], tags_of(state), ctx["cc"])
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
    state["closed"] = True
    state["close"] = {
        "type": kind,
        "supplier_id": action["supplier_id"],
        "reason": r.get("reason"),
        "c": led.get("c") if kind == "accept" else ctx["c_alt"],
        "c_open": state.get("c_open"),
        "c_alt": ctx["c_alt"],
    }
    return {"actions": [action], "route": r}


def reflect(ctx):
    """Closing reflection: the lessons this case teaches the next cases of the run."""
    state, gw, budget = ctx["state"], ctx["gw"], ctx["budget"]
    used = ctx["mem"].get("lessons") or []
    reflection = {"lessons": [], "feedback": {}, "reads": {}, "source": "det"}
    if _llm_ok(ctx) and budget.can_call():
        payload = {
            "case": _case_summary(ctx),
            "outcome": state.get("close"),
            "suppliers": {
                sid: {
                    "tags": s.get("tags"),
                    "our_read": s.get("read"),
                    "price_path": (ctx["ledgers"].get(sid) or {}).get("prices"),
                    "status_path": (ctx["ledgers"].get(sid) or {}).get("statuses"),
                    "rounds": [
                        {k: e.get(k) for k in ("r", "tactic", "level", "sheets", "claim_kinds", "tone", "pred", "out")}
                        for e in state["log"]
                        if e.get("sup") == sid
                    ][-8:],
                }
                for sid, s in state["sup"].items()
            },
            "lessons_used": [{"key": x["key"], "text": x["text"]} for x in used],
        }
        try:
            raw = gw.json(prompts.reflector(payload))
            lessons = prompts.clean_lessons(raw)
            if raw is None or not isinstance(raw.get("lessons"), list):
                raise ValueError("unusable reflection")
            reflection["lessons"] = lessons
            fb = raw.get("feedback") if isinstance(raw.get("feedback"), dict) else {}
            keys = {x["key"] for x in used}
            reflection["feedback"] = {k: v for k, v in fb.items() if k in keys and v in ("held", "failed")}
            reads = raw.get("supplier_reads") if isinstance(raw.get("supplier_reads"), dict) else {}
            reflection["reads"] = {k: v[:200] for k, v in reads.items() if k in state["sup"] and isinstance(v, str)}
            reflection["source"] = "llm"
        except Exception:  # the close is already decided: a bad reflection must never undo it
            state["llm"]["fails"] = state["llm"].get("fails", 0) + 1
    state["reflection"] = reflection
    return {}


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


def _stalled(led, rounds=2):
    prices = [p for p in led.get("prices") or [] if p is not None]
    return len(prices) > rounds and len(set(prices[-(rounds + 1) :])) == 1


def menu(ctx):
    case, state, cc, r = ctx["case"], ctx["state"], ctx["cc"], ctx["route"]
    leds, knobs = ctx["ledgers"], ctx["mem"].get("knobs") or {}
    dossier = cc["dossier"]
    used = claims_mod.used_ids(ctx["threads"])
    logged, logged_per_sup = _logged_ids(state)
    menus, claims, shares, vals = {}, {}, {}, {}
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
        model = offers.supplier_model(case, led, tags, dossier["suppliers"].get(sid) or {}, dossier["parts"])
        std = led["std"]
        v_stand = offers._value(case, led["terms"], model) if led["terms"].get("unit_price") else None
        if v_stand is not None and s.get("v_asp") is None:
            drop = ctx["p"]["anchor_drop"] * (knobs.get("drop_mult") or {}).get(sid, 1.0)
            anchor = offers.anchor_price(case, led, tags, dossier, drop)
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
            and v_stand <= s["v_ask"] + tol
            # same value to it but dearer for us (practice P08: index clause swapped in) is no match
            and (s.get("c_ask") is None or (led["c"] is not None and led["c"] <= s["c_ask"] * (1 + ASK_TOL)))
        ):
            out = accept_ready("its offer meets our last ask")
            if out:
                return out
        if v_asp is not None and v_stand is not None and v_asp >= v_stand:
            v_asp = v_stand * 0.995
        k = led["rounds"]
        horizon = min(ctx["p"]["round_budget"], (knobs.get("horizon") or {}).get(sid, 99), k + led["moves_left"])
        levels = {}
        if v_asp is not None and v_stand is not None:
            values = concession_values(s, k, horizon, v_asp, v_stand, (knobs.get("drop_mult") or {}).get(sid, 1.0))
            if final_push or led["status"] == "final_offer" or values["step"] >= v_asp + 0.6 * (v_stand - v_asp):
                values["trade"] = v_stand
            span = v_stand - v_asp
            levels = {name: (v - v_asp) / span if span > 0 else 1.0 for name, v in values.items()}
            vals[sid] = (v_asp, v_stand)
            s["v_stand_prev"] = v_stand
        must_beat = (offers.cost(case, led["terms"], std, model) if led["terms"] else None) or led["c"]
        must_beat = must_beat if must_beat is not None else math.inf
        m = within_band(offers.build_menu(case, led, model, std, v_asp, v_stand, levels, must_beat))
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
            if (late and nothing_new) or (k >= ctx["p"]["round_budget"] and _stalled(led)):
                out = accept_ready("nothing left to gain" if nothing_new else "supplier stopped moving")
                if out:
                    return out
        menus[sid], shares[sid] = m, levels
        asked = trig.get("asked_pct")
        if asked is None and case["buyer"].get("current_unit_price") and led.get("price"):
            asked = (led["price"] / case["buyer"]["current_unit_price"] - 1) * 100
        catalog = claims_mod.build_catalog(case, cc["facts"], dossier, sid, led.get("price"), asked)
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
    return {"menus": menus, "claims": claims, "shares": shares, "vals": vals, "mode": mode}


def within_band(menu):
    """Of equal-value sheets the supplier takes the one dearest for us (practice P10, P11): offer only
    those close to the cheapest of their level."""
    floor = {}
    for m in menu:
        floor[m["level"]] = min(floor.get(m["level"], m["c"]), m["c"])
    return [m for m in menu if m["c"] <= floor[m["level"]] * (1 + MENU_BAND)]


RECIPROCITY = 0.4  # we concede this share of the supplier's last concession (in value to it)
STALL_STEP = 0.15  # after one round of mutual standstill we concede this share of the remaining gap


def concession_values(s, k, horizon, v_asp, v_stand, drop_mult=1.0):
    """Supplier-value targets for this round: reciprocity, capped by a slow schedule early and pushed
    by it late (deadline pressure). We never concede without a move from its side before the end game."""
    prev = s.get("v_ask", v_asp)
    their = max(0.0, (s.get("v_stand_prev") or v_stand) - v_stand)
    late = k >= horizon - 2
    gap = v_stand - v_asp
    sched = v_asp + gap * offers.schedule(k, horizon)
    cap = v_asp + gap * min(1.0, (k / max(1, horizon - 1)) ** 1.5)
    if k == 0:
        step = v_asp
    elif late:
        step = max(prev + RECIPROCITY / max(drop_mult, 0.5) * their, sched)
        step = min(step, prev + 0.75 * (v_stand - prev))  # a real move, but never all the way to its offer
    elif their <= 1e-6:
        # no concession without a move from its side - but only once: repeating the same sheets runs into
        # its final offer at its own price (practice P02, P03, P07, P11, P18)
        step = prev + STALL_STEP * (v_stand - prev) if s.get("held") else prev
    else:
        step = min(prev + RECIPROCITY / max(drop_mult, 0.5) * their, max(cap, prev))
    step = min(max(step, prev), v_stand)
    s["held"] = 0 < k and not late and step <= prev + 1e-9
    values = {"step": step, "big": step + (v_stand - step) * 0.4}
    if k > 0 and prev < step - 1e-9:
        values["hold"] = prev
    return values


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
            out[sid] = prompts.clean_choice(
                entries.get(sid), ctx["menus"].get(sid) or [], ctx["claims"].get(sid) or [], det_c[sid]
            )
            used += out[sid].get("source") == "llm"
        except Exception:
            out[sid] = dict(det_c[sid], source="det")
    if not used:
        ctx["state"]["llm"]["fails"] = ctx["state"]["llm"].get("fails", 0) + 1
    return out


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
        sheets = [menu_by_id[m]["terms"] for m in choice.get("sheet_ids") or [] if m in menu_by_id]
        chosen = [
            c
            for c in ctx["claims"].get(sid) or []
            if c["cid"] in (choice.get("claim_ids") or []) and not (set(c["ids"]) & batch)
        ]
        wanted = len(choice.get("claim_ids") or [])
        for c in ctx["claims"].get(sid) or []:  # replace claims another supplier of this batch just got
            if len(chosen) >= wanted:
                break
            if c not in chosen and not c.get("reveals") and not (set(c["ids"]) & batch):
                chosen.append(c)
        batch.update(i for c in chosen for i in c["ids"] if not i.startswith(claims_mod.PER_SUPPLIER))
        questions = []
        for q in choice.get("questions") or []:
            text, issues = writer.lint(q)
            if text and not issues:
                questions.append(text)
        if (
            choice.get("ask_breakdown")
            and not ctx["ledgers"][sid].get("breakdown")
            and not any("breakdown" in q.lower() for q in questions)
        ):
            questions.append("Could you send us a cost breakdown for this part?")
        led = ctx["ledgers"][sid]
        hold = (ctx["route"].get("hold") or {}).get(sid, False)
        if hold:  # no sheets, no arguments: a polite holding reply that does not push its final offer
            sheets, chosen, questions = [], [], []
            choice = {**choice, "body": "", "tactic": "review", "sheet_ids": []}
        body_src, issues = writer.lint(choice.get("body") or "")
        if len(body_src) < 40:
            body_src = writer.finish_template(
                writer.template(
                    case, choice.get("tone"), choice.get("tactic"), bool(sheets), False, led["rounds"] == 0
                ),
                chosen,
            )
        body, refs, emitted = writer.compose(case, body_src, chosen, sheets, questions)
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
            s["c_ask"] = min(menu_by_id[m]["c"] for m in choice.get("sheet_ids") or [] if m in menu_by_id)
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
    "reflect": reflect,
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
    g.add_edge("close", "reflect")
    g.add_edge("reflect", END)
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
    step("reflect")
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
