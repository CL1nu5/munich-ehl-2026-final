import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from arena_kit import ArenaClient, ArenaError, complete_offer, play_all, total_cost
from arena_kit.agents import LLMAgent, NaiveAgent
from arena_kit.cost import cost_breakdown, must_haves_ok, relationship_cost, score, supplier_value
from arena_kit.data import DataPackage
from arena_kit.docs import quote_terms, read_document
from arena_kit.play import hosted, quota_used_up
from arena_kit.sim import Sim

FIXTURES = Path(__file__).parent / "fixtures"
CASE = {
    "case_id": "case:T01", "set": "practice", "type": "rfq", "title": "Test RFQ",
    "briefing": "Two suppliers quote a pump housing.", "trigger_doc": None,
    "parts": ["part:T1"], "suppliers": ["sup:A", "sup:B"], "negotiable": ["unit_price", "payment_days"],
    "fixed_terms": {"incoterm": "DAP", "lead_time_days": 35, "contract_months": 24, "volume_per_year": 4000},
    "term_options": {"payment_days": [30, 45, 60, 90, 14]},
    "buyer": {"need_per_year": 4000, "horizon_years": 2, "current_unit_price": 40.0, "fallback_unit_price": 46.0,
              "interest_rate": 0.08, "reference_payment_days": 60, "need_lead_time_days": 35,
              "late_cost_per_day": 400, "cost_per_defect": 120,
              "freight_eur_per_unit": {"EXW": 1.9, "FCA": 1.2, "DAP": 0.0}, "index_paths": {}, "must_haves": [],
              "alternative": {"kind": "none", "ref": None, "terms": {}}},
    "round_limit": 10, "refs": [],
}  # fmt: skip

# The brief's example: need 4,000 a year for 2 years.
BRIEF = {**CASE, "fixed_terms": {"incoterm": "DAP", "lead_time_days": 35}}
TWO_YEARS = {"contract_months": 24, "volume_per_year": 4000}


def test_cost_matches_the_brief_example():
    c_open = total_cost(BRIEF, {**TWO_YEARS, "unit_price": 43.20, "payment_days": 30})
    c_deal = total_cost(BRIEF, {**TWO_YEARS, "unit_price": 41.60, "payment_days": 60})
    c_best = total_cost(BRIEF, {**TWO_YEARS, "unit_price": 40.80, "payment_days": 90})
    assert (c_open, c_deal, c_best) == (347_904.00, 332_800.00, 324_224.00)
    assert round(score(c_open, c_deal, c_best), 2) == 0.64
    # 12 months covers half the need: the rest is bought at the fallback price
    half = {"contract_months": 12, "volume_per_year": 4000, "unit_price": 41.60, "payment_days": 60}
    assert total_cost(BRIEF, half) == 166_400 + 184_000


def test_cost_other_terms():
    case = {**BRIEF, "term_options": {"effective_date": ["2027-01-01", "2027-07-01"]}}
    t = {**TWO_YEARS, "unit_price": 50.0, "payment_days": 60, "effective_date": "2027-07-01"}
    pre = 4000 * 181 / 365  # units before the new price applies, at the current price of 40.00
    assert total_cost(case, t) == round(pre * 40 + (8000 - pre) * 50, 2)
    base = {**TWO_YEARS, "unit_price": 10.0, "payment_days": 60}
    case = {**BRIEF, "fixed_terms": {}, "buyer": {**BRIEF["buyer"], "index_paths": {"S1": -0.06}}}
    assert total_cost(case, {**base, "incoterm": "EXW", "lead_time_days": 35}) == 80_000 + 15_200
    assert total_cost(case, {**base, "lead_time_days": 35}) == 95_200  # no Incoterm: costliest
    assert total_cost(case, {**base, "incoterm": "DAP", "lead_time_days": 40}) == 82_000
    clause = {"series": "S1", "material_share": 0.5, "deadband_pct": 2, "base_month": "2026-09"}
    t = {**base, "incoterm": "DAP", "lead_time_days": 35, "index_clause": clause}
    assert total_cost(case, t) == 78_400  # 50% material share × (6% fall − 2% deadband)
    quality = {
        **base,
        "incoterm": "DAP",
        "lead_time_days": 35,
        "ppm_commitment": 1000,
        "chargeback_eur_per_complaint": 20,
    }
    assert total_cost(case, quality) == 80_000 + 8 * 100
    # what a term sheet leaves out closes on the supplier's standard terms
    assert total_cost(BRIEF, {**TWO_YEARS, "unit_price": 43.2}, {"payment_days": 30}) == 347_904


