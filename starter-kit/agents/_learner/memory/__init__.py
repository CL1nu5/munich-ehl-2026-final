"""Run memory obtained from the owning engine, never discovered by scanning other runs."""

from . import consolidate

EMPTY = {"episodes": 0, "lessons": [], "knobs": {}, "run": None}


def refresh(case, params, tags_by_supplier, cache, services=None):
    if not params.get("memory", True) or services is None or not hasattr(services, "history"):
        return dict(EMPTY)
    try:
        history = services.history()
        runtime = getattr(services, "runtime", {})
        if not runtime.get("run_id") or history.get("run_id") != runtime["run_id"]:
            return dict(EMPTY)
        episodes = [
            ep for ep in history.get("episodes", []) if ep.get("case_id") != case["case_id"] and ep.get("result")
        ]
        view = consolidate.view(episodes, case, tags_by_supplier, k=params.get("max_lessons", 10))
        view["run"] = history["run_id"]
        return view
    except Exception:
        return dict(EMPTY)
