"""Offline tests for the `learner` agent: menu, claims, critic, budget, engine runs and in-run memory."""

import asyncio
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents._learner import claims as claims_mod  # noqa: E402
from agents._learner import dossier as dossier_mod  # noqa: E402
from agents._learner import graph, offers, writer  # noqa: E402
from agents._learner import ledger as ledger_mod  # noqa: E402
from agents._learner.core import package  # noqa: E402
from agents._learner.params import load as load_params  # noqa: E402
from agents._learner.state import STATE_LIMIT, size  # noqa: E402
from agents.learner import Agent  # noqa: E402
from arena_kit.cost import total_cost  # noqa: E402
from arena_kit.docs import read_document  # noqa: E402
from arena_kit.harness.cli import check_agent, parser  # noqa: E402
from arena_kit.harness.config import load  # noqa: E402
from arena_kit.harness.engine import Engine  # noqa: E402
from arena_kit.harness.report import report  # noqa: E402
from arena_kit.harness.state import Journal  # noqa: E402
from arena_kit.harness.transport import Models, Transport  # noqa: E402
from arena_kit.harness.validation import validate  # noqa: E402
from arena_kit.sim import Sim, create_app, load_cases  # noqa: E402

CASES = ["case:S01", "case:S02", "case:S03"]


def new_sim():
    """The scripted simulator with the data package's facts, as the harness CLI starts it."""
    return Sim(load_cases(), facts=package(load_params({})).facts, no_clock=True)


def config(**updates):
    return load(parser().parse_args(["run"])) | updates


def opened(sim, case_id, team="test"):
    result = sim.open(team, case_id)
    for events in result["threads"].values():
        for message in events:
            for att in message["attachments"]:
                att["text"] = read_document(
                    att["name"], sim.attachment(team, result["session_id"], att["attachment_id"])[0]
                )
    return result


def observation(case, threads, seconds=60):
    return {
        "case": case,
        "threads": threads,
        "ready_suppliers": list(case["suppliers"]),
        "seconds_left": {s: seconds for s in case["suppliers"]},
        "wave_seconds_left": None,
    }


class FakeServices:
    def __init__(self, reply=None, delay=0.0, error=None):
        self.reply, self.delay, self.error, self.calls = reply, delay, error, []

    def complete(self, messages):
        self.calls.append(messages)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return self.reply(messages) if callable(self.reply) else self.reply

    @staticmethod
    def cost(case, terms, standard=None, supplier=None):
        return total_cost(case, terms, standard, supplier)


def negotiator_reply(messages):
    """A plausible model: picks the first menu package and claim, writes prose with placeholders."""
    system, user = messages[0]["content"], json.loads(messages[1]["content"])
    if '{"emails"' in system:
        return json.dumps(
            {
                "emails": {
                    s: {"subject": "x", "body": "Dear colleagues,\n{{SHEETS}}\nBest regards"} for s in user["suppliers"]
                }
            }
        )
    if '"lessons"' in system and "supplier_reads" in system:
        return json.dumps(
            {
                "lessons": [
                    {
                        "scope_level": "archetype",
                        "scope_value": "relationship",
                        "polarity": "do",
                        "lever": "tone",
                        "lesson": "Acknowledge past support before asking.",
                    }
                ],
                "supplier_reads": {},
                "feedback": {},
            }
        )
    out = {}
    for sid, sup in user["suppliers"].items():
        out[sid] = {
            "read": "values the relationship",
            "tactic": "anchor",
            "sheet_ids": [m["mid"] for m in sup["menu"][:2]],
            "claim_ids": [c["cid"] for c in sup["claims"][:1]],
            "ask_breakdown": True,
            "questions": ["What drives your cost?"],
            "tone": "warm",
            "prediction": {"status": "open", "price_move_pct": 1.0, "note": "small move"},
            "email": {
                "subject": "x",
                "body": "Dear colleagues,\nthank you. {{CLAIM:C1}}\n{{SHEETS}}\n{{QUESTIONS}}\n"
                "Best regards\nHollerau Pumpenwerke GmbH, Purchasing",
            },
        }
    return "```json\n" + json.dumps({"suppliers": out}) + "\n```"