def test_cost_category_goal_terms():
    goal = {"category_goal": "stabilise", "fallback_risk_pct": 10, "index_risk_pct": 4}
    case = {**BRIEF, "buyer": {**BRIEF["buyer"], **goal, "index_paths": {"S1": 0.05}}}
    half = {"contract_months": 12, "volume_per_year": 4000, "unit_price": 40.0, "payment_days": 60}
    # uncovered need: 4,000 units at the fallback price, plus 10 % fallback risk on them
    assert total_cost(case, half) == 160_000 + 184_000 * 1.1
    clause = {"series": "S1", "material_share": 0.5, "deadband_pct": 2, "base_month": "2026-09"}
    t = {**TWO_YEARS, "unit_price": 10.0, "payment_days": 60, "index_clause": clause}
    # index path +5 % beyond a 2 % deadband on half the price, plus 4 % index risk on that half
    assert total_cost(case, t) == 80_000 + 80_000 * 0.5 * 0.03 + 80_000 * 0.5 * 0.04
    # a 36-month commitment on a 2-year horizon counts only 2 years
    assert total_cost(case, {**t, "contract_months": 36}) == total_cost(case, t)


def test_cost_relationship_term():
    rel = {"category_goal": "stabilise", "sustainable_margin_pct": 8, "relationship_eur_per_point": 1000}
    case = {**BRIEF, "buyer": {**BRIEF["buyer"], **rel}}
    std = {"payment_days": 30, "incoterm": "DAP", "lead_time_days": 35, "volume_per_year": 4000}
    supplier = {"full_cost": 38.0, "interest_rate": 0.12, "standard": std}
    t = {**TWO_YEARS, "unit_price": 40.0, "payment_days": 30}
    assert supplier_value(case, t, supplier) == 40.0
    margin = (40 - 38) / 40 * 100  # 5 points: 3 below the sustainable 8, inside the soft band
    assert relationship_cost(case, t, std, supplier) == 1000 * (8 - margin) ** 2 / 10 * 2
    assert total_cost(case, t, std, supplier) == round(320_000 + 2_133.33 + 1000 * 0.9 * 2, 2)
    # paying at 90 days costs a cash-short supplier 2 % of the price: a lower margin for it
    assert supplier_value(case, {**t, "payment_days": 90}, supplier) == 40 * (1 - 0.12 * 60 / 360)
    # 10 points below: linear beyond the band; no estimate, no charge; other goals never pay it
    assert relationship_cost(case, {**t, "unit_price": 38.0}, std, supplier) == 1000 * (8 - 2.5) * 2
    assert relationship_cost(case, t, std) == 0
    assert relationship_cost(BRIEF, t, std, supplier) == 0


def test_cost_of_a_sheet_without_a_price():
    assert total_cost(BRIEF, {**TWO_YEARS, "payment_days": 30}) == float("inf")
    with pytest.raises(ValueError):
        cost_breakdown(BRIEF, {**TWO_YEARS, "payment_days": 30})
    no_price = {"from": "sales@A", "status": "open", "term_sheets": [{"term_sheet_id": "offer:A-0", "terms": {}}]}
    priced = {
        "from": "sales@B",
        "status": "open",
        "term_sheets": [{"term_sheet_id": "offer:B-0", "terms": {"unit_price": 44.0}}],
    }
    decision = NaiveAgent().decide(CASE, {"sup:A": [no_price], "sup:B": [priced]})
    assert decision["supplier_id"] == "sup:B"


def test_complete_offer_fills_and_snaps():
    assert complete_offer(CASE, {"unit_price": 41.234, "payment_days": 50, "x": 1}) == {
        "unit_price": 41.23,
        "payment_days": 45,
    }
    assert complete_offer(CASE, {"unit_price": 40}) == {"unit_price": 40, "payment_days": 60}
    strict = {
        **CASE,
        "buyer": {
            **CASE["buyer"],
            "must_haves": [{"field": "payment_days", "op": ">=", "value": 30}],
        },
    }
    assert not must_haves_ok(strict, {"unit_price": 40, "payment_days": 14})


