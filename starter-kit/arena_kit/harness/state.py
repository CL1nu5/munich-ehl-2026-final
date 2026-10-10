"""Durable run journal and a per-user, cross-checkout advisory lock."""

import fcntl
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def redact(value):
    text = encode(value)
    for key, secret in os.environ.items():
        if secret and len(secret) >= 6 and any(x in key.upper() for x in ("KEY", "TOKEN", "PASSWORD", "SECRET")):
            text = text.replace(secret, "[REDACTED]")
    return json.loads(text)


class RunLock:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "arena-harness"
        self.file = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = (self.root / "run.lock").open("a+")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            self.file.close()
            raise RuntimeError("Another harness run owns the local lock; no requests were made.") from e
        self.file.seek(0)
        self.file.truncate()
        self.file.write(str(os.getpid()))
        self.file.flush()
        return self

    def __exit__(self, *args):
        self.file.close()  # never unlink: another process could otherwise lock a different inode


class Journal:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.folder / "run.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations (
              id TEXT PRIMARY KEY, path TEXT NOT NULL, body TEXT NOT NULL,
              status TEXT NOT NULL, response TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (n INTEGER PRIMARY KEY, at REAL, kind TEXT, value TEXT);
            CREATE TABLE IF NOT EXISTS model_calls (
              id TEXT PRIMARY KEY, status TEXT NOT NULL, usage TEXT, created REAL NOT NULL);
        """)

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, encode(value)))

    def cases(self):
        return {r["id"]: json.loads(r["value"]) for r in self.db.execute("SELECT * FROM cases ORDER BY id")}

    def case(self, case_id, **updates):
        value = self.cases().get(case_id, {}) | updates
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO cases VALUES (?,?)", (case_id, encode(value)))
        return value

    def event(self, kind, **value):
        with self.db:
            self.db.execute(
                "INSERT INTO events(at,kind,value) VALUES (?,?,?)", (time.time(), kind, encode(redact(value)))
            )

    def operation(self, path, body, operation_id=None):
        operation_id = operation_id or uuid.uuid4().hex
        payload = encode(body)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO operations VALUES (?,?,?,?,?,?)",
                (operation_id, path, payload, "pending", None, time.time()),
            )
        row = self.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        if row["path"] != path or row["body"] != payload:
            raise ValueError("Operation ID reused for different request")
        return dict(row)

    def finish(self, operation_id, response, status="done"):
        with self.db:
            self.db.execute(
                "UPDATE operations SET status=?,response=? WHERE id=?", (status, encode(redact(response)), operation_id)
            )

    def pending(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM operations WHERE status='pending' ORDER BY created")]

    def reserve_model(self, limit):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            count = self.db.execute("SELECT count(*) FROM model_calls").fetchone()[0]
            if count >= limit:
                raise RuntimeError("Model call budget exhausted")
            call_id = uuid.uuid4().hex
            self.db.execute("INSERT INTO model_calls VALUES (?,?,?,?)", (call_id, "reserved", None, time.time()))
        return call_id

    def model_done(self, call_id, usage, status="done"):
        with self.db:
            self.db.execute("UPDATE model_calls SET status=?,usage=? WHERE id=?", (status, encode(usage), call_id))
