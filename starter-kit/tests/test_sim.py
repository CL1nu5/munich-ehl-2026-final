"""Contract tests: the kit's client and both example agents against arena-sim over HTTP - and
against a hosted arena when ARENA_TEST_URL and ARENA_TEST_KEY are set."""

import json
import os
import secrets
import sys
import time

import httpx
import pytest

from arena_kit import ArenaClient, ArenaError, RemoteTable, play, play_all
from arena_kit.agents import LLMAgent, NaiveAgent
from arena_kit.docs import quote_terms
from arena_kit.play import fetch_documents, play_waves

RESULT = {"ended", "deal", "supplier_id", "offer_id", "terms", "c_open", "c_deal", "c_best", "score"}


def team(url, code="sim"):
    key = ArenaClient(url).register(f"test-{secrets.token_hex(3)}", code)["api_key"]
    return ArenaClient(url, key, rate=1000)


def scripted(system, user):
    """A stand-in model: lowballs on the first live thread, accepts once the supplier stops."""
    sid = ":".join(user.split("THREAD ")[1].split(":")[:2])
    stop = any(f"status {s}" in user for s in ("final_offer", "agreed"))
    if stop or user.count("--- buyer") >= 8:
        return json.dumps({"supplier_id": sid, "action": "accept"})
    body = "Thank you. Could you send a cost breakdown?"
    return json.dumps({"supplier_id": sid, "action": "send", "body": body, "term_sheets": [{"unit_price": 1.0}]})


@pytest.fixture(params=["sim", "hosted"])
def arena(request):
    if request.param == "hosted":
        url, key = os.environ.get("ARENA_TEST_URL"), os.environ.get("ARENA_TEST_KEY")
        if not (url and key):
            pytest.skip("set ARENA_TEST_URL and ARENA_TEST_KEY to run against a hosted arena")
        return ArenaClient(url, key), 1
    url, _, _ = request.getfixturevalue("sim")
    return team(url), 3


def test_both_agents_play_unchanged(arena):
    client, n = arena
    for case in client.cases("practice")[:n]:
        for agent in (NaiveAgent(), LLMAgent(scripted)):
            result = play(RemoteTable(client), case["case_id"], agent)
            assert RESULT <= set(result) and result["ended"]
            assert result["score"] is None or 0 <= result["score"] <= 1


def test_naive_is_deterministic_and_play_all_runs_in_parallel(sim):
    client = team(sim[0])
    ids = [c["case_id"] for c in client.cases()]
    first = play_all(lambda: RemoteTable(client), ids + ["case:NOPE"], NaiveAgent)
    second = play_all(lambda: RemoteTable(client), ids, NaiveAgent)
    assert all(first[i] == second[i] and first[i]["deal"] for i in ids)
    assert isinstance(first["case:NOPE"], ArenaError) and first["case:NOPE"].status == 404


def test_documents_follow_the_arena_format(sim):
    client, table = team(sim[0]), None
    table = RemoteTable(client)
    case, threads = table.open("case:S03")
    sup, [opening] = next(iter(threads.items()))
    terms = quote_terms(fetch_documents(table, opening)["attachments"][0]["text"])
    assert terms["stated"] == opening["term_sheets"][0]["terms"]
    assert set(terms["standard"]) == set(opening["missing_fields"])
    assert terms["fixed"]["lead_time_days"] == case["fixed_terms"]["lead_time_days"]
    reply = table.send(sup, {"body": "Could you send a cost breakdown? (idx:NOPE@2026-01)", "term_sheets": []})
    assert reply["claim_results"] == [{"index": 0, "result": "contradicted"}]  # an ID the data lacks
    assert "=SUM(B2:B5)" in fetch_documents(table, reply)["attachments"][0]["text"]


def test_a_rival_quote_counts_once_per_supplier_the_rest_once_per_case(sim):
    url, state, _ = sim
    state.facts["quote:9001"] = {"unit_price": 100.0}  # a rival's quote the buyer holds
    table = RemoteTable(team(url))
    _, threads = table.open("case:S02")
    a, b = threads
    rival = threads[a][0]["term_sheets"][0]["term_sheet_id"]  # a's opening, an offer: ID

    def cite(sup, ids):
        reply = table.send(sup, {"body": f"Please consider {', '.join(ids)}.", "term_sheets": []})
        return [r["result"] for r in reply["claim_results"]]

    assert cite(a, ["quote:9001", "idx:PPI-C241@2026-08"]) == ["verified", "verified"]
    assert cite(b, ["quote:9001", "idx:PPI-C241@2026-08", rival]) == ["verified", "duplicate", "verified"]
    assert cite(b, ["quote:9001", rival, "idx:NOPE@2026-01"]) == ["duplicate", "duplicate", "contradicted"]
    assert cite(b, ["idx:NOPE@2026-01"]) == ["contradicted"]  # an unknown ID never becomes a duplicate


