"""Offline tests for the learner's run memory: locate, diagnose, consolidate, knobs, robustness."""

import json
import sys
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


def episode(case_id, statuses, prices=None, tags=("cash_short",), score=0.5, claims=None, log=None, reflection=None):
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


def test_final_offer_learning_needs_repeated_confirmed_cases():
    one = episode("case:A", ["open", "final_offer", "walked_away"])
    assert consolidate.view([one], CASE, {SUP: ["cash_short"]})["knobs"]["allow_final_push"] is True
    eps = [episode(f"case:{i}", ["open", "final_offer", "walked_away"]) for i in range(3)]
    assert consolidate.view(eps, CASE, {SUP: ["cash_short"]})["knobs"]["allow_final_push"] is False
    assert (
        consolidate.view(eps, {**CASE, "type": "rfq"}, {"sup:other": ["cash_short"]})["knobs"]["allow_final_push"]
        is True
    )


def test_anchors_are_bounded_and_require_independent_cases():
    soft = [episode(f"case:{i}", ["agreed"]) for i in range(3)]
    assert consolidate.view(soft[:1] * 10, CASE, {SUP: ["cash_short"]})["knobs"]["drop_mult"][SUP] == 1
    assert consolidate.view(soft, CASE, {SUP: ["cash_short"]})["knobs"]["drop_mult"][SUP] == 1.1
    hard = [episode(f"case:w{i}", ["open", "walked_away"]) for i in range(3)]
    assert consolidate.view(hard, CASE, {SUP: ["cash_short"]})["knobs"]["drop_mult"][SUP] == 0.9
    assert consolidate.view(soft + hard, CASE, {SUP: ["cash_short"]})["knobs"]["drop_mult"][SUP] == 1


def test_contradicted_claim_does_not_block_a_whole_category():
    log = [{"sup": SUP, "refs": ["r1"], "claims": [{"kind": "idx", "ids": ["r1"]}]}]
    ep = episode("case:A", ["open", "open"], claims={1: ["contradicted"]}, log=log)
    view = consolidate.view([ep], CASE, {SUP: []})
    assert view["knobs"]["blocked_claims"] == []


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


def test_one_observation_never_moves_knobs_even_with_many_tags():
    tags = ("relationship", "weak_costing", "not_strategic", "cash_short")
    soft = consolidate.view([episode("case:A", ["agreed"], tags=tags)], CASE, {SUP: list(tags)})
    assert soft["knobs"]["drop_mult"][SUP] == 1
    assert soft["hypotheses"] > 0 and not soft["lessons"]
    own = consolidate.view([episode("case:B", ["open", "final_offer"])], CASE, {SUP: ["cash_short"]})
    assert SUP not in own["knobs"]["horizon"]


def test_scores_and_model_verdicts_do_not_create_causal_support():
    taught = episode("case:A", ["agreed"])
    merged = consolidate.merge([taught])
    key = next(iter(merged))
    used = [
        episode(
            f"case:{i}",
            ["open"],
            score=score,
            log=[{"lessons": [key]}],
            reflection={"feedback": {key: "held" if score > 0.5 else "failed"}},
        )
        for i, score in enumerate([0.9, 0.8, 0.1, 0.2])
    ]
    item = consolidate.merge([taught, *used])[key]
    assert item["support"] == 1 and item["contradict"] == 0 and item["status"] == "hypothesis"
    assert 0 < item["confidence"] < 1
    taught["result"] = None
    assert not consolidate.merge([taught])


def test_retrieval_prefers_specific_matching_scopes():
    eps = [episode(f"case:{i}", ["agreed"], tags=("cash_short",)) for i in range(3)]
    lessons = consolidate.view(eps, CASE, {SUP: ["cash_short"]})["lessons"]
    assert lessons and lessons[0]["scope"]["level"] == "supplier"
    assert not consolidate.view(eps, CASE, {"sup:9": ["cash_short"]})["lessons"]
    # A distinct supplier in a distinct completed case permits archetype transfer.
    other = episode("case:other", ["agreed"])
    other["suppliers"] = {"sup:other": {"tags": ["cash_short"]}}
    other["replies"] = {"sup:other": other["replies"][SUP]}
    assert consolidate.view(eps + [other], CASE, {"sup:new": ["cash_short"]})["lessons"]


def test_identical_live_cases_do_not_enable_implicit_history(tmp_path):
    for name in ("first", "newer"):
        journal = Journal(tmp_path / name)
        journal.case(CASE["case_id"], case=CASE, case_hash=runlocate.stable_hash(CASE), result={"score": 1})
        journal.put("status", "running")
        journal.close()
    assert memory.refresh(CASE, {"runs_dir": str(tmp_path)}, {SUP: []}, {})["episodes"] == 0


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
    assert all(x["status"] == "hypothesis" for x in merged.values())
