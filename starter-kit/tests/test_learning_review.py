import asyncio

import pytest
from test_harness import api_fixture, config
from test_harness import journal as journal
from test_negotiation_generalization import scenario

from agents._learner import preferences
from arena_kit.harness.engine import Engine


@pytest.mark.parametrize("seed", [13, 101, 307, 911])
@pytest.mark.parametrize(
    "evidence",
    [
        "We are agreeable to your proposal of a two-year contract and fourteen-day payment terms.",
        "We accept your proposed payment terms.",
        "Your proposed terms are acceptable to us.",
    ],
)
def test_assent_is_not_a_new_restrictive_preference(seed, evidence):
    case, _ = scenario(seed)
    led = {"body": evidence, "std": {"payment_days": 30}, "message_id": "synthetic"}
    pref = preferences.update(
        case, led, {"fields": {}}, [{"field": "payment_days", "values": [14], "evidence": evidence}]
    )
    assert not pref["fields"]


@pytest.mark.parametrize(
    "text",
    [
        "We require payment within fourteen days.",
        "We accept only payment within fourteen days.",
        "Your payment terms are not acceptable; fourteen days is our limit.",
        "We won't accept longer payment terms than fourteen days.",
        "Wir akzeptieren ausschließlich vierzehn Tage Zahlungsziel.",
    ],
)
def test_explicit_countercondition_still_becomes_preference(text):
    case, _ = scenario(101)
    pref = preferences.update(
        case,
        {"body": text, "std": {}, "message_id": "new"},
        {},
        [{"field": "payment_days", "values": [14], "evidence": text}],
    )
    assert pref["fields"]["payment_days"]["values"] == [14]


def test_repeated_snapshot_uses_already_parsed_attachments(journal):
    async def run():
        cfg = config()
        sim, api = api_fixture(cfg, journal)
        opened = sim.open("test", "case:S01")
        engine = Engine(cfg, journal, api=api)
        try:
            _, first, _ = await engine.snapshot(opened["session_id"])
            assert any(a.get("text") for ev in first.values() for m in ev for a in m.get("attachments", []))

            async def no_parser(*args, **kwargs):
                pytest.fail("Already parsed immutable attachment must not be parsed again")

            from unittest.mock import patch

            with patch("asyncio.create_subprocess_exec", no_parser):
                _, second, _ = await engine.snapshot(opened["session_id"])

            def texts(threads):
                return [a.get("text") for ev in threads.values() for m in ev for a in m.get("attachments", [])]

            assert texts(second) == texts(first)
        finally:
            await api.close()
            await engine.models.close()

    asyncio.run(run())


def test_document_cache_separates_sessions_and_parser_names(journal, monkeypatch):
    from pathlib import Path

    parsed = []

    class API:
        async def request(self, method, path, **kwargs):
            return path.encode()

    class Parser:
        returncode = 0

        def __init__(self, name, path):
            self.output = name.encode() + b":" + Path(path).read_bytes()

        async def communicate(self):
            return self.output, b""

    async def parse(*args, **kwargs):
        parsed.append(args[-2])
        return Parser(args[-2], args[-1])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", parse)

    async def run():
        engine = Engine(config(), journal, api=API())
        try:
            texts = []
            for sid, name in [
                ("first", "quote.txt"),
                ("second", "quote.txt"),
                ("first", "quote.csv"),
                ("first", "quote.txt"),
            ]:
                message = {"attachments": [{"attachment_id": "same-id", "name": name}]}
                await engine.documents(sid, message)
                texts.append(message["attachments"][0]["text"])
            assert texts[0] != texts[1]
            assert texts[0] != texts[2]
            assert texts[0] == texts[3]
            assert parsed == ["quote.txt", "quote.txt", "quote.csv"]
        finally:
            await engine.models.close()

    asyncio.run(run())
