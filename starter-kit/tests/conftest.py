import socket
import threading
import time

import pytest


class Clock:
    """A clock the tests move by hand, so the 60-second rule is testable without waiting."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def sim():
    """arena-sim on a free local port: (base_url, Sim, Clock). Event code 'sim'."""
    pytest.importorskip("fastapi")
    import uvicorn

    from arena_kit.sim import Sim, create_app, load_cases

    clock = Clock()
    state = Sim(load_cases(), clock=clock)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(state), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}", state, clock
    server.should_exit = True
    thread.join()


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
