"""`arena` command line: register, list cases, play one case or a whole set, show the board,
run the MCP server."""

import argparse
import functools
import io
import json
import logging
import os
import statistics
import zipfile
from pathlib import Path

from .client import ArenaClient, ArenaError
from .play import RemoteTable, hosted, play, play_all, play_waves


def load_env(path=".env"):
    """Read KEY=VALUE lines from .env into the environment (real env vars win)."""
    p = Path(path)
    if p.is_file():
        for line in p.read_text().splitlines():
            key, sep, value = line.partition("=")
            value = value.strip().strip("\"'")
            if sep and value and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value)


def agent_factory(name, data):
    from .agents import LLMAgent, NaiveAgent, llm_from_env

    if name == "naive":
        return NaiveAgent
    package = None
    if data:
        from .data import DataPackage

        package = DataPackage(data)
        package.lookup("")  # build the ID index once, before agents share it
    complete = llm_from_env()
    return lambda: LLMAgent(complete, package)


def summary(result):
    keep = ("deal", "supplier_id", "terms", "c_open", "c_deal", "c_best", "score")
    return {k: result.get(k) for k in keep if k in result}


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser(prog="arena")
    ap.add_argument("-v", "--verbose", action="store_true")
    verbose = argparse.ArgumentParser(add_help=False)  # -v also after the command
    verbose.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser = functools.partial(sub.add_parser, parents=[verbose])
    r = sub.add_parser(
        "register", help="arena-sim only: get a simulator key (the arena's key comes from the organisers)"
    )
    r.add_argument("team_name")
    r.add_argument("event_code")
    data = {"default": os.environ.get("ARENA_DATA"), "help": "data package folder"}
    sets = {"default": "practice", "help": "practice, final or sandbox"}
    c = sub.add_parser("cases", help="list the cases you can play")
    c.add_argument("--set", **sets)
    p = sub.add_parser("play", help="play one case")
    p.add_argument("case_id")
    p.add_argument("--agent", default="naive", choices=["naive", "llm"])
    p.add_argument("--data", **data)
    a = sub.add_parser("run", help="play every case of a set in parallel")
    a.add_argument("--set", **sets)
    a.add_argument("--agent", default="naive", choices=["naive", "llm"])
    a.add_argument("--data", **data)
    a.add_argument("--parallel", type=int, default=8, help="cases at once (at most 10)")
    a.add_argument("--waves", type=int, default=5, help="final: stop after this many waves have closed (rehearsal: 2)")
    d = sub.add_parser("data", help="download and unpack the data (package, Sunday release once public)")
    d.add_argument("--out", default="data", help="folder to unpack into")
    sub.add_parser("me", help="your team: practice quota used and left, best score per case")
    b = sub.add_parser("board", help="show the leaderboard")
    b.add_argument("--set", **sets)
    m = sub.add_parser("mcp", help="run the read-only MCP server over the data (stdio)")
    m.add_argument("--data", **data)
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.cmd == "mcp":
        from .mcp_server import main as serve

        return serve(args.data)
    client = ArenaClient()
    if args.cmd == "register":
        out = client.register(args.team_name, args.event_code)
        print(json.dumps(out, indent=2))
        print("\nA simulator key: put it into .env as ARENA_API_KEY=... while you point ARENA_URL at arena-sim.")
    elif args.cmd == "cases":
        cases = client.cases(args.set)
        playable = {c["case_id"] for c in hosted(cases)}
        for case in cases:
            mark = "" if case["case_id"] in playable else "  (not hosted: data only)"
            print(case["case_id"], case.get("type", ""), case.get("title", "") + mark, sep="  ")
    elif args.cmd == "me":
        try:
            print(json.dumps(client.me(), indent=2))
        except ArenaError as e:
            print(f"no team view on this arena ({e})" if e.status == 404 else e)
    elif args.cmd == "board":
        print(json.dumps(client.leaderboard(args.set), indent=2))
    elif args.cmd == "data":
        for name, info in client.downloads().items():
            if info.get("url"):
                zipfile.ZipFile(io.BytesIO(client.download(name))).extractall(args.out)
                print(f"{name}: {Path(args.out) / name}")
            else:
                print(f"{name}: public from {info.get('at')}")
    elif args.cmd == "play":
        table = RemoteTable(client)
        try:
            result = play(table, args.case_id, agent_factory(args.agent, args.data)())
        except ArenaError as e:  # e.g. not hosted, the quota, a paused key: the arena says why
            raise SystemExit(str(e)) from None
        print(json.dumps(summary(result), indent=2))
        replay = client.session(table.session_id).get("replay_url")
        if replay:  # the supplier's reasons, move by move: read it, improve, play again
            print(f"replay: {os.environ['ARENA_URL'].rstrip('/')}{replay}")
    elif args.set == "final":  # waits for each wave and plays its cases within the cap
        out = play_waves(client, agent_factory(args.agent, args.data), args.waves, min(args.parallel, 10))
    else:
        ids = [case["case_id"] for case in hosted(client.cases(args.set))]
        out = play_all(
            lambda: RemoteTable(client),
            ids,
            agent_factory(args.agent, args.data),
            min(args.parallel, 10),
        )
    if args.cmd == "run":
        for case_id, result in out.items():
            print(case_id, result if isinstance(result, Exception) else summary(result))
        scores = {c: r.get("score") for c, r in out.items() if isinstance(r, dict)}
        if args.set == "final" and scores:  # points so far: each wave 0-100, their sum, as the board counts
            waves = {}
            for case in client.cases("final"):  # your own case list: which wave each case was in
                waves.setdefault(case["wave"], []).append(scores.get(case["case_id"]) or 0)
            points = {n: round(100 * statistics.mean(v), 1) for n, v in sorted(waves.items())}
            print(f"waves: {points}  points so far: {round(sum(points.values()), 1)} (the board is the record)")
        elif scores and all(v is not None for v in scores.values()):
            print(f"mean score: {100 * statistics.mean(scores.values()):.1f}")
    if args.cmd in ("play", "run") and client.practice_remaining is not None:
        print(f"practice sessions left today: {client.practice_remaining}")


if __name__ == "__main__":
    main()
