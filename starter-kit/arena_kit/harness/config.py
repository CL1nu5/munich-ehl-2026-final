"""Explicit configuration. Environment credentials never select an execution mode."""

import hashlib
import os
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from ..data import DataPackage
from .state import digest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT.parent / "data"
PROFILES = ("sim", "sandbox", "practice", "final")


def tree_hash(path):
    path = Path(path)
    h = hashlib.sha256()
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    for file in files:
        if "__pycache__" in file.parts:
            continue
        h.update(str(file.relative_to(path) if path.is_dir() else file.name).encode())
        h.update(file.read_bytes())
    return h.hexdigest()


def data_hash(root):
    """Freeze every consumed release, including sibling directories and ARENA_RELEASE."""
    data = DataPackage(root)
    try:
        release = data.release
        return digest(
            {
                "base": tree_hash(root),
                "release": str(release) if release else None,
                "release_hash": tree_hash(release) if release else None,
            }
        )
    finally:
        if data.db is not None:
            data.db.close()


def git_state():
    def git(*args):
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()

    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def agent_path(name):
    if name in ("baseline", "starter_naive", "starter_llm"):
        return ROOT / "arena_kit" / "harness" / "plugins.py"
    path = ROOT / "agents" / f"{name}.py"
    if not name.isidentifier() or not path.is_file():
        raise ValueError(f"Unknown agent {name!r}; put Agent in starter-kit/agents/{name}.py")
    return path


def load(args):
    cfg = {}
    if getattr(args, "config", None):
        with open(args.config, "rb") as f:
            cfg = tomllib.load(f)
    allowed = {"agent", "data", "parameters", "model", "parallel", "decision_seconds", "max_cases", "poll_seconds"}
    if set(cfg) - allowed:
        raise ValueError(f"Unknown config keys: {set(cfg) - allowed}")
    cfg = {
        "agent": "baseline",
        "data": str(DEFAULT_DATA),
        "parameters": {},
        "model": {},
        "parallel": 10,
        "decision_seconds": 30,
        "max_cases": 3 if getattr(args, "profile", "sim") == "sim" else 1,
        "poll_seconds": 5,
        **cfg,
    }
    for key in ("agent", "data", "max_cases"):
        if getattr(args, key, None) is not None:
            cfg[key] = getattr(args, key)
    cfg.update(
        profile=getattr(args, "profile", "sim"),
        cases=getattr(args, "case", None) or [],
        allow_arena=bool(getattr(args, "allow_arena", False)),
        allow_model=bool(getattr(args, "allow_model", False)),
    )
    cfg["data"] = str(Path(cfg["data"]).resolve())
    cfg["url"] = "https://arena.tacto.ai"
    if cfg["profile"] != "sim":
        cfg["url"] = os.environ.get("ARENA_URL", cfg["url"]).rstrip("/")
        parsed = urlparse(cfg["url"])
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Arena URL must be HTTPS without credentials, query or fragment")
    if not 1 <= cfg["parallel"] <= 10 or not 0 < cfg["decision_seconds"] <= 45:
        raise ValueError("parallel must be 1..10; decision_seconds must be >0 and <=45")
    if cfg["max_cases"] < 1 or cfg["poll_seconds"] < 1:
        raise ValueError("max_cases and poll_seconds must be positive")
    agent_path(cfg["agent"])
    if not (Path(cfg["data"]) / "facts.json").is_file():
        raise ValueError("data must be the package directory containing facts.json")
    model = cfg["model"]
    if set(model) - {
        "name",
        "base_url",
        "max_calls",
        "max_output_tokens",
        "max_input_bytes",
        "reasoning_effort",
        "json_mode",
        "parallel",
        "timeout",
        "reasoning_effort",
        "input_usd_per_million",
        "output_usd_per_million",
    }:
        raise ValueError("Unknown model keys; keys belong in LLM_API_KEY, never in TOML")
    if model:
        if not model.get("name") or not model.get("base_url"):
            raise ValueError("Model requires name and base_url")
        p = urlparse(model["base_url"])
        local = p.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            p.username
            or p.password
            or p.query
            or p.fragment
            or (p.scheme != "https" and not (local and p.scheme == "http"))
        ):
            raise ValueError("Model URL must be HTTPS, or loopback HTTP, without embedded secrets")
        if "reasoning_effort" in model and model["reasoning_effort"] not in (
            "none",
            "minimal",
            "low",
            "medium",
            "high",
        ):
            raise ValueError("Invalid model reasoning_effort")
        if "json_mode" in model and type(model["json_mode"]) is not bool:
            raise ValueError("json_mode must be boolean")
        model.setdefault("max_input_bytes", 128 * 1024)
        for name in ("max_calls", "max_output_tokens", "max_input_bytes"):
            if type(model.get(name)) is not int or model[name] <= 0:
                raise ValueError(f"Model requires positive {name}")
        if model.get("reasoning_effort", "medium") not in ("none", "minimal", "low", "medium", "high", "xhigh"):
            raise ValueError("Model reasoning_effort must be none, minimal, low, medium, high or xhigh")
        if not 1 <= model.get("parallel", 2) <= 10 or not 0 < model.get("timeout", 20) <= 35:
            raise ValueError("Model parallel must be 1..10 and timeout >0 and <=35")
    if cfg["agent"] == "starter_llm" and not model:
        raise ValueError("starter_llm requires a [model] configuration")
    cfg["source"] = git_state()
    cfg["agent_hash"] = tree_hash(agent_path(cfg["agent"]))
    cfg["code_hash"] = tree_hash(ROOT / "arena_kit")
    cfg["data_hash"] = data_hash(cfg["data"])
    package = DataPackage(cfg["data"])
    cfg["data_release"] = str(package.release) if package.release else None
    if package.db is not None:
        package.db.close()
    cfg["config_hash"] = digest({k: v for k, v in cfg.items() if k not in ("source", "config_hash")})
    return cfg


def authorize(cfg, commit=None):
    if cfg["profile"] != "sim" and not cfg["allow_arena"]:
        raise ValueError("This profile starts real sessions: pass --allow-arena explicitly")
    if cfg["profile"] != "sim" and not os.environ.get("ARENA_API_KEY"):
        raise ValueError("ARENA_API_KEY is required")
    if cfg["model"]:
        p = urlparse(cfg["model"]["base_url"])
        if p.hostname not in ("localhost", "127.0.0.1", "::1") and not cfg["allow_model"]:
            raise ValueError("Remote model requires --allow-model explicitly")
    if cfg["profile"] == "final":
        if not commit or commit != cfg["source"]["commit"] or cfg["source"]["dirty"]:
            raise ValueError("Final requires clean checkout and --commit equal to full HEAD SHA")
        if cfg["cases"]:
            raise ValueError("Final discovers all cases automatically; do not pass --case")