# --- deterministic building blocks -----------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASES)
def test_menu_sheets_are_valid_and_cheaper_than_the_standing_offer(case_id):
    sim = new_sim()
    o = opened(sim, case_id)
    case, threads = o["case"], o["threads"]
    pkg = package(load_params({}))
    dossier = dossier_mod.build(case, pkg)
    leds = ledger_mod.build(case, threads)
    for sid, led in leds.items():
        tags = dossier["suppliers"][sid]["tags"]
        model = offers.supplier_model(case, led, tags, dossier["suppliers"][sid], dossier["parts"])
        v_stand = offers._value(case, led["terms"], model)
        v_asp = offers._value(case, {"unit_price": offers.anchor_price(case, led, tags, dossier, 0.1)}, model)
        menu = offers.build_menu(
            case, led, model, led["std"], v_asp, v_stand, {"step": 0.0, "big": 0.4, "trade": 1.0}, led["c"]
        )
        assert menu, sid
        for m in menu:
            assert math.isfinite(m["c"]) and m["c"] < led["c"]
            validate(case, threads, [{"supplier_id": sid, "message": {"term_sheets": [{"terms": m["terms"]}]}}])
        steps = [m["price"] for m in menu if m["level"] == "step"]
        bigs = [m["c"] for m in menu if m["level"] == "big"]
        assert min(m["c"] for m in menu if m["level"] == "step") <= min(bigs)
        assert all(p > 0 for p in steps)


def test_concessions_wait_for_a_move_and_converge_late():
    s = {"v_ask": 100.0, "v_stand_prev": 120.0}
    still = graph.concession_values(dict(s), 2, 6, 100.0, 120.0)
    assert still["step"] == 100.0  # it did not move: neither do we
    moved = graph.concession_values(dict(s), 2, 6, 100.0, 115.0)
    assert 100.0 < moved["step"] < 100.0 + 5.0  # we concede less than it did
    late = graph.concession_values(dict(s), 5, 6, 100.0, 120.0)
    assert 110.0 < late["step"] < 120.0  # end game: a real move, never all the way to its offer


def _check_claim(c, facts):
    if c["kind"] == "idx":
        a, b = (facts[i]["value"] for i in c["ids"])
        assert abs((b / a - 1) * 100 - c["figure"]) <= 0.5
        assert c["ids"][0].split("@")[1] < c["ids"][1].split("@")[1]
    elif c["kind"] == "sc":
        f = facts[c["ids"][0]]
        assert c["figure"] in (round(f["otd_pct"], 1), round(f["ppm"]))
    elif c["kind"] in ("quote", "cost"):
        f = facts[c["ids"][0]]
        assert c["figure"] == f.get("unit_price", f.get("unit_cost"))
    elif c["kind"] == "po":
        assert all(facts[i]["unit_price"] == c["figure"] for i in c["ids"]) and 2 <= len(c["ids"]) <= 8
    elif c["kind"] == "notice":
        assert facts[c["ids"][0]]["days"] == c["figure"] < facts[c["ids"][1]]["days"]
    assert all(i in c["sentence"] for i in c["ids"])


def test_claim_catalog_figures_match_the_facts():
    pkg = package(load_params({}))
    cases = load_cases()
    p01 = {
        **cases["case:S01"],
        "suppliers": ["sup:70110"],
        "parts": ["part:310010"],
        "trigger_doc": "doc:20260911-70110-P01",
        "refs": [],
        "facts": {},
    }
    for case in [*cases.values(), p01]:
        dossier = dossier_mod.build(case, pkg)
        for sid in case["suppliers"]:
            cat = claims_mod.build_catalog(case, pkg.facts, dossier, sid, standing_price=10_000, asked_pct=9.5)
            for c in cat:
                _check_claim(c, {**pkg.facts, **case.get("facts", {})})
    kinds = {
        c["kind"]
        for c in claims_mod.build_catalog(
            p01, pkg.facts, dossier_mod.build(p01, pkg), "sup:70110", standing_price=150, asked_pct=9.5
        )
    }
    assert {"idx", "notice", "sc"} <= kinds


