from __future__ import annotations

import errno
from pathlib import Path

from fastapi.testclient import TestClient

from paper_galaxy.web.server import bind_loopback_socket, create_app


class _FakeSocket:
    def __init__(self, *, busy_port: int | None = None) -> None:
        self.busy_port = busy_port
        self.host = ""
        self.port = -1
        self.closed = False

    def setsockopt(self, *_args: object) -> None:
        return None

    def bind(self, address: tuple[str, int]) -> None:
        self.host, requested = address
        if requested == self.busy_port:
            raise OSError(errno.EADDRINUSE, "synthetic busy port")
        self.port = 43123 if requested == 0 else requested

    def listen(self, _backlog: int = 0) -> None:
        return None

    def getsockname(self) -> tuple[str, int]:
        return self.host, self.port

    def close(self) -> None:
        self.closed = True


def test_loopback_socket_uses_requested_free_port(
    monkeypatch: object,
) -> None:
    sockets: list[_FakeSocket] = []

    def fake_socket(*_args: object) -> _FakeSocket:
        created = _FakeSocket()
        sockets.append(created)
        return created

    monkeypatch.setattr("paper_galaxy.web.server.socket.socket", fake_socket)
    bound, port, used_fallback = bind_loopback_socket("127.0.0.1", 0)
    try:
        assert bound.getsockname()[0] == "127.0.0.1"
        assert port == 43123
        assert used_fallback is False
    finally:
        bound.close()
    assert sockets[0].closed is True


def test_loopback_socket_falls_back_when_requested_port_is_busy(
    monkeypatch: object,
) -> None:
    requested = 8765
    sockets: list[_FakeSocket] = []

    def fake_socket(*_args: object) -> _FakeSocket:
        created = _FakeSocket(busy_port=requested)
        sockets.append(created)
        return created

    monkeypatch.setattr("paper_galaxy.web.server.socket.socket", fake_socket)

    bound, port, used_fallback = bind_loopback_socket("127.0.0.1", requested)
    try:
        assert used_fallback is True
        assert port == 43123
    finally:
        bound.close()
    assert sockets[0].closed is True
    assert sockets[1].closed is True


def test_app_lifespan_starts_and_stops_job_manager(tmp_path: Path) -> None:
    events: list[str] = []

    class FakeManager:
        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    with TestClient(create_app(tmp_path, job_manager=FakeManager())) as client:
        assert client.get("/api/health").status_code == 200
        assert events == ["start"]

    assert events == ["start", "stop"]
