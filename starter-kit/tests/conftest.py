import asyncio
import socket

import pytest


class Clock:
    """A clock the tests move by hand, so the 60-second rule is testable without waiting."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def sim(monkeypatch):
    """Exercise the HTTP protocol in process, including sync clients and concurrent threads."""
    pytest.importorskip("fastapi")
    import httpx

    from arena_kit.sim import Sim, create_app, load_cases

    clock = Clock()
    state = Sim(load_cases(), clock=clock)
    app = create_app(state)
    original = httpx.HTTPTransport.handle_request

    def handle(transport, request):
        if request.url.host != "127.0.0.1" or request.url.port != 18080:
            return original(transport, request)

        async def dispatch():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                reply = await client.request(
                    request.method, request.url, headers=request.headers, content=request.read()
                )
                return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content, request=request)

        return asyncio.run(dispatch())

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    yield "http://127.0.0.1:18080", state, clock


def pytest_addoption(parser):
    parser.addoption(
        "--arena-live",
        action="store_true",
        default=False,
        help="Explicitly enable hosted tests (consumes Arena sessions)",
    )


@pytest.fixture(autouse=True)
def no_external_network(request, monkeypatch):
    """A default test run cannot consume live sessions even with real keys in env."""
    if request.config.getoption("--arena-live"):
        return
    import ipaddress

    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple):
            host = address[0]
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host == "localhost"
            if not local:
                raise AssertionError("External network blocked in default tests")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