@pytest.mark.parametrize("case_id", CASES)
def test_cited_claims_come_back_verified(case_id):
    sim = new_sim()
    o = opened(sim, case_id)
    agent = Agent()
    state = agent.prepare({"case": o["case"], "parameters": {"llm": False, "memory": False}})
    decision = agent.decide(observation(o["case"], o["threads"]), state, None)
    for action in decision["actions"]:
        reply = sim.message("test", o["session_id"], action["supplier_id"], action["message"])
        assert reply["claim_results"] and all(r["result"] == "verified" for r in reply["claim_results"])


def test_dossier_tags_known_suppliers_quickly():
    pkg = package(load_params({}))
    started = time.monotonic()
    tags = {
        s: dossier_mod.supplier_dossier(pkg, s, [])["tags"]
        for s in ("sup:70110", "sup:70130", "sup:70420", "sup:70190")
    }
    assert time.monotonic() - started < 1.5
    assert {"relationship", "weak_costing", "cash_short"} <= set(tags["sup:70110"])
    assert "cash_short" in tags["sup:70130"]
    assert "price_frozen" in tags["sup:70420"]
    assert "hedged_forward" in tags["sup:70190"]


def test_critic_strips_leaks_figures_and_stray_ids():
    text, issues = writer.lint(
        "Thank you. We have no alternative supplier. Our price idea is 12.50 EUR. See idx:LCI-C@2026-06.\n"
        "{{CLAIM:C1}} We value the partnership. This is urgent."
    )
    assert "alternative" not in text and "12.50" not in text and "idx:" not in text and "urgent" not in text
    assert "{{CLAIM:C1}}" in text and "partnership" in text and {"leak", "figure"} <= set(issues)
    case = load_cases()["case:S01"]
    claim = {"cid": "C1", "ids": ["sc:70270@2026Q3"], "sentence": "Your on-time delivery was 84.6% (sc:70270@2026Q3)."}
    body, refs, used = writer.compose(case, text, [claim], [{"unit_price": 16.0, "payment_days": 60}], ["Why?"])
    assert "84.6%" in body and "EUR 16.00" in body and "Why?" in body and refs == ["sc:70270@2026Q3"]
    assert used == ["C1"]


@pytest.mark.parametrize(
    "sentence",
    [
        "We expect a cut of ten percent.",
        "That is roughly forty euros less.",
        "We rely on your delivery.",
        "Our stock is low.",
        "Please reply as quickly as possible.",
        "You are our sole supplier for this part.",
        "We have no other option.",
        "See {{SHEET_LIST}} below.",
    ],
)
def test_critic_drops_spelled_figures_leaks_and_broken_placeholders(sentence):
    assert writer.lint(sentence)[0] == ""


def test_critic_repairs_near_miss_placeholders():
    text, _ = writer.lint("Here: {{sheets}}\n{{CLAIMS}}\nWe see {{claim:c2}} clearly.")
    assert text.splitlines() == ["Here: {{SHEETS}}", "", "We see {{CLAIM:C2}} clearly."]


