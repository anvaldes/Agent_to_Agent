"""Shared test helpers.

Each agent is a separate service with its own top-level modules (main, config, offer, ...),
so the modules of one agent are loaded with load_agent() and kept as module objects.
"""

import asyncio
import importlib
import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
AGENT_MODULE_NAMES = {
    "main", "config", "offer", "agent_executor", "negotiation", "supplier_client",
    "referee_client", "a2a_client", "pricing", "rules", "judge",
}


def load_agent(folder: str, *module_names: str) -> SimpleNamespace:
    """Imports modules from one agent folder without clashing with other agents' modules."""
    for name in AGENT_MODULE_NAMES:
        sys.modules.pop(name, None)
    path = str(ROOT / folder)
    sys.path.insert(0, path)
    try:
        return SimpleNamespace(**{name: importlib.import_module(name) for name in module_names})
    finally:
        sys.path.remove(path)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BackgroundServer:
    """Runs an ASGI app with uvicorn in a background thread (real HTTP, like Cloud Run)."""

    def __init__(self, app, port: int) -> None:
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("Server did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def serve():
    """serve(app, port) starts a background server that is stopped after the test."""
    servers = []

    def _serve(app, port: int) -> BackgroundServer:
        server = BackgroundServer(app, port).__enter__()
        servers.append(server)
        return server

    yield _serve
    for server in servers:
        server.__exit__(None, None, None)


class FakeClaude:
    """Stands in for AsyncAnthropic: returns scripted JSON answers and records each request."""

    def __init__(self, answers: list[dict]) -> None:
        self.answers = list(answers)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        await asyncio.sleep(0)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return SimpleNamespace(
            stop_reason="end_turn",
            stop_details=None,
            content=[SimpleNamespace(type="text", text=json.dumps(answer))],
        )
