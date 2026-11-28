"""Tests for the console entry point (uvicorn wiring)."""

from __future__ import annotations

import pytest

from mf_service import __main__
from mf_service.core.config import get_settings


def test_main_runs_uvicorn_with_configured_host_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # main() starts uvicorn against the ASGI app using the host/port from
    # Settings, without actually binding a socket.
    monkeypatch.setenv("MF_SERVICE_HOST", "127.0.0.1")
    monkeypatch.setenv("MF_SERVICE_PORT", "9002")
    get_settings.cache_clear()

    calls: dict[str, object] = {}

    def fake_run(app: str, host: str, port: int) -> None:
        calls.update(app=app, host=host, port=port)

    monkeypatch.setattr(__main__.uvicorn, "run", fake_run)
    try:
        __main__.main()
    finally:
        get_settings.cache_clear()

    assert calls == {"app": "mf_service:app", "host": "127.0.0.1", "port": 9002}
