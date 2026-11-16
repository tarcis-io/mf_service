"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mf_service import create_app


@pytest.fixture
def client() -> TestClient:
    """A TestClient wrapping a freshly built application."""
    return TestClient(create_app())