# --- the agent in process --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "services",
    [
        None,
        FakeServices(error="ValueError"),
        FakeServices(reply="not json at all"),
        FakeServices(reply=None),
        FakeServices(reply=negotiator_reply),
    ],
    ids=["no-services", "model-error", "garbage", "empty", "model"],
)
def test_decide_always_returns_valid_actions_and_small_state(services):
    sim = new_sim()
    for case_id in CASES:
        o = opened(sim, case_id)
        agent = Agent()
        state = agent.prepare({"case": o["case"], "parameters": {"memory": False}})
        out = agent.decide(observation(o["case"], o["threads"]), state, services)
        validate(o["case"], o["threads"], out["actions"])
        assert "emergency_reason" not in out["state"]
        json.dumps(out["state"], allow_nan=False)
        assert size(out["state"]) < STATE_LIMIT


@pytest.mark.parametrize(
    "bad",
    [
        {"sheet_ids": [{"mid": "M1"}], "claim_ids": [{"cid": "C1"}], "questions": "What drives your cost?"},
        {"sheet_ids": [["M1"]], "claim_ids": 2, "questions": 5, "prediction": "open", "email": "hi"},
        {"sheet_ids": 1, "claim_ids": None, "tone": ["warm"], "ask_breakdown": "yes"},
    ],
)
def test_malformed_model_fields_never_reach_the_emergency_path(bad):
    def reply(messages):
        system, user = messages[0]["content"], json.loads(messages[1]["content"])
        if "supplier_reads" in system:
            return json.dumps({"lessons": 5, "feedback": [], "supplier_reads": 3})
        return json.dumps({"suppliers": {s.split(":")[1]: bad for s in user["suppliers"]}})  # keys without "sup:"

    sim = new_sim()
    o = opened(sim, "case:S02")
    agent = Agent()
    state = agent.prepare({"case": o["case"], "parameters": {"memory": False}})
    out = agent.decide(observation(o["case"], o["threads"]), state, FakeServices(reply=reply))
    validate(o["case"], o["threads"], out["actions"])
    assert "emergency_reason" not in out["state"]
    for action in out["actions"]:
        assert action["message"]["term_sheets"]
        assert "W h" not in action["message"]["body"]


def test_final_offer_without_a_trade_is_held_while_a_rival_negotiates():
    sim = new_sim()
    o = opened(sim, "case:S02")
    case, threads = o["case"], o["threads"]
    leds = ledger_mod.build(case, threads)
    final = max(leds, key=lambda s: leds[s]["c"])
    threads[final][-1]["status"] = "final_offer"
    agent = Agent()
    state = agent.prepare({"case": case, "parameters": {"llm": False, "memory": False}})
    out = agent.decide(observation(case, threads), state, None)
    validate(case, threads, out["actions"])
    by_sup = {a["supplier_id"]: a["message"] for a in out["actions"]}
    assert not any(m.get("action") for m in by_sup.values())  # no premature accept
    assert by_sup[final]["term_sheets"] == [] and not by_sup[final]["refs"]  # holds, does not push
    assert all(by_sup[s]["term_sheets"] for s in by_sup if s != final)
    # once no rival is open any more, the cheapest acceptable ready offer is accepted
    for s in threads:
        threads[s][-1]["status"] = "final_offer"
    out = Agent().decide(
        observation(case, threads), agent.prepare({"case": case, "parameters": {"llm": False, "memory": False}}), None
    )
    action = out["actions"][0]
    assert action["message"]["action"]["type"] == "accept"
    assert action["supplier_id"] == min(leds, key=lambda s: leds[s]["c"])


def test_closing_actions_only_target_ready_suppliers():
    from agents._learner import policy

    def led(c, status="open"):
        return {
            "c": c,
            "acceptable": True,
            "offer_id": f"offer:{c}",
            "status": status,
            "moves_left": 5,
            "pushes_after_final": 0,
            "ended": False,
        }

    ledgers = {"sup:a": led(100.0, "agreed"), "sup:b": led(90.0, "final_offer")}
    r = policy.route({}, ledgers, ["sup:a"], None, {}, False)
    assert r["type"] == "close" and r["supplier_id"] == "sup:a"  # b is in flight: the engine settles it