def test_errors_limits_and_retries(sim):
    url, state, _ = sim
    client = team(url)
    assert httpx.get(f"{url}/health").json()["ok"]
    for call, status in [
        (lambda: ArenaClient(url).register("x", "wrong"), 403),
        (lambda: ArenaClient(url, "nokey").cases(), 401),
        (lambda: client.cases("final"), 403),
        (lambda: client.open_session("case:NOPE"), 404),
    ]:
        with pytest.raises(ArenaError) as e:
            call()
        assert e.value.status == status
    sid = client.open_session("case:S01")["session_id"]
    with pytest.raises(ArenaError) as e:  # a term sheet must carry every negotiable field
        client.send_message(sid, "sup:70270", {"term_sheets": [{"terms": {"unit_price": 1}}]})
    assert e.value.status == 422
    with pytest.raises(ArenaError) as e:
        client.backoffice(sid)
    assert e.value.status == 403
    sheet = {"term_sheets": [{"terms": {"unit_price": 15.0, "payment_days": 60}}]}
    headers = {"X-Move-Id": "m1", "X-API-Key": client.http.headers["X-API-Key"]}
    path = f"{url}/v1/sessions/{sid}/threads/sup:70270/messages"
    replies = [httpx.post(path, json={"message": sheet}, headers=headers).json() for _ in range(2)]
    assert replies[0] == replies[1] and state.sessions[sid]["threads"]["sup:70270"]["round"] == 1
    hasty = ArenaClient(url, client.http.headers["X-API-Key"], rate=1000, retries=0)
    with pytest.raises(ArenaError) as e:
        for _ in range(12):
            hasty.send_message(sid, "sup:70270", {"term_sheets": []})
    assert e.value.status in (409, 429)  # the round limit or the rate limit, whichever comes first


def test_the_sixty_second_rule(sim):
    url, state, clock = sim
    client = team(url)
    sid = client.open_session("case:S01")["session_id"]
    clock.now += 61
    with pytest.raises(ArenaError) as e:
        client.send_message(sid, "sup:70270", {"term_sheets": []})
    assert e.value.status == 409 and "late" in e.value.detail
    assert len(client.thread(sid, "sup:70270")["timeouts"]) == 1
    clock.now += 125  # two more: three in a row end the thread, and with it the case
    assert client.session(sid)["end_reason"] == "timeout"


def test_no_clock_never_times_out(sim):
    url, state, clock = sim
    state.no_clock = True  # arena-sim serve --no-clock
    client = team(url)
    sid, low = client.open_session("case:S01")["session_id"], {"unit_price": 1.0, "payment_days": 60}
    for _ in range(3):
        clock.now += 5 * 3600  # hours between moves, by hand
        reply = client.send_message(sid, "sup:70270", {"term_sheets": [{"terms": low}]})
        assert reply["status"] == "open"
    state_ = client.session(sid)
    assert state_["status"] == "open" and state_["threads"]["sup:70270"]["seconds_left"] is None
    assert client.thread(sid, "sup:70270")["timeouts"] == []
    clock.now += 24 * 3600
    assert client.accept(sid, reply["term_sheets"][0]["term_sheet_id"])["deal"]


def test_serve_says_which_clock(monkeypatch, capsys):
    pytest.importorskip("uvicorn")
    import uvicorn

    from arena_kit import sim as sim_mod

    built = []
    monkeypatch.setattr(sim_mod, "create_app", built.append)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    sim_mod.main(["serve"])
    sim_mod.main(["serve", "--no-clock"])
    assert [s.no_clock for s in built] == [False, True]  # the default keeps the arena's rule
    out = capsys.readouterr().out.splitlines()
    assert "60-second rule is on" in out[0] and "--no-clock - no 60-second rule" in out[1]


def test_the_replay_is_a_page_for_a_browser(sim):
    url, state, _ = sim
    client = team(url)
    sid = client.open_session("case:S01")["session_id"]
    sheet = {"terms": {"unit_price": 15.5, "payment_days": 60}}
    body = "<script>alert(1)</script> & 15.50 EUR"
    client.send_message(sid, "sup:70270", {"subject": "<b>Hi</b>", "body": body, "term_sheets": [sheet]})
    client.walk_away(sid)
    s = state.sessions[sid]
    threads = {k: th["messages"] for k, th in s["threads"].items()}
    expected = {"session_id": sid, "case_id": "case:S01", "result": s["result"], "threads": threads}
    expected = json.loads(json.dumps(expected))
    for accept in ({}, {"Accept": "application/json"}, {"Accept": "*/*"}):  # JSON clients: the JSON, unchanged
        r = httpx.get(f"{url}/replay/{sid}", headers=accept)
        assert r.headers["content-type"] == "application/json" and r.json() == expected
    browser = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    page = httpx.get(f"{url}/replay/{sid}", headers=browser)
    assert page.headers["content-type"].startswith("text/html")
    assert "No deal" in page.text and f"{s['result']['c_open']:,.2f}" in page.text and "Unit price (EUR)" in page.text
    assert "<script>" not in page.text and "<b>Hi" not in page.text  # every text escaped
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; 15.50 EUR" in page.text and "&lt;b&gt;Hi&lt;/b&gt;" in page.text


