"""Run memory: what this harness run has learned so far, rebuilt from its journal (never persisted)."""

from pathlib import Path

from . import consolidate, episodes, runlocate

EMPTY = {"episodes": 0, "lessons": [], "knobs": {}, "run": None}


def refresh(case, params, tags_by_supplier, cache):
    """The lesson view for `case`; cached until another case of the run finishes. Never raises."""
    if not params.get("memory", True):
        return dict(EMPTY)
    try:
        run = cache.get("run_dir")
        if run is None or not (Path(run) / "run.sqlite").is_file():
            run = runlocate.find(case, params.get("runs_dir"))
            cache["run_dir"] = str(run) if run else ""
        if not run:
            return dict(EMPTY)
        sig = episodes.signature(run)
        key = (sig, tuple(sorted((s, tuple(t)) for s, t in tags_by_supplier.items())))
        if cache.get("mem_key") == key and cache.get("mem"):
            return cache["mem"]
        eps = episodes.extract(run, exclude=case["case_id"])
        view = consolidate.view(eps, case, tags_by_supplier, k=params.get("max_lessons", 10))
        view["run"] = Path(run).name
        cache["mem_key"], cache["mem"] = key, view
        return view
    except Exception:
        return dict(EMPTY)
