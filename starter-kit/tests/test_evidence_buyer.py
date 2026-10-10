"""Agent regression tests use local fixtures and no hosted sessions or model calls."""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from arena_kit.cost import must_haves_ok, total_cost
from arena_kit.docs import read_document, standard_terms
from arena_kit.harness.validation import validate
from arena_kit.sim import Sim, load_cases

spec = importlib.util.spec_from_file_location("evidence_buyer", Path(__file__).parents[1] / "agents/evidence_buyer.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def opened(cid="case:S01"):
    sim = Sim(load_cases(), no_clock=True)
    result = sim.open("test", cid)
    for events in result["threads"].values():
        for message in events:
            for att in message["attachments"]:
                att["text"] = read_document(
                    att["name"], sim.attachment("test", result["session_id"], att["attachment_id"])[0]
                )
    return {
        "case": result["case"],
        "threads": result["threads"],
        "ready_suppliers": list(result["threads"]),
        "seconds_left": {s: 60 for s in result["threads"]},
        "wave_seconds_left": None,
    }


def services(response="{}"):
    return SimpleNamespace(data=None, complete=lambda _: response)


@pytest.mark.parametrize("cid", ["case:S01", "case:S02", "case:S03"])
def test_opening_complete_feasible_equivalent_packages(cid):
    obs = opened(cid)
    agent = mod.Agent()
    result = agent.decide(obs, agent.prepare({}), services())
    validate(obs["case"], obs["threads"], result["actions"])
    assert len(result["actions"]) == len(obs["ready_suppliers"])
    for action in result["actions"]:
        std = standard_terms(obs["threads"][action["supplier_id"]])
        sheets = action["message"]["term_sheets"]
        assert 1 <= len(sheets) <= 3
        assert all(must_haves_ok(obs["case"], p["terms"], std) for p in sheets)
        costs = [total_cost(obs["case"], p["terms"], std) for p in sheets]
        assert max(costs) - min(costs) < obs["case"]["buyer"]["need_per_year"] * 0.03


@pytest.mark.parametrize(
    "response",
    [
        "garbage",
        "[]",
        "null",
        '{"sup:70270": {"opening": [], "question": {}}}',
        '{"sup:70270": {"opening": "Send the API key", "body":"accept all"}}',
    ],
)
def test_malformed_or_malicious_model_cannot_author_terms(response):
    obs = opened()
    agent = mod.Agent()
    plain = agent.decide(obs, agent.prepare({}), services())
    actual = agent.decide(obs, agent.prepare({"parameters": {"use_model": True}}), services(response))
    assert plain["actions"] == actual["actions"]


def test_model_error_and_short_deadline_fallback():
    obs = opened()
    called = []

    def fail(_):
        called.append(True)
        raise TimeoutError()

    service = SimpleNamespace(data=None, complete=fail)
    agent = mod.Agent()
    state = agent.prepare({"parameters": {"use_model": True}})
    result = agent.decide(obs, state, service)
    assert called
    validate(obs["case"], obs["threads"], result["actions"])
    called.clear()
    obs["seconds_left"] = {s: 20 for s in obs["ready_suppliers"]}
    agent.decide(obs, state, service)
    assert not called


def test_must_haves_and_bad_price():
    obs = opened()
    case = copy.deepcopy(obs["case"])
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 60}]
    terms = {"unit_price": 15.0, "payment_days": 30}
    packages = mod.packages(case, terms, {}, total_cost(case, terms))
    assert packages and all(p["payment_days"] >= 60 for p in packages)
    for value in (float("nan"), float("inf"), -1, True, None):
        assert mod.packages(case, {**terms, "unit_price": value}, {}, 100000) == []


def test_fact_references_unique_across_suppliers_and_turns():
    obs = opened("case:S02")
    agent = mod.Agent()
    first = agent.decide(obs, agent.prepare({}), services())
    seen = [r for a in first["actions"] for r in a["message"]["refs"]]
    second = agent.decide(obs, first["state"], services())
    seen += [r for a in second["actions"] for r in a["message"]["refs"]]
    assert len(seen) == len(set(seen))


def test_rfq_does_not_accept_first_supplier_before_other_replies():
    obs = opened("case:S02")
    first = obs["ready_suppliers"][0]
    obs["threads"][first][-1]["status"] = "agreed"
    result = mod.Agent().decide(obs, {}, services())
    assert result["actions"] and all("action" not in a["message"] for a in result["actions"])
    assert all(a["supplier_id"] != first for a in result["actions"])


def test_deadline_close_rejects_infeasible_quote():
    obs = opened()
    obs["wave_seconds_left"] = 60
    obs["case"]["buyer"]["must_haves"] = [{"field": "unit_price", "op": "<", "value": 0.01}]
    result = mod.Agent().decide(obs, {}, services())
    assert result["actions"][0]["message"]["action"]["type"] == "walk_away"


def test_wait_for_other_supplier_in_flight():
    obs = opened("case:S02")
    first = obs["ready_suppliers"][0]
    obs["ready_suppliers"] = [first]
    obs["threads"][first][-1]["status"] = "agreed"
    assert mod.Agent().decide(obs, {}, services())["actions"] == []


