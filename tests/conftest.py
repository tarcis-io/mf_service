"""Shared pytest fixtures.

The suite requires ``mf_engine`` (a hard dependency of the package) to be
installed; ``pip install -e ".[dev]"`` pulls it in via the git pin.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mf_service import create_app
from mf_service.core.deps import get_batch_repository, get_batch_storage
from mf_service.repository import InMemoryBatchRepository
from mf_service.storage import InMemoryBatchStorage

#: Default user id used by the batch route tests (sent as the X-User-Id header).
USER = "user-123"


def auth(user_id: str = USER) -> dict[str, str]:
    """Build the ``X-User-Id`` request header for a batch request."""
    return {"X-User-Id": user_id}


@pytest.fixture
def client() -> TestClient:
    """A TestClient wrapping a freshly built application."""
    return TestClient(create_app())


@pytest.fixture
def batch_repo() -> InMemoryBatchRepository:
    """A fresh, isolated in-memory batch repository for one test."""
    return InMemoryBatchRepository()


@pytest.fixture
def batch_storage() -> InMemoryBatchStorage:
    """A fresh, isolated in-memory batch file store for one test."""
    return InMemoryBatchStorage()


@pytest.fixture
def batch_client(
    batch_repo: InMemoryBatchRepository, batch_storage: InMemoryBatchStorage
) -> TestClient:
    """A TestClient whose batch routes use the in-memory repository and storage.

    Overriding ``get_batch_repository`` / ``get_batch_storage`` (otherwise
    process-wide singletons) gives each test a clean store, shared with the
    ``batch_repo`` / ``batch_storage`` fixtures so a test can assert against them
    directly — including the files written by the create endpoint's background
    upload (TestClient runs background tasks before returning).
    """
    app = create_app()
    app.dependency_overrides[get_batch_repository] = lambda: batch_repo
    app.dependency_overrides[get_batch_storage] = lambda: batch_storage
    return TestClient(app)
