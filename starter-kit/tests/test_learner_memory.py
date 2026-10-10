"""Offline tests for the learner's run memory: locate, diagnose, consolidate, knobs, robustness."""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents._learner import memory  # noqa: E402
from agents._learner.llm import parse_json  # noqa: E402
from agents._learner.memory import consolidate, runlocate  # noqa: E402
from agents._learner.memory.diagnose import diagnose  # noqa: E402
from agents._learner.prompts import clean_lessons  # noqa: E402
from agents._learner.state import compact, sanitize, size  # noqa: E402
from arena_kit.harness.state import Journal  # noqa: E402
from arena_kit.sim import load_cases  # noqa: E402

CASE = load_cases()["case:S01"]
SUP = "sup:70270"


def episode(case_id, statuses, prices=None, tags=("cash_short",), score=None, claims=None, log=None, reflection=None):
    prices = prices or [10.0 - i * 0.1 for i in range(len(statuses))]
    return {
        "case_id": case_id,
        "type": "price_increase",
        "goal": "leverage",
        "has_alternative": False,
        "suppliers": {SUP: {"tags": list(tags)}},
        "result": {"deal": True, "score": score, "cost_breakdown": {}} if score is not None else None,
        "replies": {
            SUP: [
                {"status": s, "price": p, "claims": (claims or {}).get(i, []), "refs": [f"r{i}"]}
                for i, (s, p) in enumerate(zip(statuses, prices, strict=True))
            ]
        },
        "log": log or [],
        "reflection": reflection or {},
        "close": {},
    }


def test_walk_after_a_pushed_final_offer_disables_pushing():
    ep = episode("case:A", ["open", "final_offer", "walked_away"])
    codes = {x["code"] for x in diagnose(ep)}
    assert "WALKED_AFTER_PUSH" in codes
    view = consolidate.view([ep], CASE, {SUP: ["cash_short"]})
    assert view["knobs"]["allow_final_push"] is False
    # the lesson does not apply to an unrelated supplier kind in another case type
    other = {**CASE, "type": "rfq"}
    assert consolidate.view([ep], other, {"sup:1": ["price_frozen"]})["knobs"]["allow_final_push"] is True


def test_too_soft_and_too_aggressive_move_the_anchor():
    soft = consolidate.view([episode("case:A", ["agreed"])], CASE, {SUP: ["cash_short"]})
    assert soft["knobs"]["drop_mult"][SUP] > 1
    hard = consolidate.view([episode("case:B", ["open", "walked_away"])], CASE, {SUP: ["cash_short"]})
    assert hard["knobs"]["drop_mult"][SUP] < 1


def test_contradicted_claims_block_that_kind():
    log = [{"sup": SUP, "refs": ["r1"], "claims": [{"kind": "idx", "ids": ["r1"]}]}]
    ep = episode("case:A", ["open", "open"], claims={1: ["contradicted"]}, log=log)
    view = consolidate.view([ep], CASE, {SUP: []})
    assert view["knobs"]["blocked_claims"] == ["idx"]
    # generic model advice to cite data does not retire the guard
    advice = [
        episode(
            f"case:{i}",
            ["open"],
            reflection={
                "lessons": clean_lessons(
                    {
                        "lessons": [
                            {
                                "scope_level": "global",
                                "polarity": "do",
                                "lever": "claims",
                                "lesson": f"Cite verified data early {i}.",
                            }
                        ]
                    }
                )
            },
        )
        for i in "BCDE"
    ]
    assert consolidate.view([ep, *advice], CASE, {SUP: []})["knobs"]["blocked_claims"] == ["idx"]


def test_claim_results_are_attributed_by_id_not_by_position():
    claims = [{"kind": "sc", "ids": ["sc:1"]}, {"kind": "idx", "ids": ["idx:a", "idx:b"]}]
    log = [{"sup": SUP, "refs": ["sc:1", "idx:a", "idx:b"], "claims": claims}]
    per_argument = episode("case:A", ["open"], log=log)  # arena: one result per claim, body order
    per_argument["replies"][SUP][0].update(refs=["sc:1", "idx:a", "idx:b"], claims=["contradicted", "verified"])
    assert {x["code"] for x in diagnose(per_argument)} >= {"CONTRADICTED:sc"}
    per_id = episode("case:B", ["open"], log=log)  # simulator: one result per ID, refs order
    per_id["replies"][SUP][0].update(refs=["sc:1", "idx:a", "idx:b"], claims=["verified", "contradicted", "verified"])
    assert "CONTRADICTED:idx" in {x["code"] for x in diagnose(per_id)}
    unknown = episode("case:C", ["open"], log=log)  # counts that match neither: no attribution at all
    unknown["replies"][SUP][0].update(refs=["sc:1", "idx:a", "idx:b"], claims=["contradicted"] * 4)
    assert not [x for x in diagnose(unknown) if x["code"].startswith("CONTRADICTED")]


def test_first_message_claims_are_scored_against_the_opening_price():
    log = [{"sup": SUP, "refs": ["r0"], "claims": [{"kind": "idx", "ids": ["r0"]}]}]
    ep = episode("case:A", ["open"], prices=[9.0], claims={0: ["verified"]}, log=log)
    ep["opening"] = {SUP: 10.0}
    assert "CLAIM_MOVED:idx" in {x["code"] for x in diagnose(ep)}