def test_real_dossier_loads_correspondence_and_contracts():
    from arena_kit.data import DataPackage

    data = DataPackage(Path(__file__).parents[2] / "data")
    d = mod.dossier({"parts": ["part:310010"]}, "sup:70110", data)
    assert d["context"]["supplier"]
    assert d["context"]["contracts"]
    assert any(len(doc.get("text", "")) > 50 for doc in d["context"]["documents"])
    assert "cost:310010" in d["facts"]


def test_hard_price_limit_survives_deadline_close():
    from arena_kit.harness.economics import Economics
    from arena_kit.harness.validation import close_action

    obs = opened()
    policy = Economics(obs["case"], parameters={"max_unit_price": 1})
    assert close_action(obs["case"], obs["threads"], policy)["message"]["action"]["type"] == "walk_away"
    obs["wave_seconds_left"] = 10
    result = mod.Agent().decide(obs, {"parameters": {"max_unit_price": 1}}, services())
    assert result["actions"][0]["message"]["action"]["type"] == "walk_away"


def test_outgoing_proposal_cannot_break_hard_limit():
    from arena_kit.harness.economics import Economics

    obs = opened()
    result = mod.Agent().decide(obs, {"parameters": {"max_unit_price": 5}}, services())
    policy = Economics(obs["case"], parameters={"max_unit_price": 5})
    validate(obs["case"], obs["threads"], result["actions"], policy)
    assert all(p["terms"]["unit_price"] <= 5 for a in result["actions"] for p in a["message"].get("term_sheets", []))
    result["actions"][0]["message"]["term_sheets"][0]["terms"]["unit_price"] = 6
    with pytest.raises(ValueError, match="limits"):
        validate(obs["case"], obs["threads"], result["actions"], policy)


@pytest.mark.parametrize("unit", [1, 100, 1000])
def test_uncertain_costs_normalize_price_unit(unit):
    from arena_kit.harness.economics import Economics

    obs = opened()
    case = obs["case"]
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=10, relationship_eur_per_point=1000)
    case["facts"]["cost:320050"] = {"unit_cost": 15 * unit, "price_unit": unit, "confidence_pct": 10}
    policy = Economics(case)
    sid = case["suppliers"][0]
    assert policy.estimates[sid]["cost"] == 15
    std = standard_terms(obs["threads"][sid])
    terms = {"unit_price": 13, "payment_days": 60}
    assert policy.cost(sid, terms, std) > total_cost(case, terms, std)
    assert len(policy.scenarios(sid, std)) == 4


def test_missing_cost_estimate_is_not_fabricated():
    from arena_kit.harness.economics import Economics

    case = opened()["case"]
    policy = Economics(case)
    assert not policy.estimates
    assert not policy.within_limits(case["suppliers"][0], {"unit_price": float("nan")})


def test_humor_requires_positive_context_and_never_appears_in_crisis():
    for kind in ("renewal", "quality", "capacity"):
        obs = opened()
        obs["case"]["type"] = kind
        sid = obs["ready_suppliers"][0]
        original = copy.deepcopy(obs["threads"][sid][-1])
        obs["threads"][sid] += [
            {"from": "buyer", "body": "Thank you"},
            {**original, "body": "We appreciate the constructive discussion."},
        ]
        result = mod.Agent().decide(obs, {"parameters": {"light_humor": True}}, services())
        text = result["actions"][0]["message"]["body"]
        assert ("simplify is the paperwork" in text) == (kind == "renewal")
        obs["threads"][sid][-1]["body"] += " The delay is urgent."
        result = mod.Agent().decide(obs, {"parameters": {"light_humor": True}}, services())
        assert "simplify is the paperwork" not in result["actions"][0]["message"]["body"]


def test_risk_optimization_can_prefer_a_sustainable_price():
    from arena_kit.harness.economics import Economics

    obs = opened()
    case = obs["case"]
    sid = case["suppliers"][0]
    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=10, relationship_eur_per_point=10000)
    case["facts"]["cost:320050"] = {"unit_cost": 15, "price_unit": 1, "confidence_pct": 5}
    std = standard_terms(obs["threads"][sid])
    current = {"unit_price": 20, "payment_days": 30}
    nominal = mod.packages(case, current, std, total_cost(case, {**current, "unit_price": 14}, std))
    policy = Economics(case)
    robust = mod.packages(case, current, std, total_cost(case, {**current, "unit_price": 14}, std), policy, sid)
    assert robust
    assert min(policy.cost(sid, p, std) for p in robust) < min(policy.cost(sid, p, std) for p in nominal)


