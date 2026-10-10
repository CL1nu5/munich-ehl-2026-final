"""Agent parameters from the TOML `[parameters]` table, with safe defaults."""

DEFAULTS = {
    "llm": True,  # use the model when the budget allows; False = fully deterministic
    "two_call": True,  # allow strategist + writer when there is time for two calls
    "decision_seconds": 30,  # direct-call default; harness runtime is authoritative
    "model_timeout": 14,  # direct-call default; harness runtime is authoritative
    "data_dir": None,  # default: the repo's data/
    "memory": True,  # learn from finished cases of this run
    "max_lessons": 10,
    "anchor_drop": 0.18,  # first ask: this far below the standing price (scaled by the supplier's situation)
}


def load(parameters):
    p = dict(DEFAULTS)
    for key, value in (parameters or {}).items():
        if key in DEFAULTS:
            p[key] = value
    p["decision_seconds"] = max(1.0, min(45.0, float(p["decision_seconds"])))
    p["model_timeout"] = max(1.0, min(35.0, float(p["model_timeout"])))
    p["anchor_drop"] = max(0.0, min(0.4, float(p["anchor_drop"])))
    return p
