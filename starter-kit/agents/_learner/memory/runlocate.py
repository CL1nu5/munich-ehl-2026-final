"""Explicit read-only journal access for inspection and the owning engine's history service."""

import sqlite3
from pathlib import Path

from arena_kit.harness.state import digest


def stable_hash(case):
    return digest({k: v for k, v in case.items() if k not in ("open", "seconds_left")})


def connect(run_dir):
    con = sqlite3.connect(f"file:{Path(run_dir) / 'run.sqlite'}?mode=ro", uri=True, timeout=1.0)
    con.execute("PRAGMA query_only = ON")
    return con