def test_restart_downtime_is_not_taken_for_supplier_latency():
    sim = new_sim()
    o = opened(sim, "case:S01")
    agent = Agent()
    state = agent.prepare({"case": o["case"], "parameters": {"llm": False, "memory": False}})
    sid = o["case"]["suppliers"][0]
    state["sup"][sid].update(sent_at=time.time() - 600, seen=0)
    out = agent.decide(observation(o["case"], o["threads"]), state, None)
    assert not out["state"]["sup"][sid].get("lat")


def test_slow_model_still_returns_inside_the_budget():
    sim = new_sim()
    o = opened(sim, "case:S01")
    agent = Agent()
    state = agent.prepare(
        {"case": o["case"], "parameters": {"memory": False, "decision_seconds": 8, "model_timeout": 2}}
    )
    started = time.monotonic()
    out = agent.decide(observation(o["case"], o["threads"]), state, FakeServices(reply=negotiator_reply, delay=1.5))
    assert time.monotonic() - started < 8 - 1
    validate(o["case"], o["threads"], out["actions"])
    # with too little time left the model is not called at all
    services = FakeServices(reply=negotiator_reply)
    agent.decide(observation(o["case"], o["threads"], seconds=8), out["state"] | {"log": []}, services)
    assert not services.calls


def test_sequential_runner_matches_when_langgraph_is_missing(monkeypatch):
    monkeypatch.setattr(graph, "_GRAPH", None)
    monkeypatch.setitem(sys.modules, "langgraph.graph", None)
    sim = new_sim()
    o = opened(sim, "case:S02")
    agent = Agent()
    state = agent.prepare({"case": o["case"], "parameters": {"memory": False}})
    out = agent.decide(observation(o["case"], o["threads"]), state, None)
    validate(o["case"], o["threads"], out["actions"])
    assert out["state"]["runner"] == "sequential" and len(out["actions"]) == 2


def test_agents_check_passes():
    result = asyncio.run(check_agent(config(agent="learner")))
    assert result["checked_cases"] == CASES


# --- the harness engine on the simulator ---------------------------------------------------------


def run_engine(tmp_path, name, parameters, model=None, handler=None):
    cfg = config(agent="learner", parameters=parameters, parallel=1)
    if model:
        cfg = cfg | {"model": model, "allow_model": True}
    journal = Journal(tmp_path / name)
    journal.put("config", cfg)

    async def scenario():
        sim = new_sim()
        sim.teams["test"] = "test"
        api = Transport(
            cfg, journal, url="http://127.0.0.1", key="test", transport=httpx.ASGITransport(app=create_app(sim))
        )
        models = Models(cfg, journal, transport=httpx.MockTransport(handler)) if handler else None
        engine = Engine(cfg, journal, api=api, models=models)
        assert await engine.run() == 0

    asyncio.run(scenario())
    out = report(journal)
    states = {r[0]: json.loads(r[1]) for r in journal.db.execute("SELECT id, value FROM cases")}
    journal.close()
    return out, states


def test_engine_plays_all_cases_and_learns_within_the_run(tmp_path):
    params = {"llm": False, "runs_dir": str(tmp_path)}
    out, states = run_engine(tmp_path, "run-a", params)
    assert out["coverage"] == 1 and out["fallbacks"] == 0 and out["model_calls"] == 0
    seen = [states[c]["agent_state"]["mem"]["episodes"] for c in CASES]
    assert seen[0] == 0 and seen[1] >= 1 and seen[2] >= 2  # later cases see earlier ones
    assert states["case:S03"]["agent_state"]["mem"]["run"] == "run-a"
    # a new run starts with an empty memory although the first run's journal is next to it
    _, states_b = run_engine(tmp_path, "run-b", params)
    assert states_b["case:S01"]["agent_state"]["mem"]["episodes"] == 0
    assert states_b["case:S03"]["agent_state"]["mem"]["run"] == "run-b"


