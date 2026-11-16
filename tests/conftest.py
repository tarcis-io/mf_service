"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mf_service import create_app
from mf_service.deps import get_batch_repository
from mf_service.repository import InMemoryBatchRepository


@pytest.fixture
def client() -> TestClient:
    """A TestClient wrapping a freshly built application."""
    return TestClient(create_app())


@pytest.fixture
def batch_repo() -> InMemoryBatchRepository:
    """A fresh in-memory repository, shared with the ``batch_client`` fixture."""
    return InMemoryBatchRepository()


@pytest.fixture
def batch_client(batch_repo: InMemoryBatchRepository) -> TestClient:
    """A TestClient whose batch routes use the in-memory repository.

    Overriding get_batch_repository means the routes never touch Firestore, so
    the full CRUD surface is exercised without any external dependency.
    """
    app = create_app()
    app.dependency_overrides[get_batch_repository] = lambda: batch_repo
    return TestClient(app)
