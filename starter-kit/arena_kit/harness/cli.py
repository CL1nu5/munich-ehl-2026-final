"""CLI and supervisor. No prompts after launch, including automatic recovery."""

import argparse
import asyncio
import contextlib
import csv
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from ..data import DataPackage
from .agent_process import AgentProcess
from .config import PROFILES, ROOT, agent_path, authorize, data_hash, git_state, load, tree_hash
from .plugins import BUILTINS
from .report import compare, report
from .simulator import server
from .state import Journal, RunLock, encode, redact
from .transport import Models, Transport
from .validation import validate

DEFAULT_RUNS = ROOT.parent / ".runs"


def verify_source(cfg):
    current = git_state()
    if (
        current["commit"] != cfg["source"]["commit"]
        or tree_hash(ROOT / "arena_kit") != cfg.get("code_hash")
        or data_hash(cfg["data"]) != cfg["data_hash"]
        or tree_hash(agent_path(cfg["agent"])) != cfg["agent_hash"]
    ):
        raise ValueError("Source, agent or data changed; cannot resume the same experiment")
    if cfg["profile"] == "final" and current["dirty"]:
        raise ValueError("Final resume requires clean checkout")
    return current


def resolve_run(root, name):
    path = Path(name)
    if not path.is_dir():
        path = Path(root) / name
    if not (path / "run.sqlite").is_file():
        raise ValueError(f"No run journal at {path}")
    return path.resolve()


