"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mf_service import create_app
from mf_service.deps import get_batch_repository, get_storage_gateway
from mf_service.repository import InMemoryBatchRepository
from mf_service.storage import InMemoryStorageGateway


@pytest.fixture
def client() -> TestClient:
    """A TestClient wrapping a freshly built application."""
    return TestClient(create_app())


@pytest.fixture
def batch_repo() -> InMemoryBatchRepository:
    """A fresh in-memory repository, shared with the ``batch_client`` fixture."""
    return InMemoryBatchRepository()


@pytest.fixture
def storage_gateway() -> InMemoryStorageGateway:
    """A fresh in-memory storage gateway, shared with ``batch_client``."""
    return InMemoryStorageGateway()


@pytest.fixture
def batch_client(
    batch_repo: InMemoryBatchRepository,
    storage_gateway: InMemoryStorageGateway,
) -> TestClient:
    """A TestClient whose batch routes use the in-memory repo and storage.

    Overriding get_batch_repository and get_storage_gateway means the routes
    never touch Firestore or a live bucket, so the full CRUD + upload surface is
    exercised without any external dependency. Starlette's TestClient runs
    background tasks before returning, so a create response already reflects the
    completed upload.
    """
    app = create_app()
    app.dependency_overrides[get_batch_repository] = lambda: batch_repo
    app.dependency_overrides[get_storage_gateway] = lambda: storage_gateway
    return TestClient(app)
