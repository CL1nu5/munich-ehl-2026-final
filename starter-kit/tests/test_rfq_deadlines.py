"""Offline regression for parked RFQ deadlines found in the hosted D02 trace."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from test_evidence_buyer import mod, opened, services
from test_harness import config

from arena_kit.harness import engine as engine_module
from arena_kit.harness.engine import Engine
from arena_kit.harness.report import report
from arena_kit.harness.state import Journal
from arena_kit.harness.transport import Transport
from arena_kit.harness.validation import validate
from arena_kit.sim import Sim, create_app, load_cases


@pytest.mark.parametrize("remaining,latency", [(49, 39), (30, 20), (15, 1)])
def test_parked_agreement_is_closed_before_reply_budget_runs_out(remaining, latency):
    obs = opened("case:S02")
    first, second = obs["ready_suppliers"]
    obs["threads"][first][-1]["status"] = "agreed"
    obs["ready_suppliers"] = [first]
    obs["seconds_left"][first] = remaining
    obs["reply_latency_seconds"] = {second: latency}
    result = mod.Agent().decide(obs, {}, services())
    validate(obs["case"], obs["threads"], result["actions"])
    assert result["actions"][0]["message"]["action"]["type"] in ("accept", "walk_away")


@pytest.mark.parametrize("slow_delay", [1.5, 2.0])
def test_engine_rfq_with_asymmetric_delays_keeps_agreed_supplier_in_time(tmp_path, monkeypatch, slow_delay):
    real_monotonic = time.monotonic

    def clock():
        return real_monotonic() * 20

    monkeypatch.setattr(engine_module, "time", SimpleNamespace(monotonic=clock))

    class LocalWorker:
        def __init__(self, *args):
            self.agent = mod.Agent()

        async def call(self, method, timeout, **kwargs):
            if method == "prepare":
                return self.agent.prepare(kwargs["context"])
            return self.agent.decide(kwargs["observation"], kwargs["state"], services())

        async def close(self):
            pass

    monkeypatch.setattr(engine_module, "AgentProcess", LocalWorker)

    class QuickAgreement(Sim):
        def _answer(self, s, th, msg, sheets):
            if th["key"] == "70340":
                return self._mail(s, th, "agreed", sheets[0], "We agree.", ["quote.pdf"])
            return super()._answer(s, th, msg, sheets)

    async def run():
        cfg = config(agent="evidence_buyer", cases=["case:S02"])
        journal = Journal(tmp_path / "journal")
        journal.put("config", cfg)
        sim = QuickAgreement(load_cases(), clock=clock)
        sim.teams["test"] = "test"
        base = httpx.ASGITransport(app=create_app(sim))

        async def handle(request):
            if request.method == "POST" and "/messages" in request.url.path:
                if not json.loads(request.content)["message"].get("action"):
                    await asyncio.sleep(slow_delay if "70360" in request.url.path else 0.5)
            return await base.handle_async_request(request)

        api = Transport(cfg, journal, url="http://127.0.0.1", key="test", transport=httpx.MockTransport(handle))

        async def no_pace():
            pass

        api.pace = no_pace
        try:
            assert await asyncio.wait_for(Engine(cfg, journal, api=api).run(), 20) == 0
            result = report(journal)
            assert result["fallbacks"] == 0
            assert result["deals"] == 1
            assert all(not th["timeouts"] for s in sim.sessions.values() for th in s["threads"].values())
        finally:
            journal.close()

    asyncio.run(run())
