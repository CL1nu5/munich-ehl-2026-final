"""Shared account resources for single runs and batches on one coordinator."""

import asyncio
import json
import time
from collections import deque
from contextlib import asynccontextmanager


class Pacer:
    def __init__(self, rate=9):
        self.rate, self.next, self.lock = rate, 0.0, asyncio.Lock()

    async def wait(self):
        async with self.lock:
            delay = self.next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next = time.monotonic() + 1 / self.rate


class Resources:
    def __init__(self, journals=(), session_limit=10, model_parallel=10):
        if not 1 <= session_limit <= 10 or not 1 <= model_parallel <= 10:
            raise ValueError("Shared resource limits must be between 1 and 10")
        self.journals = list(journals)
        self.pacer = Pacer()
        self.models = asyncio.Semaphore(model_parallel)
        self.leases = {
            (j.folder.name, cid)
            for j in journals
            for cid, case in j.cases().items()
            if not case.get("result") and self.may_be_open(j, cid)
        }
        if len(self.leases) > session_limit:
            raise ValueError("Stored open sessions exceed the shared session limit")
        self.sessions = asyncio.Semaphore(session_limit - len(self.leases))
        self.peak_sessions = len(self.leases)
        self.condition = asyncio.Condition()
        self.waiters = deque()
        self.inactive = set()

    async def finished(self, journal):
        async with self.condition:
            self.inactive.add(journal.folder.name)
            self.condition.notify_all()

    async def acquire(self, key):
        async with self.condition:
            self.waiters.append(key)
            try:
                while self.sessions.locked() or self.waiters[0] != key:
                    if self.sessions.locked() and self.leases and all(r in self.inactive for r, _ in self.leases):
                        raise RuntimeError("Session capacity retained by interrupted runs; resume this batch")
                    await self.condition.wait()
                await self.sessions.acquire()
                self.leases.add(key)
                self.peak_sessions = max(self.peak_sessions, len(self.leases))
            finally:
                self.waiters.remove(key)
                self.condition.notify_all()

    def owned_sessions(self):
        owned = {c["session_id"] for j in self.journals for c in j.cases().values() if c.get("session_id")}
        for journal in self.journals:
            for row in journal.db.execute("SELECT response FROM operations WHERE path='sessions' AND status='done'"):
                response = json.loads(row[0])
                if response.get("session_id"):
                    owned.add(response["session_id"])
        return owned

    @staticmethod
    def may_be_open(journal, cid):
        case = journal.cases().get(cid, {})
        if case.get("result"):
            return False
        if case.get("session_id"):
            return True
        # Includes the crash window after saving an open response but before linking the case.
        return any(
            json.loads(row[0]).get("case_id") == cid
            for row in journal.db.execute(
                "SELECT body FROM operations WHERE path='sessions' AND status IN ('pending','done')"
            )
        )

    @asynccontextmanager
    async def session(self, journal, cid):
        key = (journal.folder.name, cid)
        if key not in self.leases:
            await self.acquire(key)
        try:
            yield
        finally:
            case = journal.cases().get(cid, {})
            if case.get("result") or not self.may_be_open(journal, cid):
                async with self.condition:
                    self.leases.discard(key)
                    self.sessions.release()
                    self.condition.notify_all()