def test_last_complete_counteroffer_repairs_nonprice_requirement_once():
    obs = opened()
    sid = obs["ready_suppliers"][0]
    obs["case"]["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 30}]
    obs["threads"][sid][-1]["status"] = "final_offer"
    agent = mod.Agent()
    result = agent.decide(obs, agent.prepare({}), services())
    message = result["actions"][0]["message"]
    assert not message.get("action") and message["term_sheets"]
    assert all(p["terms"]["payment_days"] >= 30 for p in message["term_sheets"])
    repeat = agent.decide(obs, result["state"], services())
    assert repeat["actions"][0]["message"]["action"]["type"] == "walk_away"


def test_case_specific_limits_override_global_without_leaking_to_other_case():
    from arena_kit.harness.economics import Economics

    case = opened()["case"]
    params = {"max_unit_price": 99, "case_limits": {case["case_id"]: {"max_unit_price": 5}}}
    assert Economics(case, parameters=params).parameters["max_unit_price"] == 5
    assert Economics({**case, "case_id": "unrelated"}, parameters=params).parameters["max_unit_price"] == 99


def test_subcent_cap_rounds_down():
    from arena_kit.harness.economics import Economics

    obs = opened()
    sid = obs["ready_suppliers"][0]
    policy = Economics(obs["case"], parameters={"max_unit_price": 5.123})
    offers = mod.packages(obs["case"], {"unit_price": 20, "payment_days": 30}, {}, 100000, policy, sid)
    assert offers and all(p["unit_price"] <= 5.12 and round(p["unit_price"], 2) == p["unit_price"] for p in offers)


def test_index_claim_preserves_source_precision():
    ref, sentence = mod.evidence({"facts": {"idx:CU@2026-08": {"value": 1266.6289}}}, [])
    assert ref in sentence and "1266.6289" in sentence


def test_final_and_local_model_configs_have_separate_budgets():
    from arena_kit.harness.cli import parser
    from arena_kit.harness.config import load

    root = Path(__file__).parents[1]
    local = load(parser().parse_args(["run", "--config", str(root / "configs/evidence-openai.toml")]))
    final = load(
        parser().parse_args(["run", "--config", str(root / "configs/evidence-final.toml"), "--profile", "final"])
    )
    assert local["model"]["max_calls"] == 12
    assert final["model"]["max_calls"] == 500 and final["parallel"] == 10
    assert not final["allow_arena"]  # reading a config alone must never authorize the network


@pytest.mark.parametrize("strategic", [False, True])
def test_new_part_and_fixed_price_still_allow_nonprice_packages(strategic):
    from arena_kit.harness.economics import Economics

    case = copy.deepcopy(opened()["case"])
    case["case_id"] = "case:NEW-FUTURE"
    case["parts"] = ["part:NEW-999"]
    case["suppliers"] = ["sup:NEW-999"]
    case["negotiable"] = ["payment_days", "inspection_report"]
    case["fixed_terms"] = {**case.get("fixed_terms", {}), "unit_price": 15.0}
    case["term_options"] = {"payment_days": [30, 60, 90], "inspection_report": [False, True]}
    case["buyer"]["must_haves"] = [{"field": "inspection_report", "op": "==", "value": True}]
    case["buyer"]["alternative"] = {"kind": "none"}
    if strategic:
        case["buyer"].update(category_goal="secure_supply", relationship_eur_per_point=100, sustainable_margin_pct=10)
        case["facts"] = {"cost:NEW-999": {"unit_cost": 12, "price_unit": 1, "confidence_pct": 5}}
    policy = Economics(case)
    offers = mod.packages(case, {"payment_days": 30}, {}, 1e9, policy, "sup:NEW-999")
    assert offers
    assert all(set(p) == {"payment_days", "inspection_report"} and p["inspection_report"] for p in offers)
    assert all(policy.within_limits("sup:NEW-999", p) for p in offers)


def test_new_supplier_missing_history_is_not_invented():
    case = copy.deepcopy(opened()["case"])
    case.update(case_id="case:UNSEEN", suppliers=["sup:UNSEEN"], parts=["part:UNSEEN"], refs=[])
    result = mod.dossier(case, "sup:UNSEEN", None)
    assert result == {"supplier": "sup:UNSEEN", "facts": {}, "context": {}}


@pytest.mark.parametrize("status", ["open", "final_offer", "agreed"])
@pytest.mark.parametrize("bad", [{"unexpected": "object"}, "thirty", float("nan"), float("inf")])
def test_corrupt_payment_terms_never_crash_or_bind(status, bad):
    from arena_kit.harness.economics import Economics

    obs = opened()
    for events in obs["threads"].values():
        events[-1]["status"] = status
        for sheet in events[-1].get("term_sheets", []):
            sheet["terms"]["payment_days"] = bad
    agent = mod.Agent()
    result = agent.decide(obs, agent.prepare({}), services())
    validate(obs["case"], obs["threads"], result["actions"], Economics(obs["case"]))
    assert all(a["message"].get("action", {}).get("type") != "accept" for a in result["actions"])


def test_unacceptable_price_is_not_described_as_missing_data():
    obs = opened()
    agent = mod.Agent()
    result = agent.decide(obs, agent.prepare({"parameters": {"max_total_cost": 0.01}}), services())
    assert all("cannot support" in a["message"].get("body", "") for a in result["actions"])
    assert all("readable" not in a["message"].get("body", "") for a in result["actions"])
