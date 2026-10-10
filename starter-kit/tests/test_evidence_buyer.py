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
