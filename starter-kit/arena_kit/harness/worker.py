"""Supervised plugin worker. stdout is a JSON protocol; plugin output goes to stderr."""

import contextlib
import importlib.util
import json
import sys

from ..cost import total_cost
from ..data import DataPackage
from .config import agent_path
from .plugins import BUILTINS
from .state import encode


def load_agent(name):
    if name in BUILTINS:
        return BUILTINS[name]()
    spec = importlib.util.spec_from_file_location("team_agent", agent_path(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Agent()


class Services:
    def __init__(self, data, output, runtime=None):
        self.data, self.output = DataPackage(data), output
        self.runtime = runtime or {}

    def history(self):
        self.output.write(encode({"rpc": "history"}) + "\n")
        self.output.flush()
        return json.loads(sys.stdin.readline())["value"]

    def complete(self, messages):
        self.output.write(encode({"rpc": "model", "messages": messages}) + "\n")
        self.output.flush()
        response = json.loads(sys.stdin.readline())
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["value"]

    @staticmethod
    def cost(case, terms, standard=None, supplier=None):
        return total_cost(case, terms, standard, supplier)


def main():
    output = sys.stdout
    init = json.loads(sys.stdin.readline())
    with contextlib.redirect_stdout(sys.stderr):
        try:
            agent = load_agent(init["agent"])
            services = Services(init["data"], output, init.get("runtime"))
            for line in sys.stdin:
                request = json.loads(line)
                try:
                    if request["method"] == "prepare":
                        result = agent.prepare(request["context"])
                    elif request["method"] == "on_result":
                        callback = getattr(agent, "on_result", None)
                        result = (
                            callback(request["context"], request["state"], services) if callback else request["state"]
                        )
                    else:
                        result = agent.decide(request["observation"], request["state"], services)
                    reply = {"result": result}
                    encode(reply)
                except Exception as e:
                    reply = {"error": type(e).__name__}  # errors must not leak credentials or prompts
                output.write(encode(reply) + "\n")
                output.flush()
        except Exception as e:
            output.write(encode({"error": type(e).__name__}) + "\n")
            output.flush()


if __name__ == "__main__":
    main()
