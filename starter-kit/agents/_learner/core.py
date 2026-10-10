"""prepare() and decide() of the learner agent: per-process caches, the graph run, state hygiene."""

import time

from arena_kit.data import DataPackage
from arena_kit.harness.config import DEFAULT_DATA

from . import dossier as dossier_mod
from . import graph, memory, params
from .budget import Budget
from .llm import Gateway
from .state import STATE_VERSION, compact

_PACKAGES = {}


def package(p):
    root = str(p.get("data_dir") or DEFAULT_DATA)
    if root not in _PACKAGES:
        _PACKAGES[root] = DataPackage(root)
    return _PACKAGES[root]


def case_cache(case, p, cache, pkg=None):
    """Everything per case that does not change between decisions; rebuilt after a worker restart, or
    when the harness's own data package (services.data, from --data) differs from the one we used."""
    pkg = pkg or package(p)
    cc = cache.get(case["case_id"])
    if cc is None or cc.get("root") != str(pkg.root):
        cc = {"root": str(pkg.root), "facts": pkg.facts, "dossier": dossier_mod.build(case, pkg)}
        cache[case["case_id"]] = cc
    return cc


def harness_package(p, services):
    """The data package the harness runs with, unless `data_dir` pins one explicitly."""
    data = getattr(services, "data", None) if services is not None else None
    if p.get("data_dir") or not isinstance(data, DataPackage):
        return None
    return data


def initial_state(case, p, cc):
    return {
        "v": STATE_VERSION,
        "case_id": case["case_id"],
        "p": p,
        "sup": {sid: {"tags": d.get("tags", [])} for sid, d in cc["dossier"]["suppliers"].items()},
        "llm": {"fails": 0, "calls": 0},
        "log": [],
    }


def prepare(context, cache):
    case = context["case"]
    p = params.load(context.get("parameters"))
    started = time.monotonic()
    cc = case_cache(case, p, cache)
    state = initial_state(case, p, cc)
    mem = memory.refresh(case, p, {s: v["tags"] for s, v in state["sup"].items()}, cc)
    state["mem"] = {
        "run": mem.get("run"),
        "episodes": mem.get("episodes", 0),
        "lessons": [x["key"] for x in mem.get("lessons", [])],
    }
    state["prepare_s"] = round(time.monotonic() - started, 3)
    return compact(state)


def decide(observation, state, services, cache):
    case = observation["case"]
    if not state or state.get("v") != STATE_VERSION or state.get("case_id") != case["case_id"]:
        p = params.load((state or {}).get("p"))
        cc = case_cache(case, p, cache)
        state = initial_state(case, p, cc)
    p = state.get("p") or params.load({})
    cc = case_cache(case, p, cache, harness_package(p, services))
    for sid, d in cc["dossier"]["suppliers"].items():  # tags from the package actually in use
        state["sup"].setdefault(sid, {})["tags"] = d.get("tags", [])
    budget = Budget(observation, p)
    gw = Gateway(services)
    ctx = {
        "obs": observation,
        "case": case,
        "threads": observation["threads"],
        "ready": list(observation.get("ready_suppliers") or []),
        "state": state,
        "services": services,
        "p": p,
        "cc": cc,
        "budget": budget,
        "gw": gw,
    }
    out, runner = graph.run(ctx)
    state = out["state"]
    state["llm"]["calls"] = state["llm"].get("calls", 0) + gw.calls
    state["runner"] = runner
    return {"actions": out["actions"], "state": compact(state)}
