"""One supervised process owns a batch's account resources; each experiment owns its journal."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from .engine import Engine
from .resources import Resources
from .state import Journal
from .transport import Transport, UncertainRequest


class Batch:
    def __init__(self, journals, *, api_factory=None, model_factory=None, session_limit=10, model_parallel=10):
        if not 1 <= len(journals) <= 5:
            raise ValueError("A batch contains 1..5 independent runs")
        self.journals = journals
        profiles = {j.get("config")["profile"] for j in journals}
        if len(profiles) != 1 or "final" in profiles:
            raise ValueError("Batch runs require one common sim, sandbox or practice profile; final is exclusive")
        if len({j.get("config")["url"] for j in journals}) != 1:
            raise ValueError("A batch coordinates one Arena account and host")
        self.resources = Resources(journals, session_limit, model_parallel)
        self.api_factory, self.model_factory = api_factory, model_factory
        self.engines = {}
        self.stopping = False

    def engine(self, journal):
        cfg = journal.get("config")
        if self.api_factory:
            api = self.api_factory(cfg, journal)
        else:
            urls = json.loads(os.environ.get("HARNESS_BATCH_SIM_URLS", "{}"))
            if cfg["profile"] == "sim" and journal.folder.name not in urls:
                raise ValueError("Missing isolated simulator for batch run")
            api = Transport(
                cfg,
                journal,
                url=urls.get(journal.folder.name),
                key="local-harness" if cfg["profile"] == "sim" else None,
            )
        api.pacer = self.resources.pacer
        models = self.model_factory(cfg, journal) if self.model_factory else None
        if models:
            models.shared = self.resources.models
        engine = Engine(cfg, journal, api=api, models=models, resources=self.resources)
        self.engines[journal.folder.name] = engine
        return engine

    def stop(self):
        self.stopping = True
        for engine in self.engines.values():
            engine.stop.set()

    async def prepare(self):
        # All runs reconcile and select first. No new session is opened before aggregate admission.
        engines = []
        for journal in self.journals:
            if journal.get("status") == "completed":
                continue
            engine = self.engine(journal)
            engines.append(engine)
            await engine.preflight(check_sessions=False)
            engine.selected_cases = await engine.select_cases()
        # A later journal may own a session whose opening response was lost. Reconcile every
        # journal before deciding whether any server session belongs to another coordinator.
        for engine in engines:
            await engine.check_sessions()
        if engines and engines[0].cfg["profile"] == "practice":
            remaining = [e.practice_left for e in engines]
            unopened = sum(
                not c.get("session_id") and not c.get("result") for j in self.journals for c in j.cases().values()
            )
            if any(left is None for left in remaining) or unopened > min(remaining):
                if not any(self.resources.may_be_open(j, cid) for j in self.journals for cid in j.cases()):
                    raise ValueError("Batch selection exceeds remaining shared practice quota")
                # Recovery must still finish already-open sessions when quota was consumed elsewhere.
                for engine in engines:
                    engine.quota_exhausted = True
                    engine.journal.event("quota_blocked", reason="Close owned sessions only; insufficient batch quota")
        return engines

    async def run(self):
        try:
            engines = await self.prepare()
        except BaseException:
            await asyncio.gather(*(e.api.close() for e in self.engines.values()), return_exceptions=True)
            await asyncio.gather(*(e.models.close() for e in self.engines.values()), return_exceptions=True)
            raise

        async def experiment(engine):
            journal = engine.journal
            for attempt in range(4):
                try:
                    if self.stopping:
                        engine.stop.set()
                    journal.put("status", "running")
                    return await engine.run(preflight=attempt > 0)
                except UncertainRequest:
                    journal.event("restart", attempt=attempt + 1, reason="UncertainRequest")
                    if attempt == 3 or self.stopping:
                        journal.put("status", "interrupted")
                        return 2
                    await asyncio.sleep((1, 2, 5)[attempt])
                    from .cli import verify_source

                    verify_source(journal.get("config"))
                    engine = self.engine(journal)
                except Exception as exc:
                    journal.event("engine_error", reason=type(exc).__name__)
                    journal.put("status", "failed")
                    return 2
            return 2

        async def isolated(engine):
            try:
                return await experiment(engine)
            finally:
                await self.resources.finished(engine.journal)

        results = await asyncio.gather(*(isolated(e) for e in engines), return_exceptions=True)
        for engine, result in zip(engines, results, strict=True):
            journal = engine.journal
            if isinstance(result, BaseException):
                journal.event("engine_error", reason=type(result).__name__)
                journal.put("status", "failed")
        return 0 if all(result == 0 for result in results) else 2


def read_manifest(folder):
    manifest = json.loads((Path(folder) / "batch.json").read_text())
    if manifest.get("version") != 1:
        raise ValueError("Unsupported batch manifest")
    runs = manifest.get("runs", [])
    if not 1 <= len(runs) <= 5 or len(set(runs)) != len(runs) or any(Path(r).name != r for r in runs):
        raise ValueError("Invalid batch run IDs")
    if not 1 <= manifest.get("session_limit", 0) <= 10 or not 1 <= manifest.get("model_parallel", 0) <= 10:
        raise ValueError("Invalid shared resource limits")
    return manifest


def main():
    folder = Path(sys.argv[1])
    manifest = read_manifest(folder)
    journals = [Journal(folder.parent / run) for run in manifest["runs"]]

    async def execute():
        batch = Batch(journals, session_limit=manifest["session_limit"], model_parallel=manifest["model_parallel"])
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, batch.stop)
        return await batch.run()

    try:
        code = asyncio.run(execute())
    except Exception as exc:
        for journal in journals:
            journal.event("batch_error", reason=type(exc).__name__)
        code = 2
    finally:
        for journal in journals:
            journal.close()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
