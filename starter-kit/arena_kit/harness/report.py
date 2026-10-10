"""Results from our sessions only; no scoreboard access."""

import json
from collections import Counter


def report(journal):
    cfg = journal.get("config")
    cases = journal.cases()
    rows = []
    for cid, case in cases.items():
        result = case.get("result") or {}
        rows.append(
            {
                "case_id": cid,
                "case_hash": case.get("case_hash"),
                "wave": case.get("case", {}).get("wave"),
                "session_id": case.get("session_id"),
                "error": case.get("error"),
                "end_reason": case.get("end_reason"),
                "rounds": case.get("rounds"),
                **result,
            }
        )
    complete = [r for r in rows if isinstance(r.get("score"), int | float)]
    events = [
        dict(r) | {"value": json.loads(r["value"])} for r in journal.db.execute("SELECT * FROM events ORDER BY n")
    ]
    calls = [dict(r) for r in journal.db.execute("SELECT * FROM model_calls")]
    thread_timeouts = {}
    for event in events:
        if event["kind"] == "thread_state":
            value = event["value"]
            key = (value["session"], value["supplier"])
            thread_timeouts[key] = max(thread_timeouts.get(key, 0), value["timeouts"])
    tokens = Counter()
    for call in calls:
        tokens.update({k: v for k, v in json.loads(call["usage"] or "{}").items() if isinstance(v, int)})

    def latency(kind):
        values = sorted(e["value"]["elapsed"] for e in events if e["kind"] == kind and "elapsed" in e["value"])
        return {
            "count": len(values),
            "mean": sum(values) / len(values) if values else None,
            "p95": values[min(len(values) - 1, int(0.95 * len(values)))] if values else None,
        }

    score = sum(r["score"] for r in complete) / len(complete) if complete else None
    unknown_calls = sum(c["status"] != "done" or not json.loads(c["usage"] or "{}") for c in calls)
    model = cfg["model"]
    estimate = None
    if all(k in model for k in ("input_usd_per_million", "output_usd_per_million")):
        estimate = (
            tokens["prompt_tokens"] * model["input_usd_per_million"]
            + tokens["completion_tokens"] * model["output_usd_per_million"]
        ) / 1e6
    wave_points = {}
    if cfg["profile"] == "final":
        for wave in sorted({r["wave"] for r in rows if r.get("wave") is not None}):
            group = [r for r in rows if r["wave"] == wave]
            wave_points[wave] = (
                round(100 * sum(r["score"] for r in group) / len(group), 1)
                if all(isinstance(r.get("score"), int | float) for r in group)
                else None
            )
    finished = journal.get("status") == "completed" and (cfg["profile"] != "final" or len(wave_points) == 5)
    return {
        "run_id": journal.folder.name,
        "status": journal.get("status", "running"),
        "profile": cfg["profile"],
        "agent": cfg["agent"],
        "parameters": cfg["parameters"],
        "model": model.get("name"),
        "source": cfg["source"],
        "config_hash": cfg["config_hash"],
        "data_hash": cfg["data_hash"],
        "agent_hash": cfg["agent_hash"],
        "code_hash": cfg["code_hash"],
        "selected": len(rows),
        "scored": len(complete),
        "coverage": len(complete) / len(rows) if rows else 0,
        "mean_score_completed": score,
        "complete_score": score if finished and rows and len(complete) == len(rows) else None,
        "wave_points": wave_points,
        "final_points": sum(wave_points.values())
        if finished and len(wave_points) == 5 and all(v is not None for v in wave_points.values())
        else None,
        "deals": sum(bool(r.get("deal")) for r in rows),
        "fallbacks": sum(e["kind"] == "fallback" for e in events),
        "timeouts": sum(e["kind"] == "fallback" and e["value"].get("reason") == "TimeoutError" for e in events),
        "arena_timeouts_observed": sum(thread_timeouts.values()),
        "retries": sum(e["kind"] == "api" and e["value"].get("retry", False) for e in events),
        "claims": dict(
            Counter(c["result"] for e in events if e["kind"] == "reply" for c in e["value"].get("claims", []))
        ),
        "decision_seconds": latency("decision"),
        "api_seconds": latency("api"),
        "model_calls": len(calls),
        "usage_tokens": dict(tokens),
        "unknown_usage_calls": unknown_calls,
        "estimated_usd_known_usage": estimate,
        "errors": [e["value"]["reason"] for e in events if e["kind"] == "engine_error"],
        "events": events,
        "cases": rows,
    }


def compare(reports, allow_mismatch=False):
    if len(reports) < 2:
        raise ValueError("Compare needs at least two runs")
    if len({r["profile"] for r in reports}) != 1:
        raise ValueError("Different execution profiles cannot be compared")
    common = set.intersection(*[{c["case_id"] for c in r["cases"]} for r in reports])
    indexes = [{c["case_id"]: c for c in r["cases"]} for r in reports]
    mismatch = len({r["data_hash"] for r in reports}) > 1 or any(
        len({c["case_id"] for c in r["cases"]}) != len(common) for r in reports
    )
    mismatch |= any(len({i[cid]["case_hash"] for i in indexes}) > 1 for cid in common)
    if mismatch and not allow_mismatch:
        raise ValueError("Case/data sets differ; --allow-mismatch produces a restricted comparison")
    rows = []
    for cid in sorted(common):
        scores = [i[cid].get("score") for i in indexes]
        rows.append(
            {
                "case_id": cid,
                "scores": scores,
                "deltas_from_first": [
                    s - scores[0] if s is not None and scores[0] is not None else None for s in scores
                ],
            }
        )
    return {
        "runs": [r["run_id"] for r in reports],
        "restricted": bool(mismatch),
        "cases": rows,
        "mean_paired_scores": [
            sum(row["scores"][n] for row in rows) / len(rows)
            if rows and all(row["scores"][n] is not None for row in rows)
            else None
            for n in range(len(reports))
        ],
    }
