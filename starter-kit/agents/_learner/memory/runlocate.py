"""Find the harness run this case belongs to, and open its journal read-only.

The engine journals every case (with a hash of its definition) before the agent prepares it, so the
newest unfinished run that holds this case with the same hash is ours. Nothing here ever writes.
"""

import json
import sqlite3
import time
from pathlib import Path

from arena_kit.harness.state import digest

ROOT = Path(__file__).resolve().parents[3]  # starter-kit/
DEFAULT_RUNS = ROOT.parent / ".runs"
MAX_SCAN = 30
FRESH_SECONDS = 15 * 60  # a live run writes its journal at least every poll
LIVE = (None, "created", "running")


def stable_hash(case):
    return digest({k: v for k, v in case.items() if k not in ("open", "seconds_left")})


def connect(run_dir):
    con = sqlite3.connect(f"file:{Path(run_dir) / 'run.sqlite'}?mode=ro", uri=True, timeout=1.0)
    con.execute("PRAGMA query_only = ON")
    return con


def _mtime(path):
    times = [p.stat().st_mtime for p in (path / "run.sqlite", path / "run.sqlite-wal") if p.exists()]
    return max(times, default=0.0)


def find(case, runs_dir=None):
    """The run directory of this case's harness run, or None."""
    base = Path(runs_dir) if runs_dir else DEFAULT_RUNS
    try:
        dirs = [p for p in base.iterdir() if (p / "run.sqlite").is_file()]
    except OSError:
        return None
    want = stable_hash(case)
    now = time.time()
    for run in sorted(dirs, key=_mtime, reverse=True)[:MAX_SCAN]:
        if now - _mtime(run) > FRESH_SECONDS:
            break  # older runs are never ours: memory must not leak across runs
        try:
            con = connect(run)
            try:
                row = con.execute("SELECT value FROM cases WHERE id=?", (case["case_id"],)).fetchone()
                status = con.execute("SELECT value FROM meta WHERE key='status'").fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if not row or json.loads(row[0]).get("case_hash") != want:
            continue
        if (json.loads(status[0]) if status else None) not in LIVE:
            continue
        return run
    return None