def test_the_structured_path(sim):
    client = team(sim[0])
    s = client.open_session("case:S01")
    offer = s["opening"]["sup:70270"]
    move = {"type": "offer", "offer_id": offer["offer_id"], "terms": {"unit_price": 18.0, "payment_days": 30}}
    reply = client.move(s["session_id"], "sup:70270", move)
    assert reply["status"] == "agreed" and reply["terms"]["unit_price"] == 18.0
    assert client.accept(s["session_id"], reply["offer_id"])["deal"]


class Waves:
    """The client, but `cases("final")` follows a script of Live Final listings (403 = no wave yet)."""

    def __init__(self, client, listings, caps=None):
        self.client, self.listings, self.caps = client, listings, caps or {}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def cases(self, set_="practice"):
        if set_ != "final":
            return self.client.cases(set_)
        listing = self.listings.pop(0) if len(self.listings) > 1 else self.listings[0]
        if listing is None:
            raise ArenaError(403, "the final has not started; its cases appear wave by wave")
        files = {c["case_id"]: c for c in self.client.cases("practice")}
        return [
            {**files[c], "wave": w, "theme": f"Theme {w}", "open": o, "seconds_left": self.caps.get(w, 400) * o}
            for c, w, o in listing
        ]


def test_play_waves_waits_for_each_wave(sim):
    url, _, _ = sim
    one, two = ("case:S01", 1), ("case:S02", 2)
    script = [None, [(*one, True)], [(*one, False), (*two, True)], [(*one, False), (*two, False)]]
    out = play_waves(Waves(team(url), script), NaiveAgent, waves=2, poll=0)
    assert sorted(out) == ["case:S01", "case:S02"] and all(r["ended"] for r in out.values())


def test_play_closes_before_the_cap(sim):
    url, _, _ = sim
    client = team(url)
    result = play(RemoteTable(client), "case:S01", NaiveAgent(), deadline=time.time())
    assert result["ended"] and result["deal"]  # the opening beats having no alternative
    assert client.session(client.sessions()[0]["session_id"])["rounds"] == 0


def test_a_late_accept_is_read_and_played_on(sim):
    url, _, clock = sim

    class LateAccept:
        calls = 0

        def decide(self, case, threads):
            self.calls += 1
            if self.calls == 1:
                clock.now += 61  # too late: the arena answers 409 and counts a timeout
            sid = next(iter(threads))
            sheet_id = [m for m in threads[sid] if m.get("term_sheets")][-1]["term_sheets"][0]["term_sheet_id"]
            return {"supplier_id": sid, "message": {"action": {"type": "accept", "term_sheet_id": sheet_id}}}

    agent = LateAccept()
    result = play(RemoteTable(team(url)), "case:S01", agent)
    assert result["deal"] and agent.calls == 2


def test_quota_header_team_view_and_the_quota_429(sim, monkeypatch):
    import arena_kit.sim

    monkeypatch.setattr(arena_kit.sim, "PRACTICE_LIMIT", 2)
    url, _, _ = sim
    client = team(url)
    assert all(c["hosted"] for c in client.cases())
    out = play_all(lambda: RemoteTable(client), ["case:S01", "case:S02", "case:S03"], NaiveAgent, parallel=1)
    assert out["case:S01"]["ended"] and client.practice_remaining == 0
    assert out["case:S03"].status == 429 and out["case:S03"].detail.startswith("practice limit:")
    me = client.me()
    assert me["practice"]["used"] == 2 and me["practice"]["left"] == 0 and me["practice"]["resets_at"]
    assert set(me["best"]) == {"case:S01", "case:S02"}


def test_a_slow_round_closes_earlier(sim, monkeypatch):
    module = sys.modules["arena_kit.play"]  # arena_kit.play is also the function
    monkeypatch.setattr(module, "CLOSE_BEFORE_CAP_S", 0)
    monkeypatch.setattr(module, "ROUND_MARGIN_S", 0)
    url, _, _ = sim

    class Slow(NaiveAgent):
        def decide(self, case, threads):
            time.sleep(0.6)
            return super().decide(case, threads)

    client = team(url)
    result = play(RemoteTable(client), "case:S01", Slow(), deadline=time.time() + 1.0)
    assert result["ended"] and client.session(client.sessions()[0]["session_id"])["rounds"] == 1


def test_each_wave_plays_to_its_own_cap(sim, monkeypatch):
    module = sys.modules["arena_kit.play"]  # arena_kit.play is also the function
    seen = {}

    def record(table, case_id, agent, deadline=None):
        seen[case_id] = deadline() - time.time()
        return {"ended": True, "deal": False}

    monkeypatch.setattr(module, "play", record)
    url, _, _ = sim
    one, two = ("case:S01", 1), ("case:S02", 2)
    script = [[(*one, True)], [(*one, False), (*two, True)], [(*one, False), (*two, False)]]
    play_waves(Waves(team(url), script, caps={1: 480, 2: 600}), NaiveAgent, waves=2, poll=0)
    assert 470 < seen["case:S01"] <= 480 and 590 < seen["case:S02"] <= 600  # 8 minutes; wave 2 extended by 2
