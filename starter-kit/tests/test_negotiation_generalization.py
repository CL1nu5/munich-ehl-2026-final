"""Economic and protocol invariants over synthetic suppliers; no practice IDs or score targets."""

import copy
import json
import random

import pytest

from agents._learner import graph, ledger, offers, preferences
from agents._learner.budget import Budget
from agents._learner.memory import refresh
from agents._learner.memory.episodes import extract
from arena_kit.cost import total_cost
from arena_kit.harness.state import Journal
from arena_kit.harness.validation import close_action, validate

# Separate synthetic regression partitions; these are not a live performance benchmark.
DEV_SEEDS = (13, 29)
HOLDOUT_SEEDS = (101, 307, 911)


def scenario(seed):
    rng = random.Random(seed)
    scale = rng.choice((0.2, 1, 7))
    supplier = f"sup:synthetic-{rng.randrange(100000)}"
    index = {"series": "SYNTHETIC", "material_share": 0.4, "deadband_pct": 5, "base_month": "2031-01"}
    case = {
        "case_id": f"case:synthetic-{seed}",
        "type": "renewal",
        "title": "Synthetic purchasing",
        "suppliers": [supplier],
        "parts": [],
        "round_limit": 10,
        "negotiable": ["unit_price", "payment_days", "index_clause"],
        "term_options": {"payment_days": [14, 30, 60, 90], "index_clause": [None, index]},
        "fixed_terms": {"contract_months": 24, "volume_per_year": 1000, "lead_time_days": 30, "incoterm": "DAP"},
        "buyer": {
            "need_per_year": 1000,
            "horizon_years": 2,
            "current_unit_price": 100 * scale,
            "fallback_unit_price": 130 * scale,
            "interest_rate": 0.15,
            "reference_payment_days": 30,
            "need_lead_time_days": 30,
            "freight_eur_per_unit": {"DAP": 0, "FCA": scale, "EXW": 2 * scale},
            "category_goal": "leverage",
            "must_haves": [],
            "alternative": {"kind": "none"},
            "index_paths": {"SYNTHETIC": 0.08},
            "index_risk_pct": 1,
        },
    }
    quote = {
        "name": "quote-synthetic.pdf",
        "text": "Offered terms\nUnit price (EUR) 100\nOur standard terms\nPayment terms (days net) 30\n",
    }
    sheets = [
        {
            "term_sheet_id": "offer:expensive",
            "terms": {"unit_price": 100 * scale, "payment_days": 14, "index_clause": None},
        },
        {"term_sheet_id": "offer:best", "terms": {"unit_price": 101 * scale, "payment_days": 90, "index_clause": None}},
    ]
    rng.shuffle(sheets)
    message = {
        "from": "sales@synthetic",
        "status": "open",
        "message_id": "message:0",
        "body": "Please review our offers.",
        "term_sheets": sheets,
        "attachments": [quote],
    }
    return case, {supplier: [message]}


@pytest.mark.parametrize("seed", DEV_SEEDS + HOLDOUT_SEEDS)
def test_only_the_standing_sheet_is_accepted_and_a_cheaper_alternative_is_mirrored(seed):
    # The first sheet is the supplier's standing offer; the arena rejects an alternative's ID (422).
    case, threads = scenario(seed)
    sid = case["suppliers"][0]
    for best_first in (True, False):
        sheets = sorted(threads[sid][-1]["term_sheets"], key=lambda x: x["term_sheet_id"] != "offer:best")
        threads[sid][-1]["term_sheets"] = sheets if best_first else sheets[::-1]
        standing = threads[sid][-1]["term_sheets"][0]["term_sheet_id"]
        action = close_action(case, threads)
        assert action["message"]["action"]["term_sheet_id"] == standing
        led = ledger.build(case, threads)[sid]
        assert led["offer_id"] == standing
        assert (led["alt_better"] or {}).get("term_sheet_id") == (None if best_first else "offer:best")
        validate(case, threads, [action])
        if not best_first:
            other = {"type": "accept", "term_sheet_id": "offer:best"}
            with pytest.raises(ValueError, match="stale"):
                validate(case, threads, [{"supplier_id": sid, "message": {"action": other}}])
    stale = copy.deepcopy(action)
    threads[sid].append(
        {**threads[sid][-1], "term_sheets": [threads[sid][-1]["term_sheets"][0] | {"term_sheet_id": "offer:new"}]}
    )
    with pytest.raises(ValueError, match="stale"):
        validate(case, threads, [stale])


