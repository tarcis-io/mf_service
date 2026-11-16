"""Tests for the background batch-upload task."""

from __future__ import annotations

import pytest

from mf_service.domain import Batch, BatchStatus
from mf_service.repository import InMemoryBatchRepository
from mf_service.storage import InMemoryStorageGateway
from mf_service.uploads import REQUIRED_FILES, upload_batch_files


@pytest.fixture
def repo() -> InMemoryBatchRepository:
    return InMemoryBatchRepository()


@pytest.fixture
def storage() -> InMemoryStorageGateway:
    return InMemoryStorageGateway()


def _payload() -> dict[str, bytes]:
    return {name: f"{name} bytes".encode() for name in REQUIRED_FILES}


def test_upload_writes_all_files_and_marks_created(repo, storage):
    batch = repo.create(Batch(user_id="u1", name="b"))
    upload_batch_files(repo, storage, batch.id, _payload())

    for name in REQUIRED_FILES:
        assert storage.objects[f"{batch.id}/{name}"] == f"{name} bytes".encode()
    assert repo.get("u1", batch.id).status is BatchStatus.CREATED
    assert repo.get("u1", batch.id).error_message is None


def test_upload_derives_tsv_content_type(repo, storage):
    batch = repo.create(Batch(user_id="u1", name="b"))
    upload_batch_files(repo, storage, batch.id, _payload())

    assert storage.content_types[f"{batch.id}/mzmine_metadata.csv"] == "text/csv"
    assert (
        storage.content_types[f"{batch.id}/gnps_merged_results.tsv"]
        == "text/tab-separated-values"
    )


def test_upload_failure_marks_error_with_message(repo):
    batch = repo.create(Batch(user_id="u1", name="b"))

    class _BoomStorage:
        def upload(self, path, data, *, content_type):
            raise RuntimeError("bucket exploded")

    upload_batch_files(repo, _BoomStorage(), batch.id, _payload())

    stored = repo.get("u1", batch.id)
    assert stored.status is BatchStatus.ERROR
    assert stored.error_message == "bucket exploded"


def test_upload_on_deleted_batch_is_noop(storage):
    # The batch never existed (or was deleted first): update_status raises
    # BatchNotFoundError, which the task swallows — nothing is uploaded, no
    # error is recorded against a nonexistent batch.
    repo = InMemoryBatchRepository()
    upload_batch_files(repo, storage, "ghost-id", _payload())
    assert storage.objects == {}


def test_upload_failure_on_deleted_batch_is_noop(monkeypatch):
    # Storage fails AND the batch is gone when we try to record the error: the
    # second BatchNotFoundError is swallowed too (no exception escapes the task).
    from mf_service.repository import BatchNotFoundError

    class _BoomStorage:
        def upload(self, path, data, *, content_type):
            raise RuntimeError("boom")

    repo = InMemoryBatchRepository()
    batch = repo.create(Batch(user_id="u1", name="b"))

    # The CREATING write succeeds; before the ERROR write the batch "vanishes",
    # so update_status then raises. A counter drives the two-phase behaviour.
    calls = {"n": 0}
    real_update = repo.update_status

    def _update(batch_id, status, *, error_message=None):
        calls["n"] += 1
        if calls["n"] == 1:  # the CREATING write succeeds
            return real_update(batch_id, status, error_message=error_message)
        raise BatchNotFoundError(batch_id)  # ERROR write: batch is gone

    monkeypatch.setattr(repo, "update_status", _update)
    upload_batch_files(repo, _BoomStorage(), batch.id, _payload())
    assert calls["n"] == 2  # attempted CREATING then ERROR, both handled