def test_one_observation_moves_a_knob_once_however_many_tags():
    tags = ("relationship", "weak_costing", "not_strategic", "cash_short")
    soft = consolidate.view([episode("case:A", ["agreed"], tags=tags)], CASE, {SUP: list(tags)})
    assert soft["knobs"]["drop_mult"][SUP] == 1.25
    final = episode("case:A", ["final_offer"], tags=("relationship",))
    final["suppliers"] = {"sup:other": {"tags": ["relationship"]}}
    final["replies"] = {"sup:other": final["replies"][SUP]}
    one = consolidate.view([final], CASE, {SUP: ["relationship"]})
    assert SUP not in one["knobs"]["horizon"]  # one case of another supplier is not enough
    own = consolidate.view([episode("case:B", ["open", "final_offer"])], CASE, {SUP: ["cash_short"]})
    assert own["knobs"]["horizon"][SUP] >= 3


def test_lessons_gain_or_lose_support_from_outcomes():
    taught = episode(
        "case:A",
        ["open"],
        reflection={
            "lessons": clean_lessons(
                {
                    "lessons": [
                        {
                            "scope_level": "archetype",
                            "scope_value": "cash_short",
                            "polarity": "do",
                            "lever": "payment_days",
                            "lesson": "Offer faster payment for a lower price.",
                        }
                    ]
                }
            )
        },
    )
    key = consolidate.view([taught], CASE, {SUP: ["cash_short"]})["lessons"][0]["key"]
    used_well = episode("case:B", ["open"], score=0.9, log=[{"lessons": [key]}])
    used_badly = [
        episode(f"case:{i}", ["open"], score=0.1, log=[{"lessons": [key]}], reflection={"feedback": {key: "failed"}})
        for i in "CDE"
    ]
    merged = consolidate.merge([taught, used_well])
    assert merged[key]["support"] == 1.5 and merged[key]["status"] == "active"
    merged = consolidate.merge([taught, *used_badly])
    assert merged[key]["status"] == "retired"
    assert not consolidate.retrieve(merged, CASE, {SUP: ["cash_short"]})


def test_retrieval_prefers_specific_matching_scopes():
    eps = [episode("case:A", ["agreed"], tags=("cash_short",))]
    lessons = consolidate.view(eps, CASE, {SUP: ["cash_short"]})["lessons"]
    assert lessons and lessons[0]["scope"]["level"] == "supplier"
    assert {x["scope"]["level"] for x in lessons} <= {"supplier", "archetype", "case_type", "goal", "global"}
    assert not consolidate.view(eps, CASE, {"sup:9": ["full_order_book"]})["lessons"]


def test_run_lookup_matches_only_the_live_run_with_this_case(tmp_path):
    def make(name, status, case=CASE, age=0):
        j = Journal(tmp_path / name)
        j.case(case["case_id"], case=case, case_hash=runlocate.stable_hash(case))
        if status:
            j.put("status", status)
        j.close()
        if age:
            for f in (tmp_path / name).glob("run.sqlite*"):
                os.utime(f, (time.time() - age, time.time() - age))

    make("old-running", "running", age=3600)  # stale: never ours
    make("done", "completed")
    make("other-case", "running", case={**CASE, "title": "changed"})
    assert runlocate.find(CASE, tmp_path) is None
    make("live", "running")
    assert runlocate.find(CASE, tmp_path).name == "live"
    # the case definition the final adds while open does not change the match
    assert runlocate.find({**CASE, "open": True, "seconds_left": 300}, tmp_path).name == "live"


def test_memory_refresh_never_raises_on_a_broken_journal(tmp_path):
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "run.sqlite").write_text("not a database")
    view = memory.refresh(CASE, {"memory": True, "runs_dir": str(tmp_path)}, {SUP: []}, {})
    assert view["episodes"] == 0 and view["lessons"] == []
    assert memory.refresh(CASE, {"memory": False}, {SUP: []}, {})["episodes"] == 0


def test_state_is_sanitized_and_bounded():
    state = {
        "x": float("inf"),
        "y": [float("nan"), 1.0],
        "log": [{"note": "a" * 2000} for _ in range(100)],
        "sup": {SUP: {"read": "r" * 100}},
    }
    out = compact(state, limit=20_000)
    json.dumps(out, allow_nan=False)
    assert out["x"] is None and out["y"] == [None, 1.0] and size(out) <= 20_000
    assert sanitize({"a": (1, 2)}) == {"a": [1, 2]}


def test_parse_json_handles_fences_prose_and_garbage():
    assert parse_json('Sure:\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert parse_json('noise {"a": 1} tail {"b": 2}') == {"a": 1}
    assert parse_json("{broken") is None and parse_json(None) is None and parse_json("[1, 2]") is None


def test_unrelated_model_lessons_do_not_retire_each_other():
    def refl(case_id, polarity, text):
        return episode(
            case_id,
            ["open"],
            reflection={
                "lessons": clean_lessons(
                    {"lessons": [{"scope_level": "global", "polarity": polarity, "lever": "price", "lesson": text}]}
                )
            },
        )

    eps = [
        refl("case:A", "do", "Offer longer payment terms in exchange for price."),
        refl("case:B", "avoid", "Avoid conceding before the supplier moves."),
        refl("case:C", "avoid", "Avoid splitting the difference early."),
    ]
    merged = consolidate.merge(eps)
    assert all(x["status"] == "active" for x in merged.values())
