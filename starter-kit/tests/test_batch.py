"""Five-run integration and recovery, all HTTP in process; no sockets or external calls."""

import asyncio
import time

import httpx
import pytest

from arena_kit.harness.batch import Batch
from arena_kit.harness.cli import parser, verify_source
from arena_kit.harness.config import load
from arena_kit.harness.resources import Pacer, Resources
from arena_kit.harness.state import Journal
from arena_kit.harness.transport import Models, Transport, UncertainRequest
from arena_kit.sim import Sim, create_app, load_cases


def journals_at(tmp_path, count, **updates):
    journals = []
    for number in range(count):
        cfg = load(parser().parse_args(["run", "--agent", "learner"])) | {
            "parameters": {"llm": False, "memory": True},
            "parallel": 2,
            "max_cases": 3,
            **updates,
        }
        journal = Journal(tmp_path / f"run-{number}")
        journal.put("config", cfg)
        journals.append(journal)
    return journals


def factory(sim, wrapper=None):
    sim.teams["batch-key"] = "Batch test"
    app = create_app(sim)

    def make(cfg, journal):
        api = Transport(cfg, journal, url="http://127.0.0.1", key="batch-key", transport=httpx.ASGITransport(app=app))
        return wrapper(api, journal) if wrapper else api

    return make


def test_five_runs_keep_memory_and_sessions_separate(tmp_path, monkeypatch):
    import arena_kit.sim as module

    monkeypatch.setattr(module, "MAX_CALLS", 100000)
    journals = journals_at(tmp_path, 5)
    sim = Sim(load_cases(), no_clock=True)

    async def scenario():
        batch = Batch(journals, api_factory=factory(sim))
        batch.resources.pacer.rate = 100000
        assert await batch.run() == 0
        assert batch.resources.peak_sessions == 10
        assert not batch.resources.leases
        sessions = []
        for journal in journals:
            assert journal.get("status") == "completed"
            cases = journal.cases()
            assert len(cases) == 3 and all(c.get("result") for c in cases.values())
            assert not any(row[0] == "fallback" for row in journal.db.execute("SELECT kind FROM events"))
            for case in cases.values():
                state = case["agent_state"]
                assert state["mem"]["run"] == journal.folder.name
                assert 0 <= state["mem"]["episodes"] <= 2
                sessions.append(case["session_id"])
        assert len(set(sessions)) == 15

        # Completed batch resume is a no-op; no API client is constructed.
        def forbidden(*args):
            raise AssertionError("Completed run must not contact the Arena")

        assert await Batch(journals, api_factory=forbidden).run() == 0

    try:
        asyncio.run(scenario())
    finally:
        for journal in journals:
            journal.close()


def test_batch_recovers_saved_open_response_without_duplicate_session(tmp_path, monkeypatch):
    import arena_kit.sim as module

    monkeypatch.setattr(module, "MAX_CALLS", 100000)
    journals = journals_at(tmp_path, 2, max_cases=1)
    sim = Sim(load_cases(), no_clock=True)
    crashed = []

    def wrapper(api, journal):
        original = api.request

        async def request(method, path, **kwargs):
            result = await original(method, path, **kwargs)
            if journal is journals[0] and method == "POST" and path == "sessions" and not crashed:
                crashed.append(result["session_id"])
                raise UncertainRequest("lost after persisted response, before case link")
            return result

        api.request = request
        return api

    async def scenario():
        batch = Batch(journals, api_factory=factory(sim, wrapper), session_limit=2)
        batch.resources.pacer.rate = 100000
        assert await batch.run() == 0
        assert journals[0].cases()["case:S01"]["session_id"] == crashed[0]
        assert len({c["session_id"] for j in journals for c in j.cases().values()}) == 2
        assert not batch.resources.leases
        assert sum(journals[0].db.execute("SELECT count(*) FROM operations WHERE path='sessions'").fetchone()) == 1

    try:
        asyncio.run(scenario())
    finally:
        for journal in journals:
            journal.close()


def test_shared_quota_rejects_entire_batch_before_opening(tmp_path):
    journals = journals_at(tmp_path, 5, profile="practice", allow_arena=True, max_cases=3)
    posted = []

    def make(cfg, journal):
        def handle(request):
            if request.method == "POST":
                posted.append(request)
                raise AssertionError("quota preflight must prevent all new sessions")
            path = request.url.path.rsplit("/", 1)[-1]
            value = {
                "me": {"team_name": "Batch test", "practice": {"left": 14}},
                "sessions": [],
                "cases": list(load_cases().values()),
            }[path]
            return httpx.Response(200, json=value)

        return Transport(cfg, journal, transport=httpx.MockTransport(handle))

    async def scenario():
        batch = Batch(journals, api_factory=make)
        batch.resources.pacer.rate = 100000
        with pytest.raises(ValueError, match="shared practice quota"):
            await batch.run()
        assert not posted

    try:
        asyncio.run(scenario())
    finally:
        for journal in journals:
            journal.close()


