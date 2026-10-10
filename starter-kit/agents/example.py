"""Copy this file to my_agent.py and run --agent my_agent. No API plumbing needed."""

from arena_kit.harness.plugins import Baseline


class Agent(Baseline):
    def prepare(self, context):
        # context: case and TOML [parameters]. Return JSON state.
        return {"decisions": 0}

    def decide(self, observation, state, services):
        # services.data.query/lookup/document; services.cost; services.complete(messages).
        state = {**state, "decisions": state["decisions"] + 1}
        return super().decide(observation, state, services)
