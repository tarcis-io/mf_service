"""Tests for the console entry point (`python -m mf_service` / `mf-service`)."""

from __future__ import annotations

import mf_service.__main__ as entry
from mf_service.config import Settings, get_settings


def test_main_runs_uvicorn_with_settings(monkeypatch):
    # main() must start uvicorn against the `mf_service:app` import string and
    # pass the host/port from Settings — not hard-coded values. We stub
    # uvicorn.run (it would otherwise block on a real server) and capture its
    # arguments, and override get_settings so the assertion is independent of
    # the ambient environment.
    captured: dict[str, object] = {}

    def _fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(entry.uvicorn, "run", _fake_run)
    monkeypatch.setattr(
        entry, "get_settings", lambda: Settings(host="127.0.0.1", port=9001)
    )

    entry.main()

    assert captured["app"] == "mf_service:app"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001


def test_main_defaults_come_from_settings(monkeypatch):
    # With no env overrides, main() uses the Settings defaults (0.0.0.0:8000).
    # Clear any ambient MF_SERVICE_HOST/PORT and the settings cache so the test
    # is deterministic regardless of the developer's shell.
    monkeypatch.delenv("MF_SERVICE_HOST", raising=False)
    monkeypatch.delenv("MF_SERVICE_PORT", raising=False)
    get_settings.cache_clear()
    captured: dict[str, object] = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: captured.update(kw))

    entry.main()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8000
    get_settings.cache_clear()
