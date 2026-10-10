import asyncio
import os
import sys

from .config import ROOT
from .state import encode


class AgentProcess:
    def __init__(self, cfg, models):
        self.cfg, self.models, self.process = cfg, models, None

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
        await self.write(
            {"agent": self.cfg["agent"], "data": self.cfg["data"], "release": self.cfg.get("data_release")}
        )

    async def write(self, value):
        self.process.stdin.write((encode(value) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, method, timeout, **args):
        import json

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
                        value = await self.models.complete(reply["messages"])
                        await self.write({"value": value})
                    except Exception as e:
                        await self.write({"error": type(e).__name__})
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
