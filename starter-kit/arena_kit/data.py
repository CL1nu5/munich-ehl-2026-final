"""Read-only access to the data package: SQL, lookup by citable ID, documents, practice cases -
plus the Sunday release (new news, letters and a memo) once you have downloaded it."""

import csv
import json
import os
import sqlite3
from functools import cached_property
from pathlib import Path

MAX_ROWS = 200
RELEASE = "release-sunday"


class DataPackage:
    def __init__(self, root, release=None):
        """`root`: the unpacked package. `release`: the unpacked Sunday release (default: a
        `release-sunday` folder inside or next to the package, or ARENA_RELEASE), if present."""
        self.root = Path(root).expanduser().resolve()
        # facts.json holds every citable fact with the fields the arena checks claims against.
        own = self.root / "facts.json"
        facts = [own] if own.is_file() else [p for p in sorted(self.root.rglob("facts.json")) if RELEASE not in p.parts]
        self.facts = json.loads(facts[0].read_text(encoding="utf-8")) if facts else {}
        release = release or os.environ.get("ARENA_RELEASE")
        candidates = [Path(release).expanduser()] if release else [self.root / RELEASE, self.root.parent / RELEASE]
        self.release = next((p.resolve() for p in candidates if (p / "facts.json").is_file()), None)
        if self.release:  # new IDs only: nothing in the package changes
            self.facts |= json.loads((self.release / "facts.json").read_text(encoding="utf-8"))
        dbs = sorted(self.root.rglob("*.sqlite")) + sorted(self.root.rglob("*.db"))
        if not dbs and not facts:
            raise FileNotFoundError(f"no facts.json or .sqlite under {self.root}")
        self.db = None
        if dbs:
            self.db = sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only = ON")

    def tables(self):
        """{table: {"columns": [...], "rows": n}}"""
        if self.db is None:
            return {}
        names = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {
            t: {
                "columns": [f"{c[1]} {c[2]}".strip() for c in self.db.execute(f'PRAGMA table_info("{t}")')],
                "rows": self.db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0],
            }
            for t in names
        }

    def query(self, sql, params=(), limit=MAX_ROWS):
        if self.db is None:
            raise ValueError("this package has no SQLite file")
        if sql.lstrip().split(None, 1)[0].lower() not in {"select", "with"}:
            raise ValueError("read-only: SELECT or WITH statements only")
        cur = self.db.execute(sql, params)
        return [dict(r) for r in cur.fetchmany(limit)]

    @cached_property
    def _ids(self):
        # A table's citable ID sits in its `id` column, else in its first column.
        index = {}
        for t, meta in self.tables().items():
            cols = [c.split()[0] for c in meta["columns"]]
            key = "id" if "id" in cols else cols[0]
            for row in self.db.execute(f'SELECT * FROM "{t}"'):
                value = row[key]
                if isinstance(value, str) and ":" in value:
                    index[value] = {"table": t, **dict(row)}
        for path in sorted(self.release.glob("data/*.csv")) if self.release else []:  # not in the SQLite
            with path.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    index[row["id"]] = {"table": path.stem, **row, "root": str(self.release)}
        return index

    def lookup(self, ref):
        """The fact behind a citable ID (from facts.json, else its table row), or None."""
        return self.facts.get(ref) or self._ids.get(ref)

    def ids(self, prefix):
        """All citable IDs starting with `prefix`, sorted, e.g. ids("idx:GP19-245113@")."""
        return sorted(i for i in self.facts.keys() | self._ids.keys() if i.startswith(prefix))

    def document(self, ref):
        """A document's text by ID (row with a path or body column) or by path in the package."""
        row, base = self._ids.get(ref) or self.facts.get(ref), self.root
        if row:
            for col in ("body", "text", "content"):
                if row.get(col):
                    return row[col]
            base = Path(row.get("root") or self.root)
            ref = next((row[c] for c in ("path", "file") if row.get(c)), None)
            if ref is None:
                return json.dumps(row, ensure_ascii=False)
        path = (base / ref).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise FileNotFoundError(ref)
        return path.read_text(encoding="utf-8")

    @cached_property
    def cases(self):
        """Practice (and demo) case files in the package: {case_id: case}."""
        out = {}
        for p in sorted(self.root.rglob("cases/**/*.json")):
            case = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(case, dict) and "case_id" in case:
                out[case["case_id"]] = case
        return out
