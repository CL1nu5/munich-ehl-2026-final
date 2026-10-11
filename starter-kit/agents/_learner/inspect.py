"""Read-only view of what one harness run learned: `python -m agents._learner.inspect RUN_ID [--json]`.

Shows every lesson of the run (scope, do/avoid, support, which cases taught it) and, per case, the
lessons it had available when it decided. Nothing is written; other runs are never read.
"""

import argparse
import json
import sys
from pathlib import Path

from arena_kit.harness.cli import DEFAULT_RUNS

from .memory import consolidate, episodes


def resolve(run, runs_dir=None):
    path = Path(run)
    if (path / "run.sqlite").is_file():
        return path
    for base in [Path(runs_dir) if runs_dir else None, Path(".runs"), DEFAULT_RUNS]:
        if base and (base / run / "run.sqlite").is_file():
            return base / run
    raise SystemExit(f"No run journal found for {run!r}")


def report(run_dir):
    eps = episodes.extract(run_dir)
    merged = consolidate.merge(eps)
    lessons = sorted(merged.values(), key=lambda x: (-x["support"], x["key"]))
    cases = []
    for ep in eps:
        used = sorted({k for e in ep["log"] for k in e.get("lessons") or []})
        res = ep.get("result") or {}
        cases.append(
            {
                "case_id": ep["case_id"],
                "score": res.get("score"),
                "deal": res.get("deal"),
                "close": ep.get("close"),
                "lessons_available": used,
                "reflection": ep.get("reflection"),
            }
        )
    return {
        "run": run_dir.name,
        "episodes": len(eps),
        "lessons": [
            {
                k: x[k]
                for k in ("key", "polarity", "lever", "text", "support", "contradict", "confidence", "status", "cases")
            }
            for x in lessons
        ],
        "cases": cases,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run", help="run ID under .runs/ or a run directory")
    ap.add_argument("--runs-dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = report(resolve(args.run, args.runs_dir))
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    print(f"Run {out['run']}: {out['episodes']} finished cases\n")
    print("Lessons (support / confidence / status):")
    for x in out["lessons"]:
        print(f"  [{x['polarity']:5}] {x['support']:4.1f} {x['confidence']:.2f} {x['status']:7} {x['key']}")
        print(f"          {x['text']}  <- {', '.join(x['cases'])}")
    print("\nCases:")
    for c in out["cases"]:
        score = "-" if c["score"] is None else f"{c['score']:.3f}"
        print(
            f"  {c['case_id']}: score {score}, close {(c['close'] or {}).get('reason')}, "
            f"{len(c['lessons_available'])} lessons available, reflection {(c['reflection'] or {}).get('source')}"
        )
        for item in (c["reflection"] or {}).get("lessons") or []:
            print(f"      reflection: [{item['polarity']}] {item['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
