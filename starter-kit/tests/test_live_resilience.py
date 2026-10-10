"""Local protocol-level rehearsals; no hosted Arena or real model requests."""

import asyncio
import copy
import io
import json

import httpx
import pytest

from arena_kit.harness.cli import parser
from arena_kit.harness.config import load
from arena_kit.harness.engine import Engine
from arena_kit.harness.report import report
from arena_kit.harness.state import Journal
from arena_kit.harness.transport import Transport, UncertainRequest
from arena_kit.harness.validation import validate_case
from arena_kit.harness.worker import Services
from arena_kit.sim import Sim, create_app, load_cases


def cfg(**updates):
    return load(parser().parse_args(["run", "--agent", "evidence_buyer"])) | updates


def test_external_release_is_passed_to_real_worker(tmp_path, monkeypatch):
    from arena_kit.harness.agent_process import AgentProcess
    from arena_kit.harness.transport import Models

    root, release = tmp_path / "package", tmp_path / "external"
    root.mkdir()
    release.mkdir()
    (root / "facts.json").write_text("{}")
    (release / "facts.json").write_text(json.dumps({"sc:new": {"otd_pct": 91, "supplier": "sup:70270"}}))
    monkeypatch.setenv("ARENA_RELEASE", str(release))
    config = load(parser().parse_args(["run", "--agent", "evidence_buyer", "--data", str(root)]))
    assert config["data_release"] == str(release)
    # The worker deliberately does not inherit ARENA_RELEASE or API secrets.
    from test_evidence_buyer import opened

    obs = opened()
    sid = list(obs["threads"])[0]
    facts = {"sc:new": {"otd_pct": 91, "supplier": sid}}
    (release / "facts.json").write_text(json.dumps(facts))
    obs["case"]["refs"] = ["sc:new"]
    obs["case"]["facts"] = {}

    async def run():
        with_journal = Journal(tmp_path / "journal")
        models = Models(config, with_journal)
        worker = AgentProcess(config, models)
        try:
            state = await worker.call("prepare", 10, context={})
            decision = await worker.call("decide", 10, observation=obs, state=state)
            assert decision["state"]["suppliers"][sid]["dossier"]["facts"] == facts
        finally:
            await worker.close()
            await models.close()
            with_journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("placement", ["inside", "sibling", "external"])
def test_public_release_preflight_uses_same_resolved_package(tmp_path, monkeypatch, placement):
    root = tmp_path / "package"
    root.mkdir()
    (root / "facts.json").write_text("{}")
    release = (root if placement == "inside" else tmp_path) / "release-sunday"
    if placement == "external":
        release = tmp_path / "external"
        monkeypatch.setenv("ARENA_RELEASE", str(release))
    else:
        monkeypatch.delenv("ARENA_RELEASE", raising=False)
    release.mkdir()
    (release / "facts.json").write_text('{"news:new":{"text":"new"}}')
    config = load(parser().parse_args(["run", "--data", str(root), "--agent", "evidence_buyer"]))
    config.update(profile="final", allow_arena=True)

    async def run():
        journal = Journal(tmp_path / "journal")

        def respond(request):
            if request.url.path.endswith("/me"):
                return httpx.Response(200, json={"status": "active", "team_name": "local"})
            if request.url.path.endswith("/data"):
                return httpx.Response(200, json={"release-sunday": {"public": True}})
            return httpx.Response(200, json=[])

        api = Transport(config, journal, url="http://127.0.0.1", key="local", transport=httpx.MockTransport(respond))
        engine = Engine(config, journal, api=api)
        try:
            await engine.preflight()
            assert engine.policy_data.release == release
            assert Services(str(root), io.StringIO(), config["data_release"]).data.release == release
        finally:
            await api.close()
            await engine.models.close()
            journal.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation", ["missing_buyer", "missing_volume", "bad_volume", "unknown_operator", "bad_options", "bad_rounds"]
)
def test_invalid_case_isolated_without_consuming_session(tmp_path, mutation):
    async def run():
        cases = copy.deepcopy(load_cases())
        bad = copy.deepcopy(cases["case:S01"])
        bad["case_id"] = "case:A-broken"
        if mutation == "missing_buyer":
            bad.pop("buyer")
        elif mutation == "missing_volume":
            bad["buyer"].pop("need_per_year")
        elif mutation == "bad_volume":
            bad["buyer"]["need_per_year"] = "unknown"
        elif mutation == "unknown_operator":
            bad["buyer"]["must_haves"] = [{"field": "unit_price", "op": "approximately", "value": 10}]
        elif mutation == "bad_options":
            bad["term_options"]["payment_days"] = {"unknown": True}
        elif mutation == "bad_rounds":
            bad["round_limit"] = None
        cases[bad["case_id"]] = bad
        config = cfg(max_cases=4)
        journal = Journal(tmp_path / "journal")
        journal.put("config", config)
        sim = Sim(cases, no_clock=True)
        sim.teams["local"] = "local"
        api = Transport(
            config, journal, url="http://127.0.0.1", key="local", transport=httpx.ASGITransport(app=create_app(sim))
        )

        async def no_pace():
            pass

        api.pace = no_pace
        assert await Engine(config, journal, api=api).run() == 2  # truthful incomplete coverage
        assert len(sim.sessions) == 3
        assert all(s["case"]["case_id"] != "case:A-broken" for s in sim.sessions.values())
        assert all(s["status"] == "ended" for s in sim.sessions.values())
        assert journal.cases()["case:A-broken"]["error"]
        journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("index", range(100))