def test_retained_open_operation_reserves_capacity_after_restart(tmp_path):
    journal = journals_at(tmp_path, 1)[0]
    journal.case("case:S01", case=load_cases()["case:S01"])
    op = journal.operation("sessions", {"case_id": "case:S01"})
    journal.finish(op["id"], {"session_id": "already-open"})
    resources = Resources([journal], session_limit=1)
    assert len(resources.leases) == 1 and "already-open" in resources.owned_sessions()
    assert resources.sessions.locked()
    journal.close()


def test_global_pacer_spaces_requests_across_runs():
    async def scenario():
        pacer = Pacer(rate=9)
        times = []

        async def request():
            await pacer.wait()
            times.append(time.monotonic())

        await asyncio.gather(*(request() for _ in range(5)))
        assert all(b - a >= 1 / 9 - 0.003 for a, b in zip(times, times[1:], strict=False))

    asyncio.run(scenario())


def test_global_model_limit_and_cancelled_queue_do_not_spend_budget(tmp_path):
    journals = journals_at(tmp_path, 2)
    active = peak = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def scenario():
        nonlocal active, peak
        resources = Resources(journals, model_parallel=1)

        async def handle(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.set()
            await release.wait()
            active -= 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 1}})

        model = {"name": "mock", "base_url": "http://127.0.0.1", "max_calls": 2, "max_output_tokens": 100}
        clients = [
            Models(
                j.get("config") | {"model": model}, j, shared=resources.models, transport=httpx.MockTransport(handle)
            )
            for j in journals
        ]
        first = asyncio.create_task(clients[0].complete([]))
        await entered.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(clients[1].complete([]), 0.02)
        assert journals[1].db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        release.set()
        assert await first == "ok" and peak == 1
        await asyncio.gather(*(c.close() for c in clients))

    try:
        asyncio.run(scenario())
    finally:
        for journal in journals:
            journal.close()


def test_final_is_not_a_batch_profile(tmp_path):
    journals = journals_at(tmp_path, 1, profile="final")
    try:
        with pytest.raises(ValueError, match="final is exclusive"):
            Batch(journals)
    finally:
        journals[0].close()


def test_source_fingerprint_includes_helpers_and_dependencies(monkeypatch):
    from arena_kit.harness import config as module

    cfg = load(parser().parse_args(["run", "--agent", "learner"]))
    assert cfg["dependency_hash"]
    from arena_kit.harness import cli

    monkeypatch.setattr(cli, "agent_hash", lambda name: "changed helper")
    with pytest.raises(ValueError, match="changed"):
        verify_source(cfg)
    assert module.agent_hash("learner") == cfg["agent_hash"]


def test_reconcile_all_journals_before_checking_foreign_sessions(tmp_path, monkeypatch):
    import arena_kit.sim as module

    monkeypatch.setattr(module, "MAX_CALLS", 100000)
    journals = journals_at(tmp_path, 2, max_cases=1)
    sim = Sim(load_cases(), no_clock=True)
    make = factory(sim)

    async def scenario():
        from arena_kit.harness.engine import Engine

        journal = journals[1]
        cfg = journal.get("config")
        api = make(cfg, journal)
        engine = Engine(cfg, journal, api=api)
        await engine.select_cases()
        await api.request("POST", "sessions", body={"case_id": "case:S01"}, operation_id="lost-open")
        # The server committed; the second run lost the response before journaling it.
        with journal.db:
            journal.db.execute("UPDATE operations SET status='pending',response=NULL WHERE id='lost-open'")
        await api.close()
        await engine.models.close()
        batch = Batch(journals, api_factory=make, session_limit=2)
        batch.resources.pacer.rate = 100000
        assert await batch.run() == 0
        assert len(sim.sessions) == 2

    try:
        asyncio.run(scenario())
    finally:
        for journal in journals:
            journal.close()