def test_engine_with_a_mocked_model_uses_it_and_reflects(tmp_path):
    def handle(request):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": negotiator_reply(body["messages"])}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    model = {
        "name": "mock",
        "base_url": "https://mock.invalid/v1",
        "max_calls": 200,
        "max_output_tokens": 500,
        "parallel": 4,
        "timeout": 10,
    }
    out, states = run_engine(tmp_path, "run-m", {"runs_dir": str(tmp_path), "model_timeout": 10}, model, handle)
    assert out["coverage"] == 1 and out["fallbacks"] == 0 and out["model_calls"] > 0
    logs = [e for c in CASES for e in states[c]["agent_state"]["log"]]
    assert any(e["src"] == "llm" for e in logs)
    assert any(states[c]["agent_state"].get("reflection", {}).get("source") == "llm" for c in CASES)
    # S01's reflection (a relationship supplier) is retrieved for S02's relationship suppliers, not for S03's
    assert any(
        k.startswith("archetype|relationship|tone|do") for k in states["case:S02"]["agent_state"]["mem"]["lessons"]
    )
    assert not any("relationship|tone" in k for k in states["case:S03"]["agent_state"]["mem"]["lessons"])


def test_agent_never_writes_into_the_repository(tmp_path):
    before = {
        p: p.stat().st_mtime for p in (ROOT / "agents").rglob("*") if p.is_file() and "__pycache__" not in p.parts
    }
    run_engine(tmp_path, "run-w", {"llm": False, "runs_dir": str(tmp_path)})
    after = {p: p.stat().st_mtime for p in (ROOT / "agents").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert before == after
    con = sqlite3.connect(tmp_path / "run-w" / "run.sqlite")
    assert con.execute("SELECT count(*) FROM cases").fetchone()[0] == 3
    con.close()


# --- round-2 review regressions --------------------------------------------------------------------


def _led(c, status="open", moves=5, pushes=0, acceptable=True):
    return {
        "c": c,
        "acceptable": acceptable,
        "offer_id": f"offer:{c}",
        "status": status,
        "moves_left": moves,
        "pushes_after_final": pushes,
        "ended": False,
    }


def test_a_held_final_offer_stays_held_while_a_rival_is_open():
    from agents._learner import policy

    ledgers = {"sup:f": _led(110.0, "final_offer", pushes=0), "sup:r": _led(100.0, "open")}
    r = policy.route({}, ledgers, ["sup:f"], None, {"allow_final_push": False}, False)
    assert r["type"] == "negotiate" and r["hold"] == {"sup:f": True}
    # after our holding note (no sheets) it is still not counted as pushed, and still held
    sim = new_sim()
    o = opened(sim, "case:S02")
    sid = o["case"]["suppliers"][0]
    events = o["threads"][sid]
    events[-1]["status"] = "final_offer"
    events += [{"from": "buyer", "body": "hold", "term_sheets": []}, {**events[-1], "status": "final_offer"}]
    assert ledger_mod.build(o["case"], o["threads"])[sid]["pushes_after_final"] == 0


def test_without_an_alternative_the_cheapest_final_offer_is_never_pushed():
    from agents._learner import policy

    ledgers = {
        "sup:a": _led(100.0, "final_offer"),
        "sup:b": _led(110.0, "final_offer"),
        "sup:c": _led(120.0, "final_offer"),
    }
    r = policy.route({}, ledgers, ["sup:a", "sup:b", "sup:c"], None, {}, False)
    assert r["type"] == "negotiate"
    assert len(r["final_push"]) == 1 and "sup:a" not in r["final_push"]  # one push per round, fallback kept
    assert r["hold"].get("sup:a")
    single = policy.route({}, {"sup:a": _led(100.0, "final_offer")}, ["sup:a"], None, {}, False)
    assert single["type"] == "close" and single["action"]["type"] == "accept"  # nothing else to fall back on


def test_an_offer_at_or_below_our_last_ask_is_accepted():
    sim = new_sim()
    o = opened(sim, "case:S01")
    case, threads = o["case"], o["threads"]
    agent = Agent()
    state = agent.prepare({"case": case, "parameters": {"llm": False, "memory": False}})
    out = agent.decide(observation(case, threads), state, None)
    sid = case["suppliers"][0]
    sheet = out["actions"][0]["message"]["term_sheets"][0]["terms"]
    reply = sim.message("test", o["session_id"], sid, out["actions"][0]["message"])
    reply["term_sheets"][0]["terms"] = {"unit_price": round(sheet["unit_price"] - 1.0, 2)}  # below our ask
    reply["status"] = "open"
    threads[sid] += [{**out["actions"][0]["message"], "from": "buyer"}, reply]
    for att in reply.get("attachments", []):
        att["text"] = read_document(att["name"], sim.attachment("test", o["session_id"], att["attachment_id"])[0])
    out2 = agent.decide(observation(case, threads), out["state"], None)
    assert out2["actions"][0]["message"].get("action", {}).get("type") == "accept"


def test_rival_plant_should_cost_is_not_a_floor_and_prune_keeps_extremes():
    dossier = {
        "parts": {
            "part:x": {
                "should_cost": [{"id": "cost:x", "supplier_id": "sup:other", "unit_cost": 36.0, "price_unit": 1}]
            }
        }
    }
    case = {"buyer": {}, "type": "rfq"}
    assert offers.should_cost_row(dossier["parts"], "sup:me") is None
    assert offers.anchor_price(case, {"price": 32.9, "supplier_id": "sup:me"}, [], dossier, 0.1) < 32.9
    domains = {f"f{i}": [1, 2, 3, 4, 5] for i in range(5)}
    pruned = offers._prune({k: list(v) for k, v in domains.items()}, {})
    assert math.prod(len(v) for v in pruned.values()) <= offers.MAX_COMBOS
    assert all(1 in v and 5 in v for v in pruned.values())


def test_ids_cited_in_messages_still_in_flight_are_not_cited_again():
    sim = new_sim()
    o = opened(sim, "case:S02")
    case, threads = o["case"], o["threads"]
    agent = Agent()
    state = agent.prepare({"case": case, "parameters": {"llm": False, "memory": False}})
    first = agent.decide(observation(case, threads), state, None)
    cited = {i for a in first["actions"] for i in a["message"]["refs"]}
    one, other = case["suppliers"]
    reply = sim.message(
        "test", o["session_id"], one, next(a for a in first["actions"] if a["supplier_id"] == one)["message"]
    )
    threads[one] += [
        {**next(a for a in first["actions"] if a["supplier_id"] == one)["message"], "from": "buyer"},
        reply,
    ]
    obs = observation(case, threads) | {"ready_suppliers": [one], "seconds_left": {one: 60}}
    second = agent.decide(obs, first["state"], None)  # `other` is still in flight
    assert not set(second["actions"][0]["message"].get("refs") or []) & cited


def test_a_trigger_letter_only_in_the_case_facts_is_used():
    pkg = package(load_params({}))
    letter = "doc:20990101-70110-P99"
    case = {
        **load_cases()["case:S01"],
        "suppliers": ["sup:70110"],
        "parts": ["part:310010"],
        "trigger_doc": letter,
        "refs": [],
        "facts": {letter: {"asked_pct": 9.5, "days": 51, "supplier": "sup:70110", "title": "Letter"}},
    }
    d = dossier_mod.build(case, pkg)
    assert d["trigger"]["days"] == 51 and d["trigger"]["text"]
    kinds = {c["kind"] for c in claims_mod.build_catalog(case, pkg.facts, d, "sup:70110", 150, 9.5)}
    assert "notice" in kinds


# --- practice run 20261010T180504Z-efccf31f regressions ---------------------------------------------


def test_a_final_offer_is_not_pushed_when_walking_would_cost_us():
    from agents._learner import policy

    # P04: its final offer beats our alternative; a push risked a walk-away scoring 0
    r = policy.route({}, {"sup:a": _led(197.0, "final_offer")}, ["sup:a"], 203.0, {}, False)
    assert r["type"] == "close" and r["action"]["type"] == "accept"
    # an alternative at least as good makes the push free
    r = policy.route({}, {"sup:a": _led(205.0, "final_offer")}, ["sup:a"], 203.0, {}, False)
    assert r["type"] == "negotiate" and r["final_push"] == {"sup:a": True}


def test_an_agreement_is_held_while_a_cheaper_rival_negotiates():
    from agents._learner import policy

    # P05: one supplier agreed to a soft ask while a cheaper one was still open
    ledgers = {"sup:a": _led(303.0, "agreed"), "sup:b": _led(269.0), "sup:c": _led(311.0)}
    r = policy.route({}, ledgers, ["sup:a", "sup:b", "sup:c"], None, {}, False)
    assert r["type"] == "negotiate" and r["hold"] == {"sup:a": True}
    # cheapest already and the rivals stand still: take it
    ledgers = {"sup:a": _led(260.0, "agreed"), "sup:b": _led(269.0), "sup:c": _led(311.0)}
    r = policy.route({}, ledgers, ["sup:a"], None, {}, False)
    assert r["type"] == "close" and r["supplier_id"] == "sup:a"


def test_menu_keeps_only_sheets_close_to_the_cheapest_of_their_level():
    menu = [
        {"mid": "M1", "level": "step", "c": 1464865.0},
        {"mid": "M2", "level": "step", "c": 1475230.0},  # P10: +0.7 %
        {"mid": "M3", "level": "step", "c": 1480091.0},  # P10: 56 days lead time, +1.0 %
        {"mid": "M4", "level": "big", "c": 1490000.0},
        {"mid": "M5", "level": "big", "c": 1492000.0},
    ]
    assert [m["mid"] for m in graph.within_band(menu)] == ["M1", "M4", "M5"]


def test_a_mutual_standstill_is_broken_after_one_held_round():
    s = {"v_ask": 100.0, "v_stand_prev": 120.0}
    first = graph.concession_values(s, 1, 6, 100.0, 120.0)
    assert first["step"] == 100.0 and s["held"]
    second = graph.concession_values(s, 2, 6, 100.0, 120.0)
    assert second["step"] == pytest.approx(100.0 + graph.STALL_STEP * 20.0) and not s["held"]


def test_an_offer_matching_our_ask_in_value_but_dearer_for_us_is_not_accepted():
    sim = new_sim()
    o = opened(sim, "case:S01")
    case, threads = o["case"], o["threads"]
    agent = Agent()
    state = agent.prepare({"case": case, "parameters": {"llm": False, "memory": False}})
    out = agent.decide(observation(case, threads), state, None)
    sid = case["suppliers"][0]
    sheet = out["actions"][0]["message"]["term_sheets"][0]["terms"]
    reply = sim.message("test", o["session_id"], sid, out["actions"][0]["message"])
    reply["term_sheets"][0]["terms"] = {"unit_price": round(sheet["unit_price"] - 1.0, 2)}  # below our ask
    reply["status"] = "open"
    threads[sid] += [{**out["actions"][0]["message"], "from": "buyer"}, reply]
    for att in reply.get("attachments", []):
        att["text"] = read_document(att["name"], sim.attachment("test", o["session_id"], att["attachment_id"])[0])
    # P08: pretend our ask was cheaper for us than this offer (the supplier swapped in a dearer term)
    out["state"]["sup"][sid]["c_ask"] = ledger_mod.build(case, threads)[sid]["c"] * 0.97
    out2 = agent.decide(observation(case, threads), out["state"], None)
    assert not out2["actions"][0]["message"].get("action")
