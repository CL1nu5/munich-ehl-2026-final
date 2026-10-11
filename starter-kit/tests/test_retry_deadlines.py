import asyncio
import time

import httpx
import pytest
from test_harness import config
from test_harness import journal as journal

from arena_kit.client import ArenaError
from arena_kit.harness.transport import Transport


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_network_error_near_deadline_resyncs_without_resending(journal, error):
    seen = []

    def handle(request):
        seen.append(request.headers.get("X-Move-Id"))
        if len(seen) == 1:
            raise error("synthetic network failure")
        return httpx.Response(200, json={})

    async def run():
        api = Transport(config(), journal, transport=httpx.MockTransport(handle))
        try:
            with pytest.raises(ArenaError) as exc:
                await api.request(
                    "POST",
                    "sessions/s/threads/t/messages",
                    body={"message": {"body": "offer"}},
                    deadline=time.monotonic() + 10,
                )
            assert exc.value.status == 409
        finally:
            await api.close()

    asyncio.run(run())
    assert len(seen) == 1


def test_early_network_failure_retries_same_operation(journal):
    seen = []

    def handle(request):
        seen.append(request.headers.get("X-Move-Id"))
        if len(seen) == 1:
            raise httpx.ReadTimeout("synthetic network failure")
        return httpx.Response(200, json={"ok": True})

    async def run():
        api = Transport(config(), journal, transport=httpx.MockTransport(handle))
        try:
            assert await api.request(
                "POST",
                "sessions/s/threads/t/messages",
                body={"message": {"body": "offer"}},
                deadline=time.monotonic() + 60,
            ) == {"ok": True}
        finally:
            await api.close()

    asyncio.run(run())
    assert len(seen) == 2 and seen[0] == seen[1]