def test_failed_run_does_not_abort_other_experiment(tmp_path, monkeypatch):
    import arena_kit.sim as module

    monkeypatch.setattr(module, "MAX_CALLS", 100000)
    journals = journals_at(tmp_path, 2, max_cases=1)
    sim = Sim(load_cases(), no_clock=True)

    def wrapper(api, journal):
        original = api.request

        async def request(method, path, **kwargs):
            reply = await original(method, path, **kwargs)
            if journal is journals[0] and method == "POST" and path == "sessions":
                raise RuntimeError("simulated unrecoverable run failure")
            return reply

        api.request = request
        return api

    async def scenario():
        batch = Batch(journals, api_factory=factory(sim, wrapper), session_limit=2)
        batch.resources.pacer.rate = 100000
        assert await asyncio.wait_for(batch.run(), 20) == 2
        assert journals[0].get("status") == "failed"
        assert journals[1].get("status") == "completed"
        assert len(batch.resources.leases) == 1

    try:
        asyncio.run(scenario())
    finally:
        for journal in journals:
            journal.close()


def test_capacity_retained_by_failed_runs_does_not_hang_waiters(tmp_path):
    journals = journals_at(tmp_path, 2)
    journals[0].case("case:S01", session_id="still-open")

    async def scenario():
        resources = Resources(journals, session_limit=1)

        async def waiting():
            async with resources.session(journals[1], "case:S01"):
                pytest.fail("An unresolved session cannot be treated as a free slot")

        task = asyncio.create_task(waiting())
        await asyncio.sleep(0)
        await resources.finished(journals[0])
        with pytest.raises(RuntimeError, match="retained by interrupted runs"):
            await asyncio.wait_for(task, 1)
        assert len(resources.leases) == 1

    try:
        asyncio.run(scenario())
    finally:
        for journal in journals:
            journal.close()


@pytest.mark.parametrize("empty_quota", [False, True])
def test_resume_prioritizes_existing_session_and_can_close_without_quota(tmp_path, monkeypatch, empty_quota):
    import arena_kit.sim as module
    from arena_kit.harness.engine import Engine

    monkeypatch.setattr(module, "MAX_CALLS", 100000)
    journals = journals_at(tmp_path, 1, max_cases=2, parallel=1, profile="practice", allow_arena=True)
    sim = Sim(load_cases(), no_clock=True)
    opened = []

    def wrapper(api, journal):
        original = api.request

        async def request(method, path, **kwargs):
            reply = await original(method, path, **kwargs)
            if method == "GET" and path == "me" and empty_quota:
                reply["practice"] = {"left": 0}
            if method == "POST" and path == "sessions":
                opened.append(reply["session_id"])
            return reply

        api.request = request
        return api

    make = factory(sim, wrapper)

    async def scenario():
        journal = journals[0]
        cfg = journal.get("config")
        engine = Engine(cfg, journal, api=make(cfg, journal))
        cases = await engine.select_cases()
        # The later case owns the only slot. Starting the first case first would deadlock.
        reply = await engine.api.request("POST", "sessions", body={"case_id": cases[1]["case_id"]})
        journal.case(cases[1]["case_id"], session_id=reply["session_id"])
        await engine.api.close()
        await engine.models.close()
        batch = Batch(journals, api_factory=make, session_limit=1)
        batch.resources.pacer.rate = 100000
        code = await asyncio.wait_for(batch.run(), 20)
        assert code == (2 if empty_quota else 0)
        assert journal.cases()[cases[1]["case_id"]].get("result")
        assert len(opened) == (1 if empty_quota else 2)
        assert not batch.resources.leases

    try:
        asyncio.run(scenario())
    finally:
        journals[0].close()


def test_batch_cli_persists_five_independent_configs_and_resumes_completed_manifest(tmp_path, monkeypatch):
    from arena_kit.harness import cli
    from arena_kit.harness.batch import read_manifest

    invocations = []

    def supervised(folder, journals, lock):
        invocations.append(folder)
        assert len(journals) == 5 and len({j.folder.name for j in journals}) == 5
        for journal in journals:
            assert journal.get("config")["parallel"] == 2
            assert journal.get("batch_id") == folder.name
            journal.put("status", "completed")
        return 0

    monkeypatch.setattr(cli, "supervise_batch", supervised)
    args = parser().parse_args(
        ["batch", "--config", "configs/learner.toml", "--copies", "5", "--runs-dir", str(tmp_path)]
    )
    assert cli.run_batch(args, None) == 0
    folder = invocations[0]
    assert len(read_manifest(folder)["runs"]) == 5
    args = parser().parse_args(["resume-batch", folder.name, "--runs-dir", str(tmp_path)])
    assert cli.run_batch(args, None) == 0
    assert invocations == [folder]