def test_documents_read_and_parse():
    text = read_document("quote.pdf", (FIXTURES / "quote.pdf").read_bytes())
    terms = quote_terms(text)
    assert terms["stated"] == {"unit_price": 43.2, "effective_date": "2027-01-01"}
    assert terms["standard"] == {
        "payment_days": 30,
        "ppm_commitment": 900,
        "chargeback_eur_per_complaint": 0.0,
    }
    assert terms["fixed"]["index_clause"] == {
        "series": "S1",
        "material_share": 0.35,
        "deadband_pct": 3.0,
        "base_month": "2026-09",
    }
    sheet = read_document("cost-breakdown.xlsx", (FIXTURES / "cost-breakdown.xlsx").read_bytes())
    assert "Material | 14.1 | =B2/B6" in sheet and "Unit price | =SUM(B2:B5)" in sheet


def test_client_retries_with_the_same_move_id():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(503) if len(seen) == 1 else httpx.Response(200, json={"api_key": "k"})

    c = ArenaClient("http://arena.test", transport=httpx.MockTransport(handle), rate=1000)
    assert c.register("Team", "CODE")["api_key"] == "k"
    assert len(seen) == 2 and seen[0].headers["X-Move-Id"] == seen[1].headers["X-Move-Id"]


@pytest.fixture
def package(tmp_path):
    db = sqlite3.connect(tmp_path / "world.sqlite")
    db.execute("CREATE TABLE documents (id TEXT, file TEXT)")
    db.execute("INSERT INTO documents VALUES ('doc:L1', 'correspondence/letter.md')")
    db.execute("CREATE TABLE cost_drivers (part_id TEXT, series_id TEXT, weight REAL)")
    db.execute("INSERT INTO cost_drivers VALUES ('part:T1', 'S1', 0.4)")
    db.commit()
    months = {"2025-01": 130.0, "2025-09": 125.0, "2026-03": 120.0, "2026-09": 110.0}
    facts = {f"idx:S1@{m}": {"series": "S1", "month": m, "value": v} for m, v in months.items()}
    facts |= {
        "doc:L1": {"supplier": "sup:A", "date": "2026-09-11", "days": 51},
        "ctr:4600000001": {"supplier": "sup:A", "days": 90},
        "sc:A@2026Q3": {
            "supplier": "sup:A",
            "quarter": "2026Q3",
            "colour": "amber",
            "otd_pct": 85.2,
            "ppm": 712,
        },
        "quote:Q1": {"supplier": "sup:B", "part": "part:T1", "unit_price": 41.9},
        "cost:T1": {"part": "part:T1", "unit_cost": 36.2},
    }
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    (tmp_path / "correspondence").mkdir()
    (tmp_path / "correspondence/letter.md").write_text("We raise prices by 8%.")
    (tmp_path / "cases/practice").mkdir(parents=True)
    (tmp_path / "cases/practice/T01.json").write_text(json.dumps(CASE))
    return DataPackage(tmp_path)


def test_data_package_is_read_only_and_finds_ids(package):
    assert package.lookup("idx:S1@2026-09")["value"] == 110.0
    assert package.ids("idx:S1@") == [f"idx:S1@{m}" for m in ("2025-01", "2025-09", "2026-03", "2026-09")]
    assert package.document("doc:L1") == "We raise prices by 8%."
    assert list(package.cases) == ["case:T01"]
    with pytest.raises(ValueError):
        package.query("DELETE FROM documents")
    with pytest.raises(sqlite3.OperationalError):
        package.query("WITH x AS (SELECT 1) INSERT INTO documents VALUES ('a', 'b')")
    with pytest.raises(FileNotFoundError):
        package.document("../../etc/passwd")


def test_data_package_merges_the_sunday_release(package):
    release = package.root / "release-sunday"
    (release / "data").mkdir(parents=True)
    (release / "correspondence").mkdir()
    (release / "facts.json").write_text(json.dumps({"news:N1": {"supplier": "sup:A", "date": "2026-10-02"}}))
    (release / "data/documents.csv").write_text("id,file\ndoc:M1,correspondence/memo.md\n")
    (release / "correspondence/memo.md").write_text("Market memo.")
    merged = DataPackage(package.root)
    assert merged.lookup("news:N1")["supplier"] == "sup:A" and merged.lookup("idx:S1@2026-09")["value"] == 110.0
    assert merged.document("doc:M1") == "Market memo." and merged.document("doc:L1") == "We raise prices by 8%."


def scripted(*replies):
    calls = []

    def complete(system, user):
        calls.append(user)
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    return complete, calls