def test_new_case_ids_types_and_additive_requirements_stay_open(index):
    case = copy.deepcopy(list(load_cases().values())[index % 3])
    case.update(case_id=f"case:never-seen-{index}", type=f"future_category_{index}", new_metadata={"future": True})
    case["suppliers"] = [f"sup:new-{index}"]
    field = f"new_certificate_{index}"
    case["negotiable"].append(field)
    case["term_options"][field] = [False, True]
    case["buyer"]["must_haves"].append({"field": field, "op": "==", "value": True})
    assert validate_case(case) is case
    from test_evidence_buyer import mod, opened, services

    from arena_kit.harness.economics import Economics
    from arena_kit.harness.validation import validate

    obs = opened(f"case:S{index % 3 + 1:02}")
    sid = case["suppliers"][0]
    obs.update(
        case=case, threads={sid: next(iter(obs["threads"].values()))}, ready_suppliers=[sid], seconds_left={sid: 60}
    )
    agent = mod.Agent()
    decision = agent.decide(obs, agent.prepare({}), services())
    validate(case, obs["threads"], decision["actions"], Economics(case))
    sheets = [sheet for action in decision["actions"] for sheet in action["message"].get("term_sheets", [])]
    assert sheets and all(sheet["terms"][field] is True for sheet in sheets)


@pytest.mark.parametrize("fault", ["slow_supplier", "lost_open", "lost_accept", "server_error", "missing_attachment"])
def test_five_waves_unknown_cases_under_protocol_faults(tmp_path, fault):
    async def run():
        config = cfg(profile="final", allow_arena=True, poll_seconds=0.02, parallel=3)
        journal = Journal(tmp_path / "journal")
        journal.put("config", config)
        cases = []
        for wave in range(1, 6):
            case = copy.deepcopy(list(load_cases().values())[(wave - 1) % 3])
            case.update(case_id=f"case:new-wave-{wave}", wave=wave, type=f"new_type_{wave}", set="hidden")
            cases.append(case)
        sim = Sim({c["case_id"]: c for c in cases}, no_clock=True)
        sim.teams["local"] = "local"
        base = httpx.ASGITransport(app=create_app(sim))
        injected = False
        requests = []

        async def handle(request):
            nonlocal injected
            requests.append((request.method, request.url.path))
            path = request.url.path
            if path == "/v1/data":
                return httpx.Response(200, json={})
            if path == "/v1/cases":
                result = []
                for c in cases:
                    sessions = [s for s in sim.sessions.values() if s["case"]["case_id"] == c["case_id"]]
                    ended = bool(sessions and sessions[0]["status"] == "ended")
                    result.append(c | {"open": not ended, "seconds_left": 0 if ended else 480})
                    if not ended:
                        break
                return httpx.Response(200, json=result)
            if fault == "missing_attachment" and "/attachments/" in path:
                return httpx.Response(404, json={"detail": "file not yet available"})
            if fault == "slow_supplier" and path.endswith("/messages"):
                await asyncio.sleep(0.03)
            if fault == "server_error" and not injected and path.endswith("/messages"):
                injected = True
                return httpx.Response(503, json={"detail": "temporary"})
            response = await base.handle_async_request(request)
            if fault == "lost_open" and not injected and request.method == "POST" and path == "/v1/sessions":
                injected = True
                raise UncertainRequest("Simulated crash after server committed open")
            if (
                fault == "lost_accept"
                and not injected
                and request.method == "POST"
                and path.endswith("/messages")
                and json.loads(request.content)["message"].get("action")
            ):
                injected = True
                raise UncertainRequest("Simulated crash after binding closure")
            return response

        def engine():
            api = Transport(config, journal, url="http://127.0.0.1", key="local", transport=httpx.MockTransport(handle))

            async def no_pace():
                pass

            api.pace = no_pace
            return Engine(config, journal, api=api)

        if fault in ("lost_open", "lost_accept"):
            with pytest.raises(UncertainRequest):
                await engine().run()
            assert len(sim.sessions) == 1
        assert await engine().run() == 0
        assert len(sim.sessions) == 5
        assert journal.get("closed_waves") == [1, 2, 3, 4, 5]
        assert report(journal)["coverage"] == 1
        assert all(not any(x in path for x in ("leaderboard", "events", "results", "screen")) for _, path in requests)
        if fault == "missing_attachment":
            assert all(not c["result"]["deal"] for c in journal.cases().values())
        journal.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "method,path",
    [
        ("DELETE", "sessions"),
        ("PUT", "sessions/local/threads/sup/messages"),
        ("GET", "sessions/local/leaderboard"),
        ("GET", "sessions/local/results"),
        ("GET", "sessions/local/events"),
        ("POST", "sessions/local/unexpected/messages"),
    ],
)
def test_unexpected_protocol_operations_never_reach_network(tmp_path, method, path):
    journal = Journal(tmp_path / "journal")

    async def run():
        def reject(_):
            raise AssertionError("Forbidden operation reached network")

        api = Transport(cfg(), journal, url="http://127.0.0.1", key="local", transport=httpx.MockTransport(reject))
        try:
            with pytest.raises(ValueError):
                await api.request(method, path)
        finally:
            await api.close()

    asyncio.run(run())
    journal.close()


