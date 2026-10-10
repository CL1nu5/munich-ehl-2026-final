"""Agent parameters from the TOML `[parameters]` table, with safe defaults."""

DEFAULTS = {
    "llm": True,  # use the model when the budget allows; False = fully deterministic
    "two_call": True,  # allow strategist + writer when there is time for two calls
    "decision_seconds": 30,  # mirror of the TOML top-level decision_seconds
    "model_timeout": 14,  # mirror of [model].timeout
    "data_dir": None,  # default: the repo's data/
    "runs_dir": None,  # default: the repo's .runs/
    "memory": True,  # learn from finished cases of this run
    "max_lessons": 10,
    "anchor_drop": 0.10,  # first ask: this far below the standing value (before priors and lessons)
    "round_budget": 6,  # rounds we plan to use per thread at most
}


def load(parameters):
    p = dict(DEFAULTS)
    for key, value in (parameters or {}).items():
        if key in DEFAULTS:
            p[key] = value
    p["decision_seconds"] = max(1.0, min(45.0, float(p["decision_seconds"])))
    p["model_timeout"] = max(1.0, min(35.0, float(p["model_timeout"])))
    p["round_budget"] = max(2, int(p["round_budget"]))
    p["anchor_drop"] = max(0.0, min(0.4, float(p["anchor_drop"])))
    return p
