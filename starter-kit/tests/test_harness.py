"""Offline fault-injection tests; external credentials never enable live tests."""

import asyncio
import contextlib
import copy
import json
import os
import subprocess
import sys

import httpx
import pytest

from arena_kit.client import ArenaError
from arena_kit.docs import read_document
from arena_kit.harness.agent_process import AgentProcess
from arena_kit.harness.cli import parser
from arena_kit.harness.config import authorize, load
from arena_kit.harness.engine import Engine
from arena_kit.harness.report import compare, report
from arena_kit.harness.state import Journal, RunLock, redact
from arena_kit.harness.transport import Models, Transport, UncertainRequest
from arena_kit.harness.validation import acceptable, close_action, validate
from arena_kit.sim import Sim, create_app, load_cases


def config(**updates):
    cfg = load(parser().parse_args(["run"]))
    return cfg | updates


def execute(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "run")
    j.put("config", config())
    yield j
    j.close()


def api_fixture(cfg, journal):
    sim = Sim(load_cases(), no_clock=True)
    sim.teams["test"] = "test"
    api = Transport(
        cfg, journal, url="http://127.0.0.1", key="test", transport=httpx.ASGITransport(app=create_app(sim))
    )
    return sim, api


def test_lock_excludes_another_process_and_releases(tmp_path):
    env = os.environ | {"XDG_STATE_HOME": str(tmp_path)}
    code = "from arena_kit.harness.state import RunLock\nwith RunLock(): print('acquired')"
    with RunLock(tmp_path):
        blocked = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        assert blocked.returncode != 0 and "Another harness run" in blocked.stderr
    success = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert success.returncode == 0 and success.stdout.strip() == "acquired"


def test_keys_do_not_authorize_runs(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "real-but-unused")
    monkeypatch.setenv("LLM_API_KEY", "real-but-unused")
    cfg = config(profile="practice")
    with pytest.raises(ValueError, match="allow-arena"):
        authorize(cfg)
    assert config()["profile"] == "sim"
    with pytest.raises(ValueError, match="clean checkout"):
        authorize(config(profile="final", allow_arena=True), "bad-sha")


def test_supervisor_restarts_at_most_three_times(journal, monkeypatch, tmp_path):
    from arena_kit.harness import cli

    starts = []

    class CrashedEngine:
        def __init__(self, command, **kwargs):
            starts.append(kwargs)

        def wait(self):
            return -9

        def poll(self):
            return -9

    monkeypatch.setattr(cli, "server", lambda *args: contextlib.nullcontext("http://127.0.0.1"))
    monkeypatch.setattr(cli.subprocess, "Popen", CrashedEngine)
    monkeypatch.setattr(cli, "verify_source", lambda cfg: cfg["source"])
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    with RunLock(tmp_path) as lock:
        assert cli.supervise(journal.get("config"), journal, lock) == 2
        assert len(starts) == 4
        assert all(start["pass_fds"] == (lock.file.fileno(),) for start in starts)
    assert journal.get("status") == "interrupted"


def test_recovery_rejects_source_changes(monkeypatch):
    from arena_kit.harness import cli

    cfg = config()
    assert cli.verify_source(cfg)["commit"] == cfg["source"]["commit"]
    monkeypatch.setattr(cli, "tree_hash", lambda _: "modified")
    with pytest.raises(ValueError, match="changed"):
        cli.verify_source(cfg)


def test_transport_reuses_id_after_lost_response(journal):
    ids = []

    async def handle(request):
        ids.append(request.headers["X-Move-Id"])
        if len(ids) == 1:
            raise httpx.ReadError("lost after server processing")
        return httpx.Response(200, json={"session_id": "once"})

    async def scenario():
        api = Transport(config(), journal, transport=httpx.MockTransport(handle))
        result = await api.request("POST", "sessions", body={"case_id": "case:S01"})
        await api.close()
        assert result["session_id"] == "once"

    execute(scenario())
    assert len(ids) == 2 and len(set(ids)) == 1
    assert journal.pending() == []


def test_durable_operation_survives_new_client(journal):
    op = journal.operation("sessions", {"case_id": "case:S01"})
    seen = []

    def handle(request):
        seen.append(request.headers["X-Move-Id"])
        return httpx.Response(200, json={"session_id": "existing"})

    async def scenario():
        api = Transport(config(), journal, transport=httpx.MockTransport(handle))
        await api.request("POST", op["path"], body=json.loads(op["body"]), operation_id=op["id"])
        await api.request("POST", op["path"], body=json.loads(op["body"]), operation_id=op["id"])
        await api.close()

    execute(scenario())
    assert seen == [op["id"]]