@pytest.mark.parametrize("value", ["unknown", "inf", "-inf", "nan", "30.9", "1e400"])
@pytest.mark.parametrize("status", ["open", "final_offer", "agreed"])
def test_readable_but_invalid_quote_numbers_are_not_binding(value, status):
    from test_evidence_buyer import mod, opened, services

    from arena_kit.docs import standard_terms
    from arena_kit.harness.economics import Economics
    from arena_kit.harness.validation import validate

    obs = opened()
    for events in obs["threads"].values():
        events[-1]["status"] = status
        for att in events[-1]["attachments"]:
            att["text"] = "Offered terms\nUnit price (EUR) 15\nOur standard terms\nPayment terms (days net) " + value
        assert standard_terms(events) == {}
    agent = mod.Agent()
    result = agent.decide(obs, agent.prepare({}), services())
    validate(obs["case"], obs["threads"], result["actions"], Economics(obs["case"]))
    assert all(action["message"].get("action", {}).get("type") != "accept" for action in result["actions"])


def test_new_unreadable_quote_does_not_reuse_older_standard_terms():
    from arena_kit.docs import standard_terms

    old = {"attachments": [{"name": "quote.pdf", "text": "Our standard terms\nPayment terms (days net) 90"}]}
    unreadable = {"attachments": [{"name": "quote.pdf", "error": "unreadable"}]}
    assert standard_terms([old])["payment_days"] == 90
    assert standard_terms([old, unreadable]) == {}


def test_real_loopback_http_ten_parallel_sessions(tmp_path, sim):
    """Real TCP HTTP, live clocks, normal pacing, actual worker/parser subprocesses."""
    import time

    url, state, _ = sim
    state.clock = time.time
    state.no_clock = False
    state.teams["local-rehearsal"] = "Local rehearsal"
    cases = {}
    for index in range(12):
        case = copy.deepcopy(list(load_cases().values())[index % 3])
        case.update(case_id=f"case:http-new-{index:02}", round_limit=4)
        cases[case["case_id"]] = case
    state.cases = cases

    async def run():
        config = cfg(max_cases=12, parallel=10)
        journal = Journal(tmp_path / "journal")
        journal.put("config", config)
        api = Transport(config, journal, url=url, key="local-rehearsal")
        engine = Engine(config, journal, api=api)
        peak = 0
        finished = False

        async def monitor():
            nonlocal peak
            while not finished:
                with state.lock:
                    peak = max(peak, sum(s["status"] == "open" for s in state.sessions.values()))
                await asyncio.sleep(0.02)

        observer = asyncio.create_task(monitor())
        try:
            assert await asyncio.wait_for(engine.run(), 120) == 0
            assert len(state.sessions) == 12
            assert 2 <= peak <= 10
            assert all(s["status"] == "ended" for s in state.sessions.values())
            assert all(not thread["timeouts"] for s in state.sessions.values() for thread in s["threads"].values())
            assert report(journal)["coverage"] == 1
        finally:
            finished = True
            await observer
            journal.close()

    asyncio.run(run())
