"""Tests for the recurring batch-processing job."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mf_service.domain import Batch, BatchStatus
from mf_service.jobs import process_next_batch
from mf_service.processing import PipelineContext
from mf_service.repository import InMemoryBatchRepository
from mf_service.storage import InMemoryStorageGateway


class _OutputTask:
    """A pipeline task that writes a fixed output file, no engine needed."""

    def run(self, ctx: PipelineContext) -> None:
        ctx.outputs["annotations.csv"] = b"Feature ID\r\n1\r\n"


class _BoomTask:
    def run(self, ctx: PipelineContext) -> None:
        raise RuntimeError("pipeline exploded")


def _created_batch(repo: InMemoryBatchRepository, name: str, created=None) -> Batch:
    """Create a batch and advance it to ``created`` (upload done)."""
    batch = repo.create(Batch(user_id="u1", name=name, created=created or _now()))
    repo.update_status(batch.id, BatchStatus.CREATED)
    return batch


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_process_next_returns_none_when_queue_empty():
    repo = InMemoryBatchRepository()
    storage = InMemoryStorageGateway()
    assert process_next_batch(repo, storage, None, pipeline=(_OutputTask(),)) is None


def test_process_next_processes_oldest_and_uploads_output():
    repo = InMemoryBatchRepository()
    storage = InMemoryStorageGateway()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older = _created_batch(repo, "older", created=base)
    _created_batch(repo, "newer", created=base + timedelta(hours=1))

    handled = process_next_batch(repo, storage, None, pipeline=(_OutputTask(),))

    assert handled is not None
    assert handled.id == older.id  # oldest created batch picked first
    assert handled.status is BatchStatus.PROCESSED
    # The output landed in storage under the batch prefix.
    assert storage.objects[f"{older.id}/annotations.csv"] == b"Feature ID\r\n1\r\n"
    assert repo.get("u1", older.id).status is BatchStatus.PROCESSED


def test_process_next_only_claims_created_batches():
    repo = InMemoryBatchRepository()
    storage = InMemoryStorageGateway()
    # A batch still 'creating' (upload not finished) must not be claimed.
    repo.create(Batch(user_id="u1", name="pending"))
    assert process_next_batch(repo, storage, None, pipeline=(_OutputTask(),)) is None


def test_process_next_marks_error_on_pipeline_failure():
    repo = InMemoryBatchRepository()
    storage = InMemoryStorageGateway()
    batch = _created_batch(repo, "b")

    handled = process_next_batch(repo, storage, None, pipeline=(_BoomTask(),))

    assert handled is not None
    assert handled.status is BatchStatus.ERROR
    assert handled.error_message == "pipeline exploded"
    # Nothing was uploaded for a failed run.
    assert storage.objects == {}


def test_process_next_output_is_listable_and_downloadable():
    repo = InMemoryBatchRepository()
    storage = InMemoryStorageGateway()
    batch = _created_batch(repo, "b")

    process_next_batch(repo, storage, None, pipeline=(_OutputTask(),))

    names = {o.name for o in storage.list(f"{batch.id}/")}
    assert "annotations.csv" in names
    assert storage.download(f"{batch.id}/annotations.csv") == b"Feature ID\r\n1\r\n"


def test_process_next_deleted_mid_processing_is_noop(monkeypatch):
    # The batch is claimed, then deleted before the PROCESSED write: the
    # resulting BatchNotFoundError is swallowed and the job returns None.
    from mf_service.repository import BatchNotFoundError

    repo = InMemoryBatchRepository()
    storage = InMemoryStorageGateway()
    batch = _created_batch(repo, "b")

    real_update = repo.update_status

    def _update(batch_id, status, *, error_message=None):
        if status is BatchStatus.PROCESSED:
            raise BatchNotFoundError(batch_id)  # vanished before completion
        return real_update(batch_id, status, error_message=error_message)

    monkeypatch.setattr(repo, "update_status", _update)
    assert process_next_batch(repo, storage, None, pipeline=(_OutputTask(),)) is None


def test_process_next_failure_on_deleted_batch_is_noop(monkeypatch):
    # The pipeline fails AND the batch is gone when we try to record the error:
    # the ERROR write raises BatchNotFoundError, which is swallowed too.
    from mf_service.repository import BatchNotFoundError

    repo = InMemoryBatchRepository()
    storage = InMemoryStorageGateway()
    batch = _created_batch(repo, "b")

    def _update(batch_id, status, *, error_message=None):
        raise BatchNotFoundError(batch_id)  # batch vanished before the ERROR write

    monkeypatch.setattr(repo, "update_status", _update)
    assert process_next_batch(repo, storage, None, pipeline=(_BoomTask(),)) is None
