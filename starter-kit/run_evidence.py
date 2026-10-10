"""Safe entry point: local simulation or explicitly selected final. No practice mode."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_local_env():
    file = ROOT / ".env.local"
    if file.is_symlink():
        raise ValueError("Refusing symlink credentials")
    if file.exists():
        for line in file.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in ("OPENAI_API_KEY", "ARENA_API_KEY"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    if os.environ.get("OPENAI_API_KEY"):
        os.environ.setdefault("LLM_API_KEY", os.environ["OPENAI_API_KEY"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("local", "model", "check", "final"))
    parser.add_argument("--commit")
    args = parser.parse_args()
    load_local_env()
    os.environ.setdefault("XDG_STATE_HOME", str(ROOT.parent / ".runs" / "state"))
    if args.mode == "check":
        command = ["agents", "check", "--agent", "evidence_buyer"]
    else:
        use_model = args.mode in ("model", "final")
        if use_model and not os.environ.get("LLM_API_KEY"):
            parser.error("OpenAI key is missing from local configuration")
        config = ROOT / "configs" / ("evidence-openai.toml" if use_model else "evidence.toml")
        command = ["run", "--config", str(config), "--profile", "sim"]
        if use_model:
            command += ["--allow-model"]
        if args.mode == "final":
            if not args.commit:
                parser.error("Final requires --commit with the frozen clean commit")
            command = [
                "run",
                "--config",
                str(config),
                "--profile",
                "final",
                "--allow-arena",
                "--allow-model",
                "--commit",
                args.commit,
            ]
    from arena_kit.harness.cli import main as harness

    sys.argv = ["arena-harness", *command]
    return harness()


if __name__ == "__main__":
    raise SystemExit(main())
