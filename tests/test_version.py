"""Tests for the /version endpoint."""

from __future__ import annotations

import mf_engine
from fastapi.testclient import TestClient

import mf_service
from mf_service import create_app
from mf_service.config import Settings


def test_version_returns_all_fields(client: TestClient):
    response = client.get("/version")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"name", "description", "version", "engine_version"}


def test_version_reports_service_identity(client: TestClient):
    body = client.get("/version").json()
    # Name and version come from the installed package metadata; description is
    # present and non-empty.
    assert body["name"] == "mf_service"
    assert body["version"] == mf_service.__version__
    assert body["description"]


def test_version_reports_engine_version(client: TestClient):
    # The engine version must match mf_engine's own single source of truth.
    body = client.get("/version").json()
    assert body["engine_version"] == mf_engine.__version__


def test_version_reflects_injected_settings():
    # Settings passed to create_app must reach the /version route, not just the
    # OpenAPI metadata — create_app registers the dependency override so the
    # factory argument and Depends(get_settings) can't diverge.
    app = create_app(Settings(name="injected", description="injected desc"))
    body = TestClient(app).get("/version").json()
    assert body["name"] == "injected"
    assert body["description"] == "injected desc"
    # The engine version is independent of service settings.
    assert body["engine_version"] == mf_engine.__version__
