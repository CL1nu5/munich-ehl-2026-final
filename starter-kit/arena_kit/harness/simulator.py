"""Loopback-only simulator managed by the supervisor, surviving engine restarts."""

import contextlib
import os
import pickle
import socket
import threading
import time

from ..sim import Sim, create_app, load_cases


@contextlib.contextmanager
def server(folder, facts=None):
    import uvicorn

    file = folder / "simulator.pickle"
    state = Sim(load_cases(), facts=facts)
    # This file is private local harness state, never an imported/public artifact.
    if file.exists():
        state.__dict__.update(pickle.loads(file.read_bytes()))
    state.teams["local-harness"] = "Local harness"
    app = create_app(state)

    @app.middleware("http")
    async def save(request, call_next):
        response = await call_next(request)
        # Persist mutations BEFORE sending their response to the engine.
        with state.lock:
            snapshot = {k: v for k, v in vars(state).items() if k not in ("lock", "clock")}
            temp = file.with_suffix(".tmp")
            with temp.open("wb") as f:
                f.write(pickle.dumps(snapshot))
                f.flush()
                os.fsync(f.fileno())
            temp.replace(file)
        return response

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="error", access_log=False)
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    until = time.monotonic() + 10
    while not running.started:
        if not thread.is_alive() or time.monotonic() > until:
            running.should_exit = True
            sock.close()
            raise RuntimeError("Local simulator failed to start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        running.should_exit = True
        thread.join(timeout=10)
        sock.close()
