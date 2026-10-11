"""Load existing ignored credentials, then invoke the harness with explicit arguments."""

import getpass
import os
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_credentials():
    source = ROOT / ".env.local"
    if source.is_file():
        for line in source.read_text().splitlines():
            key, sep, value = line.removeprefix("export ").partition("=")
            key = key.strip()
            if sep and key in {"OPENAI_API_KEY", "LLM_API_KEY", "ARENA_API_KEY"}:
                parts = shlex.split(value, comments=True)
                if len(parts) == 1 and parts[0]:
                    if not os.environ.get(key):
                        os.environ[key] = parts[0]
    if not os.environ.get("LLM_API_KEY") and os.environ.get("OPENAI_API_KEY"):
        os.environ["LLM_API_KEY"] = os.environ["OPENAI_API_KEY"]


def main():
    load_credentials()
    os.chdir(ROOT)
    args = sys.argv[1:] or ["doctor", "--config", "configs/learner_openai.toml"]
    from arena_kit.harness.cli import parser

    profile = getattr(parser().parse_args(args), "profile", "sim")
    if profile != "sim" and not os.environ.get("ARENA_API_KEY"):
        if not sys.stdin.isatty():
            raise SystemExit("ARENA_API_KEY must be supplied securely before a hosted command.")
        os.environ["ARENA_API_KEY"] = getpass.getpass("Existing Arena key (hidden, not saved): ")
    from arena_kit.harness.cli import main as harness_main

    sys.argv = ["arena-harness", *args]
    return harness_main()


if __name__ == "__main__":
    raise SystemExit(main())