@pytest.mark.parametrize("seed", DEV_SEEDS + HOLDOUT_SEEDS)
def test_packages_cost_us_the_same_and_an_insisted_index_clause_is_repriced(seed):
    case, threads = scenario(seed)
    sid = case["suppliers"][0]
    led = ledger.build(case, threads)[sid]
    model = offers.supplier_model(case, led, [], {}, {})
    v = offers._value(case, led["terms"], model)
    menu = offers.build_menu(case, led, model, led["std"], v * 0.8, v, {"step": 0}, led["c"])
    banded = graph.within_band(menu)
    floor = min(m["c"] for m in menu)
    assert banded and all(m["c"] <= floor * (1 + graph.MENU_BAND) for m in banded)
    clause = case["term_options"]["index_clause"][1]
    pref = {"fields": {"index_clause": {"values": [clause]}}}
    with_pref = graph.within_band(menu, case, led["std"], model, pref, led["price"])
    indexed = [m for m in with_pref if m["terms"]["index_clause"] == clause]
    assert indexed and all(m["c"] <= floor * (1 + graph.MENU_BAND) for m in indexed)
    for m in indexed:  # the clause costs us index risk: it comes with a lower price than the same terms without
        same = [
            x
            for x in menu
            if x["terms"]["index_clause"] is None and x["terms"]["payment_days"] == m["terms"]["payment_days"]
        ]
        assert all(m["price"] < x["price"] for x in same if x["level"] == m["level"])
    chosen = preferences.rank(with_pref, pref)[0]
    assert chosen["terms"]["index_clause"] == clause
    validate(case, threads, [{"supplier_id": sid, "message": {"term_sheets": [{"terms": chosen["terms"]}]}}])


def test_generic_should_cost_cannot_erase_observed_competition():
    case, threads = scenario(29)
    sid = case["suppliers"][0]
    led = ledger.build(case, threads)[sid]
    generic = {
        "parts": {
            "part:any": {
                "should_cost": [
                    {"unit_cost": led["price"] * 2, "supplier_id": None, "price_unit": 1, "confidence_pct": 15}
                ]
            }
        }
    }
    assert offers.anchor_price(case, led, [], generic, 0.1) < led["price"]
    assert offers.anchor_price(case, led, [], generic, 0.1) == offers.anchor_price(case, led, [], {}, 0.1)


def test_repeated_counterconditions_are_evidence_and_questions_are_not_repeated():
    case, threads = scenario(29)
    sid = case["suppliers"][0]
    offered = threads[sid][0]["term_sheets"][0]["terms"]
    for number in range(2):
        threads[sid].append(
            {"from": "buyer", "body": "Could you send a cost breakdown?", "term_sheets": [{"terms": offered}]}
        )
        reply = copy.deepcopy(threads[sid][0])
        reply.update(message_id=f"counter:{number}", body="We cannot share our internal cost breakdown.")
        reply["term_sheets"] = [
            {
                "term_sheet_id": f"offer:counter-{number}",
                "terms": {**offered, "index_clause": case["term_options"]["index_clause"][1]},
            }
        ]
        threads[sid].append(reply)
    pref = preferences.observe(case, threads[sid])
    assert pref["fields"]["index_clause"]["source"] == "counter:1"
    assert not preferences.can_ask_breakdown(pref)
    assert not preferences.filter_questions("Please share the cost breakdown.", pref)
    assert preferences.filter_questions("Which delivery schedule works for you?", pref)
    invalid = preferences.update(
        case,
        ledger.build(case, threads)[sid],
        pref,
        [{"field": "payment_days", "values": [0], "evidence": "invented evidence"}],
    )
    assert invalid == pref


def test_run_history_rejects_other_runs_and_unconfirmed_closures(tmp_path):
    case, _ = scenario(13)
    journal = Journal(tmp_path / "run-a")
    journal.case(case["case_id"], case=case, agent_state={"closed": True})
    assert extract(journal.folder) == []
    journal.close()

    class WrongHistory:
        runtime = {"run_id": "run-a"}

        def history(self):
            return {"run_id": "run-b", "episodes": [{"case_id": "other", "result": {"score": 1}}]}

    assert refresh(case, {"memory": True}, {}, {}, WrongHistory())["episodes"] == 0


def test_time_horizon_uses_available_moves_and_wave_time():
    obs = {"ready_suppliers": ["supplier"], "seconds_left": {"supplier": 60}}
    p = {"decision_seconds": 30, "model_timeout": 10}
    assert Budget(obs, p).horizon(2, 8, []) == 10
    assert Budget({**obs, "wave_seconds_left": 160}, p).horizon(2, 8, [45]) < 10


