"""Offline regressions for thread reconciliation and simulator reference costs."""

import asyncio
import copy

import pytest
from test_harness import api_fixture, config
from test_harness import journal as journal

from arena_kit.cost import total_cost
from arena_kit.harness.engine import Engine
from arena_kit.harness.validation import close_action
from arena_kit.play import live_threads, standing
from arena_kit.sim import FLOOR, Sim, load_cases


@pytest.mark.parametrize("status", ["agreed", "final_offer", "open"])
def test_snapshot_does_not_revive_server_closed_supplier(journal, status):
    async def run():
        cfg = config()
        sim, api = api_fixture(cfg, journal)
        opened = sim.open("test", "case:S02")
        sid = opened["session_id"]
        first, second = opened["threads"]
        th = sim.sessions[sid]["threads"][first]
        th["messages"][-1]["status"] = status
        th["closed"] = "timeout"
        engine = Engine(cfg, journal, api=api)
        try:
            _, threads, _ = await engine.snapshot(sid)
            assert first not in live_threads(threads)
            assert second in live_threads(threads)
            assert close_action(opened["case"], threads)["supplier_id"] == second
        finally:
            await api.close()
            await engine.models.close()

    asyncio.run(run())


def test_snapshot_uses_timeout_offer_instead_of_stale_mail(journal):
    async def run():
        cfg = config()
        sim, api = api_fixture(cfg, journal)
        opened = sim.open("test", "case:S01")
        sid = opened["session_id"]
        supplier = next(iter(opened["threads"]))
        th = sim.sessions[sid]["threads"][supplier]
        th.update(offer_id="offer:after-timeout", stated={"unit_price": 24.0})
        th["late"].append({"offer_id": th["offer_id"], "status": "final_offer", "terms": th["stated"]})
        th["status"] = "final_offer"
        engine = Engine(cfg, journal, api=api)
        try:
            _, threads, _ = await engine.snapshot(sid)
            assert standing(threads[supplier]) == ("offer:after-timeout", {"unit_price": 24.0})
            assert threads[supplier][-1]["status"] == "final_offer"
        finally:
            await api.close()
            await engine.models.close()

    asyncio.run(run())


@pytest.mark.parametrize("price_first", [True, False])
def test_sim_reference_uses_fixed_terms_standards_and_mandatory_conditions(price_first):
    case = copy.deepcopy(load_cases()["case:S02"])
    case["negotiable"] = ["unit_price", "payment_days", "lead_time_days", "incoterm"]
    if not price_first:
        case["negotiable"].reverse()
    case["term_options"] = {"payment_days": [30, 60], "lead_time_days": [14, 42], "incoterm": ["EXW", "DAP"]}
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": "<=", "value": 30}]
    sim = Sim({case["case_id"]: case}, no_clock=True)
    opened = sim.open("test", case["case_id"])
    session = sim.sessions[opened["session_id"]]
    for thread in session["threads"].values():
        thread["standard"]["ppm_commitment"] = 3000
    sim._end(session, "test", None)
    expected = min(
        total_cost(
            case,
            {"unit_price": round(t["opening"] * FLOOR, 2), "payment_days": 30, "lead_time_days": 14, "incoterm": "DAP"},
            t["standard"],
        )
        for t in session["threads"].values()
    )
    assert session["result"]["c_best"] == expected


def test_sim_impossible_reference_is_explicit_and_not_full_marks():
    case = copy.deepcopy(load_cases()["case:S01"])
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">", "value": 10000}]
    sim = Sim({case["case_id"]: case}, no_clock=True)
    opened = sim.open("test", case["case_id"])
    session = sim.sessions[opened["session_id"]]
    sim._end(session, "test", None)
    assert session["result"]["reference_feasible"] is False
    assert session["result"]["score"] == 0


def test_sim_opening_standard_terms_are_independent_of_field_order():
    case = copy.deepcopy(load_cases()["case:S02"])
    case["negotiable"].reverse()
    sim = Sim({case["case_id"]: case}, no_clock=True)
    opened = sim.open("test", case["case_id"])
    for th in sim.sessions[opened["session_id"]]["threads"].values():
        assert "unit_price" not in th["standard"]
        assert set(th["standard"]) == set(case["negotiable"]) - {"unit_price"}


@pytest.mark.parametrize("share", [0.3, 0.333, 0.125, 1 / 3])
def test_sim_index_clause_survives_pdf_roundtrip(share):
    from arena_kit.docs import quote_terms, read_document
    from arena_kit.sim import pdf

    clause = {"series": "CU-NOTATION", "material_share": share, "deadband_pct": 5, "base_month": "2026-08"}
    content = pdf("Quote", [("Offered terms", {"unit_price": 12.5, "index_clause": clause})])
    actual = quote_terms(read_document("quote.pdf", content))["stated"]
    assert actual["index_clause"] == clause
    assert actual["unit_price"] == 12.5


@pytest.mark.parametrize("committed", [5000, 50000, 500000])
def test_cent_rounding_does_not_discard_valid_large_commitment(committed):
    from test_evidence_buyer import mod, opened

    from arena_kit.docs import standard_terms

    obs = opened("case:S01")
    case = obs["case"]
    case["buyer"]["need_per_year"] = 100
    case["negotiable"] = ["unit_price"]
    case["fixed_terms"]["payment_days"] = 30
    case["fixed_terms"]["volume_per_year"] = committed
    std = standard_terms(next(iter(obs["threads"].values())))
    target = total_cost(case, {"unit_price": 12.346}, std)
    offers = mod.packages(case, {"unit_price": 15}, std, target)
    assert offers
    assert offers[0]["unit_price"] == 12.34
    assert total_cost(case, offers[0], std) <= target
