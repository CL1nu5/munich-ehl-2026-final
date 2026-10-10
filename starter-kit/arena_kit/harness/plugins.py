"""Reference plugins. State is explicit JSON so the supervisor can restore it."""

from ..agents import LLMAgent, NaiveAgent
from ..cost import complete_offer
from ..play import latest, moves_left, standing


class Baseline:
    def prepare(self, context):
        return {}

    def decide(self, observation, state, services):
        case, threads = observation["case"], observation["threads"]
        actions = []
        for sid in observation["ready_suppliers"]:
            events = threads[sid]
            offer_id, terms = standing(events)
            if latest(events).get("status") in ("agreed", "final_offer") or moves_left(case, events) <= 0:
                # Runner owns the cross-supplier safe close, including alternatives.
                return {
                    "actions": [
                        {"supplier_id": sid, "message": {"action": {"type": "accept", "term_sheet_id": offer_id}}}
                    ],
                    "state": state,
                }
            actions.append(
                {
                    "supplier_id": sid,
                    "message": {
                        "subject": case["title"],
                        "body": "Thank you. Could you improve this package?",
                        "term_sheets": [
                            {"terms": complete_offer(case, {**terms, "unit_price": terms["unit_price"] * 0.95})}
                        ],
                    },
                }
            )
        return {"actions": actions, "state": state}


class StarterNaive:
    def prepare(self, context):
        return {}

    def decide(self, observation, state, services):
        ready = {s: observation["threads"][s] for s in observation["ready_suppliers"]}
        action = NaiveAgent().decide(observation["case"], ready)
        return {"actions": [action], "state": state}


class StarterLLM:
    def prepare(self, context):
        return {}

    def decide(self, observation, state, services):
        agent = LLMAgent(
            lambda system, user: services.complete(
                [{"role": "system", "content": system}, {"role": "user", "content": user}]
            ),
            services.data,
        )
        ready = {s: observation["threads"][s] for s in observation["ready_suppliers"]}
        return {"actions": [agent.decide(observation["case"], ready)], "state": state}


BUILTINS = {"baseline": Baseline, "starter_naive": StarterNaive, "starter_llm": StarterLLM}
