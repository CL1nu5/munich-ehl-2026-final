"""Model access through the harness gateway (`services.complete`), as a LangChain chat model.

The gateway is text-in/text-out (no tools, no response_format), synchronous and not thread-safe, so
calls are serial, never retried, and JSON is parsed here.
"""

import json
import re

try:  # LangChain is optional at runtime: without it we call the gateway directly.
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
except Exception:  # pragma: no cover - exercised by the import-fallback test
    BaseChatModel = None

ROLES = {"system": "system", "human": "user", "ai": "assistant"}


def to_dicts(messages):
    out = []
    for m in messages:
        if isinstance(m, dict):
            out.append({"role": m["role"], "content": m["content"]})
        else:
            out.append({"role": ROLES.get(m.type, "user"), "content": m.content})
    return out


if BaseChatModel is not None:

    class ServicesChatModel(BaseChatModel):
        """LangChain chat model whose every call goes through the harness's budgeted gateway."""

        complete: object  # services.complete

        @property
        def _llm_type(self):
            return "arena-harness-gateway"

        def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
            text = self.complete(to_dicts(messages))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text or ""))])

else:
    ServicesChatModel = None


class Gateway:
    """One object per decide: counts calls and failures, never raises."""

    def __init__(self, services):
        self.services = services
        self.calls = 0
        self.failures = 0
        self.model = None
        if ServicesChatModel is not None and services is not None:
            try:
                self.model = ServicesChatModel(complete=services.complete)
            except Exception:
                self.model = None

    def text(self, messages):
        self.calls += 1
        try:
            if self.model is not None:
                out = self.model.invoke(messages).content
            else:
                out = self.services.complete(to_dicts(messages))
            if not isinstance(out, str) or not out.strip():
                raise ValueError("empty model reply")
            return out
        except Exception:
            self.failures += 1
            return None

    def json(self, messages):
        text = self.text(messages)
        if text is None:
            return None
        out = parse_json(text)
        if out is None:
            self.failures += 1
        return out


FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(text):
    """The first top-level JSON object in a model reply, or None (never an object nested in a broken one)."""
    if not isinstance(text, str) or not text:
        return None
    m = FENCE.search(text)
    if m:
        text = m.group(1)
    start = text.find("{")
    while start >= 0:
        depth, in_str, esc, end = 0, False, False, None
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            return None  # never closes: do not fall back to an inner object
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else None
        except ValueError:
            start = text.find("{", end + 1)
    return None