@pytest.mark.parametrize("status", [401, 403, 409, 422, 429])
def test_permanent_errors_do_not_retry(journal, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"detail": "practice limit: exhausted" if status == 429 else "rejected"})

    async def scenario():
        api = Transport(config(), journal, transport=httpx.MockTransport(handle))
        with pytest.raises(ArenaError) as e:
            await api.request("POST", "sessions", body={"case_id": "case:S01"})
        assert e.value.status == status
        await api.close()

    execute(scenario())
    assert len(calls) == 1 and not journal.pending()


def test_final_endpoint_allowlist(journal):
    async def scenario():
        api = Transport(config(profile="final", allow_arena=True), journal)
        for path in (
            "leaderboard",
            "events",
            "screen",
            "results",
            "/v1/results",
            "../leaderboard",
            "sessions/abc/backoffice",
            "https://example.com",
        ):
            with pytest.raises(ValueError):
                await api.request("GET", path)
        await api.close()

    execute(scenario())


def test_model_budget_atomic_and_persistent(journal, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "never-log-this-secret")
    cfg = config(
        allow_model=True,
        model={
            "name": "test",
            "base_url": "https://mock.invalid/v1",
            "max_calls": 2,
            "max_output_tokens": 64,
            "parallel": 3,
        },
    )
    seen = []

    async def handle(request):
        seen.append(json.loads(request.content))
        await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        )

    async def scenario():
        broker = Models(cfg, journal, transport=httpx.MockTransport(handle))
        results = await asyncio.gather(*(broker.complete([]) for _ in range(8)), return_exceptions=True)
        assert results.count("ok") == 2
        await broker.close()
        broker = Models(cfg, journal, transport=httpx.MockTransport(handle))
        with pytest.raises(RuntimeError, match="budget"):
            await broker.complete([])
        await broker.close()

    execute(scenario())
    assert len(seen) == 2 and all(x["max_tokens"] == 64 for x in seen)
    assert all("service_tier" not in x for x in seen)
    assert "never-log-this-secret" not in (journal.folder / "run.sqlite").read_bytes().decode(errors="ignore")


def test_reasoning_effort_uses_completion_token_limit(journal):
    model = {"name": "x", "base_url": "https://mock.invalid/v1", "max_calls": 1, "max_output_tokens": 64}
    cfg = config(allow_model=True, model=model | {"reasoning_effort": "medium"})
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def scenario():
        broker = Models(cfg, journal, transport=httpx.MockTransport(handle))
        assert await broker.complete([]) == "ok"
        await broker.close()

    execute(scenario())
    assert seen[0]["reasoning_effort"] == "medium" and seen[0]["max_completion_tokens"] == 64
    assert "max_tokens" not in seen[0]


def test_reasoning_effort_is_validated(tmp_path):
    path = tmp_path / "model.toml"
    path.write_text(
        '[model]\nname = "x"\nbase_url = "https://mock.invalid/v1"\n'
        'max_calls = 1\nmax_output_tokens = 64\nreasoning_effort = "mediun"\n'
    )
    with pytest.raises(ValueError, match="reasoning_effort"):
        load(parser().parse_args(["run", "--config", str(path)]))


@pytest.mark.parametrize("config_name", ["learner_openai", "learner_final"])
@pytest.mark.parametrize("served_tier", ["fast", "priority", "default", None])
def test_luna_fast_mode_is_sent_and_actual_tier_is_reported(journal, config_name, served_tier):
    cfg = load(parser().parse_args(["run", "--config", f"configs/{config_name}.toml", "--allow-model"]))
    journal.put("config", cfg)
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        response = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 10}}
        if served_tier is not None:
            response["service_tier"] = served_tier
        return httpx.Response(200, json=response)

    async def scenario():
        broker = Models(cfg, journal, transport=httpx.MockTransport(handle))
        assert await broker.complete([]) == "ok"
        await broker.close()

    execute(scenario())
    assert len(seen) == 1
    assert seen[0]["service_tier"] == "fast"
    assert seen[0]["model"] == "gpt-6-luna" and seen[0]["reasoning_effort"] == "medium"
    result = report(journal)
    assert result["model_service_tier_requested"] == "fast"
    assert result["model_service_tiers"] == {served_tier or "unknown": 1}
    event = next(e["value"] for e in result["events"] if e["kind"] == "model")
    assert event["service_tier_requested"] == "fast" and event["service_tier"] == served_tier