def supervise(cfg, journal, run_lock):
    environment = os.environ.copy()
    simulator = (
        server(journal.folder, DataPackage(cfg["data"]).facts)
        if cfg["profile"] == "sim"
        else contextlib.nullcontext(None)
    )
    with simulator as url:
        if url:
            environment["HARNESS_SIM_URL"] = url
        else:
            environment.pop("HARNESS_SIM_URL", None)
        child = None
        interrupted = False

        def stop(signum, frame):
            nonlocal interrupted
            if child and child.poll() is None:
                if interrupted:
                    child.kill()
                else:
                    child.send_signal(signal.SIGTERM)
            interrupted = True

        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            for attempt in range(4):
                # Automatic recovery has the same source freeze as explicit resume.
                verify_source(cfg)
                journal.put("status", "running")
                child = subprocess.Popen(
                    [sys.executable, "-m", "arena_kit.harness.engine", str(journal.folder)],
                    cwd=ROOT,
                    env=environment,
                    start_new_session=True,
                    pass_fds=(run_lock.file.fileno(),),
                )
                code = child.wait()
                if interrupted or code in (0, 2) or (code != 75 and code >= 0):
                    return code
                journal.event("restart", attempt=attempt + 1, exit_code=code)
                if attempt < 3:
                    time.sleep((1, 2, 5)[attempt])
            journal.put("status", "interrupted")
            print("Restart limit reached. Resume this run after resolving the failure.", file=sys.stderr)
            return 2
        finally:
            if child and child.poll() is None:
                child.kill()
                child.wait()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def saved_report(folder):
    journal = Journal(folder)
    try:
        result = redact(report(journal))
        (folder / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        return result
    finally:
        journal.close()


def summary(result):
    return {
        k: result[k]
        for k in (
            "run_id",
            "status",
            "profile",
            "agent",
            "selected",
            "scored",
            "complete_score",
            "fallbacks",
            "model_calls",
            "unknown_usage_calls",
        )
    }


def parser():
    ap = argparse.ArgumentParser(prog="arena-harness")
    sub = ap.add_subparsers(dest="command", required=True)

    def options(p):
        p.add_argument("--config")
        p.add_argument("--agent")
        p.add_argument("--data")
        p.add_argument("--profile", choices=PROFILES, default="sim")
        p.add_argument("--runs-dir", default=str(DEFAULT_RUNS))

    run = sub.add_parser("run", help="Start one exclusive run; defaults to local simulation")
    options(run)
    run.add_argument("--case", action="append")
    run.add_argument("--max-cases", type=int)
    run.add_argument("--allow-arena", action="store_true")
    run.add_argument("--allow-model", action="store_true")
    run.add_argument("--commit")
    resume = sub.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
    doctor = sub.add_parser("doctor", help="Read-only checks; never opens a session or calls a model")
    options(doctor)
    agents = sub.add_parser("agents").add_subparsers(dest="operation", required=True)
    agents.add_parser("list")
    options(agents.add_parser("check"))
    rep = sub.add_parser("report")
    rep.add_argument("run_id")
    rep.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
    rep.add_argument("--format", choices=("json", "csv"), default="json")
    comp = sub.add_parser("compare")
    comp.add_argument("run_ids", nargs="+")
    comp.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
    comp.add_argument("--allow-mismatch", action="store_true")
    return ap


async def doctor(cfg, runs_dir=DEFAULT_RUNS):
    data = DataPackage(cfg["data"])
    result = {
        "python": sys.version.split()[0],
        "agent": cfg["agent"],
        "data_tables": len(data.tables()),
        "source": cfg["source"],
        "data_hash": cfg["data_hash"],
        "model_called": False,
    }
    import sqlite3

    result["stored_runs"] = []
    for db in sorted(Path(runs_dir).glob("*/run.sqlite")):
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
            status = connection.execute("SELECT value FROM meta WHERE key='status'").fetchone()
            result["stored_runs"].append(
                {
                    "run_id": db.parent.name,
                    "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "status": json.loads(status[0]) if status else "unknown",
                }
            )
    if cfg["profile"] != "sim":
        if not os.environ.get("ARENA_API_KEY"):
            raise ValueError("ARENA_API_KEY required for read-only team checks")
        # No persistent run journal: doctor cannot authorize a later run.
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            journal = Journal(folder)
            api = Transport(cfg, journal)
            try:
                result["team"] = await api.request("GET", "me")
                result["open_sessions"] = []
                for set_ in ("practice", "sandbox", "final"):
                    result["open_sessions"] += [
                        s for s in await api.request("GET", "sessions", params={"set": set_}) if s["status"] == "open"
                    ]
                result["releases"] = await api.request("GET", "data")
            finally:
                await api.close()
                journal.close()
    return result


async def check_agent(cfg):
    import tempfile

    from ..sim import Sim, load_cases

    cfg = {**cfg, "model": {}, "allow_model": False}  # check is always offline
    with tempfile.TemporaryDirectory() as folder:
        journal = Journal(folder)
        models = Models(cfg, journal)
        worker = AgentProcess(cfg, models)
        try:
            sim = Sim(load_cases(), no_clock=True)
            checks = []
            for case in sim.cases.values():
                state = await worker.call(
                    "prepare", cfg["decision_seconds"], context={"case": case, "parameters": cfg["parameters"]}
                )
                opened = sim.open("check", case["case_id"])
                for messages in opened["threads"].values():
                    for message in messages:
                        for att in message.get("attachments", []):
                            from ..docs import read_document

                            att["text"] = read_document(
                                att["name"], sim.attachment("check", opened["session_id"], att["attachment_id"])[0]
                            )
                observation = {
                    "case": case,
                    "threads": opened["threads"],
                    "ready_suppliers": case["suppliers"],
                    "seconds_left": {s: 60 for s in case["suppliers"]},
                    "wave_seconds_left": None,
                }
                result = await worker.call("decide", cfg["decision_seconds"], observation=observation, state=state)
                validate(case, opened["threads"], result["actions"])
                checks.append(case["case_id"])
            return {"agent": cfg["agent"], "checked_cases": checks, "model_calls": 0}
        finally:
            await worker.close()
            await models.close()
            journal.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "agents" and args.operation == "list":
            print(
                "\n".join(
                    sorted(
                        set(BUILTINS) | {p.stem for p in (ROOT / "agents").glob("*.py") if not p.name.startswith("_")}
                    )
                )
            )
            return 0
        if args.command in ("doctor", "agents"):
            cfg = load(args)
            result = asyncio.run(doctor(cfg, args.runs_dir) if args.command == "doctor" else check_agent(cfg))
            print(json.dumps(redact(result), indent=2))
            return 0
        if args.command in ("report", "compare"):
            names = args.run_ids if args.command == "compare" else [args.run_id]
            reports = [saved_report(resolve_run(args.runs_dir, name)) for name in names]
            if args.command == "compare":
                print(json.dumps(compare(reports, args.allow_mismatch), indent=2))
            elif args.format == "csv":
                fields = (
                    "case_id",
                    "score",
                    "deal",
                    "must_haves_ok",
                    "c_open",
                    "c_deal",
                    "c_alt",
                    "c_best",
                    "rounds",
                    "error",
                )
                writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(reports[0]["cases"])
            else:
                print(json.dumps(reports[0], indent=2))
            return 0
        with RunLock() as run_lock:  # before credentials, simulator startup or network calls
            if args.command == "run":
                cfg = load(args)
                authorize(cfg, args.commit)
                run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
                folder = Path(args.runs_dir).resolve() / run_id
                journal = Journal(folder)
                journal.put("config", cfg)
                journal.put("status", "created")
                (folder / "config.json").write_text(json.dumps(redact(cfg), indent=2) + "\n")
            else:
                folder = resolve_run(args.runs_dir, args.run_id)
                journal = Journal(folder)
                cfg = journal.get("config")
                try:
                    current = verify_source(cfg)
                except Exception:
                    journal.close()
                    raise
                if journal.get("status") == "completed":
                    print(json.dumps(summary(report(journal)), indent=2))
                    journal.close()
                    return 0
                authorize(cfg, current["commit"] if cfg["profile"] == "final" else None)
            print(f"Run: {folder.name}", flush=True)
            try:
                code = supervise(cfg, journal, run_lock)
            finally:
                journal.close()
            print(json.dumps(summary(saved_report(folder)), indent=2))
            return code
    except (ValueError, RuntimeError, OSError) as e:
        print(encode(redact({"error": str(e)})), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
