"""Offline regressions for thread reconciliation and simulator reference costs."""

import asyncio

import pytest
from test_harness import api_fixture, config
from test_harness import journal as journal

from arena_kit.harness.engine import Engine
from arena_kit.harness.validation import close_action
from arena_kit.play import live_threads, standing


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


@pytest.mark.parametrize("status", ["open", "agreed", "final_offer"])
def test_snapshot_preserves_unchanged_live_offer(journal, status):
    async def run():
        cfg = config()
        sim, api = api_fixture(cfg, journal)
        opened = sim.open("test", "case:S01")
        sid = opened["session_id"]
        supplier = next(iter(opened["threads"]))
        th = sim.sessions[sid]["threads"][supplier]
        th["status"] = status
        th["messages"][-1]["status"] = status
        expected = standing(th["messages"])
        count = len(th["messages"])
        engine = Engine(cfg, journal, api=api)
        try:
            _, threads, _ = await engine.snapshot(sid)
            assert standing(threads[supplier]) == expected
            assert len(threads[supplier]) == count
            assert threads[supplier][-1]["status"] == status
        finally:
            await api.close()
            await engine.models.close()

    asyncio.run(run())