def test_invalid_service_tier_is_rejected_before_requests(tmp_path):
    path = tmp_path / "model.toml"
    path.write_text(
        '[model]\nname = "gpt-6-luna"\nbase_url = "https://api.openai.com/v1"\n'
        'max_calls = 1\nmax_output_tokens = 64\nservice_tier = "fats"\n'
    )
    with pytest.raises(ValueError, match="service_tier"):
        load(parser().parse_args(["run", "--config", str(path)]))


def test_rejected_fast_tier_does_not_retry_or_silently_change_tier(journal):
    cfg = load(parser().parse_args(["run", "--config", "configs/learner_openai.toml", "--allow-model"]))
    journal.put("config", cfg)
    seen = []

    def handle(request):
        seen.append(json.loads(request.content)["service_tier"])
        return httpx.Response(400, json={"error": {"message": "Service tier not allowed for this project"}})

    async def scenario():
        broker = Models(cfg, journal, transport=httpx.MockTransport(handle))
        with pytest.raises(httpx.HTTPStatusError):
            await broker.complete([])
        await broker.close()

    execute(scenario())
    assert seen == ["fast"]
    assert report(journal)["model_service_tiers"] == {"unknown": 1}


def test_ambiguous_model_call_is_not_retried(journal):
    cfg = config(
        allow_model=True,
        model={"name": "x", "base_url": "https://mock.invalid/v1", "max_calls": 1, "max_output_tokens": 64},
    )

    def handle(request):
        raise httpx.ReadTimeout("unknown billing state")

    async def scenario():
        model = Models(cfg, journal, transport=httpx.MockTransport(handle))
        with pytest.raises(httpx.ReadTimeout):
            await model.complete([])
        with pytest.raises(RuntimeError, match="budget"):
            await model.complete([])
        await model.close()

    execute(scenario())
    assert journal.db.execute("SELECT status FROM model_calls").fetchone()[0] == "unknown"


def opened():
    sim = Sim(load_cases(), no_clock=True)
    result = sim.open("test", "case:S01")
    for events in result["threads"].values():
        for message in events:
            for att in message["attachments"]:
                att["text"] = read_document(
                    att["name"], sim.attachment("test", result["session_id"], att["attachment_id"])[0]
                )
    return result["case"], result["threads"]


def test_acceptance_requires_quote_must_haves_and_alternative():
    case, threads = opened()
    sid = next(iter(threads))
    message = threads[sid][0]
    # A missing or unreadable quote is never silently assumed to have buyer-friendly terms.
    message["attachments"][0]["error"] = "corrupt"
    assert not acceptable(case, threads[sid])
    assert close_action(case, threads)["message"]["action"]["type"] == "walk_away"
    message["attachments"][0].pop("error")
    case["buyer"]["must_haves"] = [{"field": "unit_price", "op": "<", "value": 0.01}]
    assert not acceptable(case, threads[sid])
    with pytest.raises(ValueError, match="Unsafe"):
        validate(
            case,
            threads,
            [
                {
                    "supplier_id": sid,
                    "message": {
                        "action": {"type": "accept", "term_sheet_id": message["term_sheets"][0]["term_sheet_id"]}
                    },
                }
            ],
        )


def test_report_never_hides_missing_scores(journal):
    case = next(iter(load_cases().values()))
    journal.case("one", case=case, case_hash="a", result={"score": 0.9, "deal": True})
    journal.case("two", case=case, case_hash="b", error="failed")
    result = report(journal)
    assert result["mean_score_completed"] == 0.9
    assert result["complete_score"] is None and result["coverage"] == 0.5
    assert compare([result, result])["mean_paired_scores"] == [None, None]
    other = copy.deepcopy(result)
    other["data_hash"] = "different"
    with pytest.raises(ValueError, match="differ"):
        compare([result, other])
    assert compare([result, other], True)["restricted"]
    other["profile"] = "practice"
    with pytest.raises(ValueError, match="profiles"):
        compare([result, other], True)


def test_interrupted_final_has_no_overall_score(journal):
    journal.put("config", config(profile="final"))
    journal.put("status", "interrupted")
    for wave in range(1, 6):
        journal.case(str(wave), case={"wave": wave}, result={"score": 0.9})
    result = report(journal)
    assert result["coverage"] == 1 and result["mean_score_completed"] == 0.9
    assert result["complete_score"] is None and result["final_points"] is None
    journal.put("status", "completed")
    assert report(journal)["final_points"] == 450


def test_redaction(monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "sensitive-test-key")
    assert redact({"error": "key=sensitive-test-key"}) == {"error": "key=[REDACTED]"}


def test_local_engine_all_cases(journal):
    async def scenario():
        cfg = config()
        sim, api = api_fixture(cfg, journal)
        engine = Engine(cfg, journal, api=api)
        assert await engine.run() == 0
        assert len(sim.sessions) == 3
        result = report(journal)
        assert result["coverage"] == 1 and result["model_calls"] == 0 and result["fallbacks"] == 0

    execute(scenario())


