import asyncio
import os
import sys
import time

from .config import ROOT
from .state import encode


class AgentProcess:
    def __init__(self, cfg, models, journal=None):
        self.cfg, self.models, self.process, self.journal = cfg, models, None, journal
        self.runtime = {
            "run_id": journal.folder.name if journal else None,
            "data_dir": cfg["data"],
            "decision_seconds": cfg["decision_seconds"],
            "model_timeout": cfg["model"].get("timeout", 20),
        }

    async def start(self):
        # Only a small OS environment crosses into plugins; keys stay in the broker.
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT")}
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "arena_kit.harness.worker",
            cwd=ROOT,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=256 * 1024,
        )
        await self.write({"agent": self.cfg["agent"], "data": self.cfg["data"], "runtime": self.runtime})

    async def write(self, value):
        self.process.stdin.write((encode(value) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, method, timeout, **args):
        import json

        deadline = time.monotonic() + timeout
        runtime = {**self.runtime, "decision_seconds": min(timeout, self.cfg["decision_seconds"])}
        if "context" in args:
            args["context"] = {**args["context"], "runtime": runtime}
        if "observation" in args:
            args["observation"] = {**args["observation"], "runtime": runtime}

        async def exchange():
            if self.process is None:
                await self.start()
            await self.write({"method": method, **args})
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise RuntimeError("Agent process exited")
                reply = json.loads(line)
                if reply.get("rpc") == "model":
                    try:
                        # Queue waiting counts toward the worker's deadline as well as HTTP time.
                        value = await asyncio.wait_for(
                            self.models.complete(
                                reply["messages"], **({"background": True} if method == "on_result" else {})
                            ),
                            max(0.01, deadline - time.monotonic() - 2),
                        )
                        await self.write({"value": value})
                    except Exception as e:
                        await self.write({"error": type(e).__name__})
                elif reply.get("rpc") == "history":
                    from agents._learner.memory.episodes import extract

                    episodes = extract(self.journal.folder) if self.journal else []
                    await self.write({"value": {"run_id": self.runtime["run_id"], "episodes": episodes}})
                elif "result" in reply:
                    return reply["result"]
                else:
                    raise RuntimeError(reply.get("error", "Invalid worker response"))

        try:
            return await asyncio.wait_for(exchange(), timeout=max(0.01, timeout))
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.process:
            if self.process.returncode is None:
                self.process.kill()
            await self.process.wait()
            self.process = None