def opened():
    threads = Sim({CASE["case_id"]: CASE}).open("team", "case:T01")["threads"]
    threads["sup:A"][0]["attachments"][0] = {
        "name": "quote.pdf",
        "text": read_document("quote.pdf", (FIXTURES / "quote.pdf").read_bytes()),
    }
    return threads


def test_llm_agent_writes_e_mails_and_drops_unknown_ids(package):
    case = {**CASE, "trigger_doc": "doc:L1", "refs": ["sc:A@2026Q3", "cost:T1"]}
    decision = {
        "supplier_id": "sup:A",
        "action": "send",
        "subject": "Your quote",
        "body": "Our should-cost estimate is 36.20 EUR per unit (cost:T1).\n"
        "Scrap fell 30% (idx:S1@2020-01). Could you send a cost breakdown?",
        "term_sheets": [{"unit_price": 39.0}, {"unit_price": 38.0, "payment_days": 14}],
    }
    complete, calls = scripted("```json\n" + json.dumps(decision) + "\n```")
    msg = LLMAgent(complete, package).decide(case, opened())["message"]
    assert msg["term_sheets"] == [
        {"terms": {"unit_price": 39.0, "payment_days": 60}},
        {"terms": {"unit_price": 38.0, "payment_days": 14}},
    ]
    assert msg["body"] == (  # a known ID stays; one the data does not have is removed
        "Our should-cost estimate is 36.20 EUR per unit (cost:T1).\nScrap fell 30%. Could you send a cost breakdown?"
    )
    prompt = calls[0]
    assert "We raise prices by 8%." in prompt and "Payment terms (days net) 30" in prompt
    assert 'cost:T1: {"part": "part:T1", "unit_cost": 36.2}' in prompt  # facts the case cites


def test_llm_agent_reads_standard_terms_before_it_accepts(package):
    # sup:A's sheet leaves payment open; its quote PDF says 30 days - the must-have is >= 30
    threads = opened()
    case = {
        **CASE,
        "buyer": {
            **CASE["buyer"],
            "must_haves": [{"field": "payment_days", "op": ">=", "value": 45}],
        },
    }
    accept = json.dumps({"supplier_id": "sup:A", "action": "accept"})
    out = LLMAgent(scripted(accept)[0], package).decide(case, threads)
    assert "action" not in out["message"]  # refused: the naive counter-offer goes out instead


def test_mcp_server_has_six_read_only_tools(package):
    pytest.importorskip("mcp")
    import asyncio

    from arena_kit.mcp_server import build

    tools = asyncio.run(build(package.root).list_tools())
    assert sorted(t.name for t in tools) == [
        "get_case",
        "list_ids",
        "list_tables",
        "lookup",
        "query",
        "read_document",
    ]


def test_client_stops_on_the_quota_and_reads_what_is_left():
    seen = []

    def handle(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/cases"):
            return httpx.Response(200, json=[], headers={"X-Practice-Remaining": "17"})
        if len(seen) == 2:  # the per-second limit: retried
            return httpx.Response(429, json={"detail": "more than 10 calls per second"}, headers={"Retry-After": "1"})
        return httpx.Response(429, json={"detail": "practice limit: 200 sessions per team in 24 hours"})

    c = ArenaClient("http://arena.test", "key", transport=httpx.MockTransport(handle), rate=1000)
    assert c.cases() == [] and c.practice_remaining == 17
    with pytest.raises(ArenaError) as e:
        c.open_session("case:P01")
    assert e.value.status == 429 and len(seen) == 3  # one retry for the rate limit, none for the quota
    assert quota_used_up(e.value) and not quota_used_up(ArenaError(429, "at most 10 open sessions per key"))


def test_practice_plays_the_hosted_cases():
    cases = [{"case_id": "case:P01", "hosted": True}, {"case_id": "case:P02", "hosted": False}]
    assert [c["case_id"] for c in hosted(cases)] == ["case:P01"]
    assert hosted([{"case_id": "case:P01"}]) == [{"case_id": "case:P01"}]  # no marker: all

    class Used:
        def open(self, case_id):
            raise ArenaError(429, "practice limit: 200 sessions per team in 24 hours")

    out = play_all(Used, ["case:P01", "case:P02", "case:P03"], NaiveAgent, parallel=1)
    assert all(isinstance(e, ArenaError) and e.status == 429 for e in out.values())
    assert "not started" in out["case:P03"].detail
