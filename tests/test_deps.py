"""Tests for the shared FastAPI dependencies."""

from __future__ import annotations

import pytest

from mf_service.core import deps
from mf_service.core.config import Settings
from mf_service.repository import BatchRepository, FirestoreBatchRepository
from mf_service.storage import BatchStorage, GCSBatchStorage


def test_get_batch_repository_builds_firestore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The provider builds the Firestore-backed repository from settings, using
    # the configured collection — without opening a live connection (the client
    # builder is patched).
    captured: dict[str, object] = {}

    def fake_client(project: str | None, database: str) -> object:
        captured["project"] = project
        captured["database"] = database
        return object()  # stand-in Firestore client

    monkeypatch.setattr(deps, "_build_firestore_client", fake_client)

    settings = Settings(
        firestore_project="proj-x",
        firestore_database="db-x",
        batches_collection="my_batches",
    )
    repo = deps.get_batch_repository(settings)

    assert isinstance(repo, FirestoreBatchRepository)
    assert isinstance(repo, BatchRepository)  # satisfies the contract
    assert captured == {"project": "proj-x", "database": "db-x"}
    assert repo._collection_name == "my_batches"


def test_get_batch_storage_builds_gcs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The provider builds the GCS-backed storage from settings, using the
    # configured bucket and the (shared) GCP project — without a live client.
    captured: dict[str, object] = {}

    def fake_client(project: str | None) -> object:
        captured["project"] = project
        return object()  # stand-in Storage client

    monkeypatch.setattr(deps, "_build_storage_client", fake_client)

    settings = Settings(firestore_project="proj-x", storage_bucket="my-bucket")
    storage = deps.get_batch_storage(settings)

    assert isinstance(storage, GCSBatchStorage)
    assert isinstance(storage, BatchStorage)  # satisfies the contract
    assert captured == {"project": "proj-x"}
    assert storage._bucket_name == "my-bucket"


def test_get_user_id_rejects_blank() -> None:
    # Direct unit check of the header dependency's trim-and-reject behaviour.
    from fastapi import HTTPException

    assert deps.get_user_id("  user-1  ") == "user-1"
    with pytest.raises(HTTPException) as exc:
        deps.get_user_id("   ")
    assert exc.value.status_code == 422