def test_recover_open_response_before_case_link(journal):
    async def scenario():
        cfg = config()
        sim, api = api_fixture(cfg, journal)
        engine = Engine(cfg, journal, api=api)
        case = sim.cases["case:S01"]
        engine.remember(case)
        # Simulate process death after durable response, before session ID saved in cases table.
        await api.request("POST", "sessions", body={"case_id": case["case_id"]}, operation_id="open-case:S01")
        assert not journal.cases()[case["case_id"]].get("session_id")
        assert await engine.run() == 0
        assert len(sim.sessions) == 1

    execute(scenario())


def test_unowned_session_blocks_run(journal):
    async def scenario():
        sim, api = api_fixture(config(), journal)
        sim.open("test", "case:S01")
        with pytest.raises(ValueError, match="Unowned"):
            await Engine(config(), journal, api=api).run()
        assert len(sim.sessions) == 1

    execute(scenario())


def test_agent_timeout_kills_worker(journal, monkeypatch):
    # Hang without executing a model, then prove the OS process has exited.
    from arena_kit.harness import agent_process

    original = asyncio.create_subprocess_exec
    processes = []

    async def hanging(*args, **kwargs):
        process = await original(
            sys.executable,
            "-c",
            "import time; time.sleep(120)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(agent_process.asyncio, "create_subprocess_exec", hanging)

    async def scenario():
        models = Models(config(), journal)
        worker = AgentProcess(config(), models)
        with pytest.raises(TimeoutError):
            await worker.call("prepare", 0.1, context={})
        assert worker.process is None
        assert processes[0].returncode is not None
        await models.close()

    execute(scenario())


def test_final_five_waves_with_extension(journal, monkeypatch):
    cfg = config(profile="final", allow_arena=True, poll_seconds=0.02)
    journal.put("config", cfg)

    async def scenario():
        sim = Sim(load_cases(), no_clock=True)
        sim.teams["test"] = "test"
        cases = []
        for wave in range(1, 6):
            case = copy.deepcopy(sim.cases["case:S01"])
            case.update(case_id=f"case:F{wave}", wave=wave, theme="test", set="hidden")
            cases.append(case)
            sim.cases[case["case_id"]] = case
        base = httpx.ASGITransport(app=create_app(sim))
        polls = 0

        async def handle(request):
            nonlocal polls
            if request.url.path == "/v1/data":
                return httpx.Response(200, json={"release-sunday": {"public": False}})
            if request.url.path == "/v1/cases":
                polls += 1
                if polls == 1:
                    return httpx.Response(403, json={"detail": "not started"})
                result = []
                current = 1
                for c in cases:
                    sessions = [s for s in sim.sessions.values() if s["case"]["case_id"] == c["case_id"]]
                    ended = bool(sessions and sessions[0]["status"] == "ended")
                    if c["wave"] <= current:
                        result.append(
                            c | {"open": not ended, "seconds_left": 0 if ended else (480 if polls % 2 else 600)}
                        )
                    if ended:
                        current += 1
                    else:
                        break
                return httpx.Response(200, json=result)
            return await base.handle_async_request(request)

        api = Transport(cfg, journal, url="http://127.0.0.1", key="test", transport=httpx.MockTransport(handle))

        # Real pacing has separate coverage; virtual waves should finish quickly.
        async def no_pace():
            pass

        api.pace = no_pace
        engine = Engine(cfg, journal, api=api)
        assert await engine.run() == 0
        assert journal.get("closed_waves") == [1, 2, 3, 4, 5]
        assert len(sim.sessions) == 5
        assert report(journal)["final_points"] is not None
        assert polls > 5

    execute(scenario())


def test_rfq_dispatches_both_suppliers_before_waiting(journal):
    async def scenario():
        cfg = config(cases=["case:S02"])
        sim = Sim(load_cases(), no_clock=True)
        sim.teams["test"] = "test"
        base = httpx.ASGITransport(app=create_app(sim))
        started = set()
        both = asyncio.Event()

        async def handle(request):
            if request.method == "POST" and request.url.path.endswith("/messages"):
                body = json.loads(request.content)["message"]
                if not body.get("action") and len(started) < 2:
                    started.add(request.url.path)
                    if len(started) == 2:
                        both.set()
                    await asyncio.wait_for(both.wait(), 2)
                    await asyncio.sleep(0.1)
            return await base.handle_async_request(request)

        api = Transport(cfg, journal, url="http://127.0.0.1", key="test", transport=httpx.MockTransport(handle))
        engine = Engine(cfg, journal, api=api)
        assert await engine.run() == 0
        assert both.is_set()
        assert report(journal)["fallbacks"] == 0

    execute(scenario())


def test_invalid_decision_and_corrupt_quote_use_safe_fallback(journal, monkeypatch):
    from arena_kit.harness import engine as module

    class BadAgent:
        def __init__(self, *args):
            pass

        async def call(self, method, *args, **kwargs):
            return {} if method == "prepare" else {"actions": [{"oops": True}], "state": {}}

        async def close(self):
            pass

    monkeypatch.setattr(module, "AgentProcess", BadAgent)

    async def scenario():
        cfg = config(cases=["case:S01"])
        sim = Sim(load_cases(), no_clock=True)
        sim.teams["test"] = "test"
        base = httpx.ASGITransport(app=create_app(sim))

        async def handle(request):
            if "/attachments/" in request.url.path:
                return httpx.Response(200, content=b"corrupt PDF")
            return await base.handle_async_request(request)

        api = Transport(cfg, journal, url="http://127.0.0.1", key="test", transport=httpx.MockTransport(handle))
        assert await Engine(cfg, journal, api=api).run() == 0
        result = journal.cases()["case:S01"]["result"]
        assert not result["deal"]
        assert report(journal)["fallbacks"] == 1

    execute(scenario())


def test_live_final_restarts_after_lost_session_response(journal):
    async def scenario():
        cfg = config(profile="final", allow_arena=True, poll_seconds=0.01)
        journal.put("config", cfg)
        sim = Sim(load_cases(), no_clock=True)
        sim.teams["test"] = "test"
        cases = []
        for wave in range(1, 6):
            c = copy.deepcopy(sim.cases["case:S01"])
            c.update(case_id=f"case:F{wave}", wave=wave, theme="restart", set="hidden")
            cases.append(c)
            sim.cases[c["case_id"]] = c
        base = httpx.ASGITransport(app=create_app(sim))
        lose = True
        lost_id = None

        async def handle(request):
            nonlocal lose, lost_id
            if request.url.path == "/v1/data":
                return httpx.Response(200, json={})
            if request.url.path == "/v1/cases":
                result = []
                for c in cases:
                    sessions = [s for s in sim.sessions.values() if s["case"]["case_id"] == c["case_id"]]
                    ended = bool(sessions and sessions[0]["status"] == "ended")
                    result.append(c | {"open": not ended, "seconds_left": 0 if ended else 480})
                    if not ended:
                        break
                return httpx.Response(200, json=result)
            response = await base.handle_async_request(request)
            if lose and request.method == "POST" and request.url.path == "/v1/sessions":
                lose = False
                lost_id = request.headers["X-Move-Id"]
                raise UncertainRequest("Engine dies after server opened session")
            if request.method == "POST" and request.url.path == "/v1/sessions" and len(sim.sessions) == 1:
                assert request.headers["X-Move-Id"] == lost_id
            return response

        async def fresh():
            api = Transport(cfg, journal, url="http://127.0.0.1", key="test", transport=httpx.MockTransport(handle))

            async def no_pace():
                pass

            api.pace = no_pace
            return Engine(cfg, journal, api=api)

        with pytest.raises(UncertainRequest):
            await (await fresh()).run()
        assert len(sim.sessions) == 1 and len(journal.pending()) == 1
        assert await (await fresh()).run() == 0
        assert len(sim.sessions) == 5
        assert report(journal)["coverage"] == 1

    execute(scenario())


def test_retry_after_and_global_rate_limit(journal):
    seen = []

    def handle(request):
        import time

        seen.append(time.monotonic())
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "rate"})
        return httpx.Response(200, json={})

    async def scenario():
        api = Transport(config(), journal, transport=httpx.MockTransport(handle))
        await asyncio.gather(*(api.request("GET", "me") for _ in range(12)))
        await api.close()

    execute(scenario())
    assert len(seen) == 13
    assert all(sum(0 <= t - start < 0.999 for t in seen) <= 9 for start in seen)


def test_oversized_or_secret_request_rejected_before_network(journal, monkeypatch):
    monkeypatch.setenv("ARENA_API_KEY", "secret-arena-key-value")

    async def scenario():
        api = Transport(config(), journal)
        for body in ({"message": {"body": "secret-arena-key-value"}}, {"message": {"body": "x" * 66000}}):
            with pytest.raises(ValueError):
                await api.request("POST", "sessions/s/threads/a/messages", body=body)
        await api.close()

    execute(scenario())
    assert journal.pending() == []