@pytest.mark.parametrize("best_first", [True, False])
def test_final_offer_is_closed_without_a_model(best_first):
    from agents.learner import Agent

    case, threads = scenario(307)
    sid = case["suppliers"][0]
    threads[sid][0]["status"] = "final_offer"
    sheets = sorted(threads[sid][0]["term_sheets"], key=lambda x: x["term_sheet_id"] != "offer:best")
    threads[sid][0]["term_sheets"] = sheets if best_first else sheets[::-1]
    agent = Agent()
    state = agent.prepare({"case": case, "parameters": {"llm": True, "memory": False}})

    class NeverCall:
        cost = staticmethod(total_cost)

        def complete(self, messages):
            raise AssertionError("Closing on a final offer must not call a model")

    out = agent.decide(
        {"case": case, "threads": threads, "ready_suppliers": [sid], "seconds_left": {sid: 60}}, state, NeverCall()
    )
    message = out["actions"][0]["message"]
    if best_first:
        assert message["action"] == {"type": "accept", "term_sheet_id": "offer:best"}
    else:  # its cheaper alternative: we send exactly its terms back
        assert not message.get("action")
        best = next(x for x in sheets if x["term_sheet_id"] == "offer:best")["terms"]
        assert [s["terms"] for s in message["term_sheets"]] == [{f: best[f] for f in case["negotiable"]}]
    assert out["state"]["llm"]["calls"] == 0 and not out["state"].get("closed")


def test_relationship_estimate_uses_same_cost_for_menu_ledger_and_last_ask():
    case, threads = scenario(29)
    sid = case["suppliers"][0]
    case["buyer"].update(category_goal="stabilise", relationship_eur_per_point=500, sustainable_margin_pct=8)
    led = ledger.build(case, threads)[sid]
    model = offers.supplier_model(case, led, [], {}, {})
    led = ledger.build(case, threads, {sid: model})[sid]
    assert led["c"] == offers.cost(case, led["terms"], led["std"], model)
    assert led["c"] == graph._dearest(case, [led["terms"]], led["std"], model)
    assert led["relationship_estimate"] is not None
    assert led["model"]["cost_source"] == "opening-price prior"


def test_audit_uses_only_offers_available_before_acceptance(tmp_path):
    from arena_kit.harness.audit import audit

    case, threads = scenario(13)
    sid = case["suppliers"][0]
    folder = tmp_path / "recorded-run"
    journal = Journal(folder)
    journal.case(case["case_id"], case=case, result={"score": 1, "c_deal": 1})
    op = journal.operation("sessions", {"case_id": case["case_id"]})
    journal.finish(op["id"], {"session_id": "session", "opening": {sid: threads[sid][0]}})
    path = f"sessions/session/threads/{sid}/messages"
    op = journal.operation(path, {"message": {"action": {"type": "accept", "term_sheet_id": "offer:expensive"}}})
    journal.finish(op["id"], {"ended": True, "result": {"score": 1}})
    # A later record or final score must never enter the earlier decision's candidate set.
    future = copy.deepcopy(threads[sid][0])
    future["term_sheets"] = [
        {"term_sheet_id": "offer:future", "terms": {**future["term_sheets"][0]["terms"], "unit_price": 1}}
    ]
    op = journal.operation(path, {"message": {"body": "Later evidence"}})
    journal.finish(op["id"], future)
    journal.close()
    result = audit(folder)
    assert result["missed_current_alternatives"] == 1
    assert result["acceptances"][0]["best_current_id"] == "offer:best"
    assert "offer:future" not in str(result)


@pytest.mark.parametrize("invalid_proposal", [False, True])
def test_model_signals_and_proposals_pass_through_complete_decision(invalid_proposal):
    from agents.learner import Agent

    case, threads = scenario(911)
    sid = case["suppliers"][0]
    clause = case["term_options"]["index_clause"][1]
    evidence = "We require an index clause for this contract."
    threads[sid][0]["body"] = evidence
    agent = Agent()
    state = agent.prepare({"case": case, "parameters": {"llm": True, "memory": False, "two_call": False}})

    class Model:
        cost = staticmethod(total_cost)

        def complete(self, messages):
            payload = json.loads(messages[-1]["content"])
            return json.dumps(
                {
                    "suppliers": {
                        sid: {
                            "sheet_ids": [payload["suppliers"][sid]["menu"][0]["mid"]],
                            "signals": [{"field": "index_clause", "values": [clause], "evidence": evidence}],
                            "proposal": {"unit_price": 1}
                            if invalid_proposal
                            else {"payment_days": 90, "index_clause": clause},
                        }
                    }
                }
            )

    out = agent.decide(
        {"case": case, "threads": threads, "ready_suppliers": [sid], "seconds_left": {sid: 60}}, state, Model()
    )
    validate(case, threads, out["actions"])
    sheets = out["actions"][0]["message"]["term_sheets"]
    assert all(s["terms"]["index_clause"] == clause for s in sheets)
    assert all(s["terms"]["unit_price"] > 1 for s in sheets)
    if not invalid_proposal:
        assert all(s["terms"]["payment_days"] == 90 for s in sheets)
    assert out["state"]["llm"]["calls"] == 1
