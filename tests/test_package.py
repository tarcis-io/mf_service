"""Tests for the package surface and application factory."""

from __future__ import annotations

import importlib

from fastapi import FastAPI

import mf_service
from mf_service import create_app


def test_package_exposes_version():
    # A string version is always available (from metadata, or the source-tree
    # fallback), so callers can rely on mf_service.__version__.
    assert isinstance(mf_service.__version__, str)
    assert mf_service.__version__


def test_version_falls_back_when_not_installed(monkeypatch):
    # When the distribution metadata is unavailable (running from a source tree
    # that was never installed), __version__ falls back to "0.0.0" rather than
    # raising on import.
    from importlib.metadata import PackageNotFoundError

    def _raise(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr("importlib.metadata.version", _raise)
    reloaded = importlib.reload(mf_service)
    try:
        assert reloaded.__version__ == "0.0.0"
    finally:
        # Restore real metadata-backed version for any later tests.
        monkeypatch.undo()
        importlib.reload(mf_service)


def test_create_app_returns_fastapi_instance():
    app = create_app()
    assert isinstance(app, FastAPI)


def test_create_app_builds_independent_instances():
    # The factory yields fresh apps, not a shared singleton — important for
    # isolated tests and customised entry points.
    assert create_app() is not create_app()


def test_module_app_is_fastapi_instance():
    # `uvicorn mf_service:app` relies on this attribute existing.
    assert isinstance(mf_service.app, FastAPI)
