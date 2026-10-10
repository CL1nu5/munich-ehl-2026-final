"""Linus's concept: a negotiation agent that learns from the finished cases of its own run.

Sub-agents per decision (LangGraph): Observer -> Router -> Offer Builder -> Negotiator (LLM) -> Critic,
or Closer -> Reflector (LLM) when the case ends. See agents/_learner/README.md.
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class Agent:
    def __init__(self):
        self._cache = {}

    def prepare(self, context):
        try:
            from agents._learner import core

            return core.prepare(context, self._cache)
        except Exception as e:  # a broken plugin must not cost the case: play the safe policy
            return {"v": 0, "broken": type(e).__name__, "p": dict(context.get("parameters") or {})}

    def decide(self, observation, state, services):
        try:
            from agents._learner import core

            return core.decide(observation, state, services, self._cache)
        except Exception as e:
            return emergency(observation, state, type(e).__name__)


def emergency(observation, state, reason):
    """Last resort: ask a question (no term sheets) while rounds last, then the harness's safe close."""
    from arena_kit.harness.validation import close_action
    from arena_kit.play import moves_left

    case, threads = observation["case"], observation["threads"]
    state = dict(state or {})
    state["emergencies"] = state.get("emergencies", 0) + 1
    state["emergency_reason"] = reason
    llm = dict(state.get("llm") or {})
    llm["fails"] = max(2, llm.get("fails", 0))  # if the model output caused this, do not call it again
    state["llm"] = llm
    ready = observation.get("ready_suppliers") or []
    if state["emergencies"] <= 3 and ready and all(moves_left(case, threads[s]) > 1 for s in ready):
        body = (
            "Dear colleagues,\nthank you for your message. Could you tell us where you see flexibility - on price, "
            "payment terms or contract length?\nBest regards\nHollerau Pumpenwerke GmbH, Purchasing"
        )
        actions = [
            {
                "supplier_id": s,
                "message": {
                    "subject": "Re: " + str(case.get("title", "Your quote"))[:150],
                    "body": body,
                    "term_sheets": [],
                },
            }
            for s in ready
        ]
        return {"actions": actions, "state": state}
    return {"actions": [close_action(case, threads)], "state": state}
