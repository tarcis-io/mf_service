"""Tests for the Batch domain entity."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from mf_service.domain import Batch, BatchStatus

LATER = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_new_batch_has_generated_id_and_timestamps() -> None:
    batch = Batch(user_id="u1", name="b1")
    # id is a valid UUID4 string.
    parsed = uuid.UUID(batch.id)
    assert parsed.version == 4
    # created/updated are timezone-aware UTC and equal at birth.
    assert batch.created.tzinfo is timezone.utc
    assert batch.updated.tzinfo is timezone.utc
    assert batch.updated == batch.created


def test_new_batch_defaults_to_creating_with_no_error() -> None:
    batch = Batch(user_id="u1", name="b1")
    assert batch.status is BatchStatus.CREATING
    assert batch.error_message is None


def test_ids_are_unique_across_instances() -> None:
    ids = {Batch(user_id="u1", name=f"b{i}").id for i in range(100)}
    assert len(ids) == 100


def test_status_values_in_lifecycle_order() -> None:
    assert [s.value for s in BatchStatus] == [
        "creating",
        "created",
        "processing",
        "processed",
        "error",
    ]


def test_set_status_advances_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = Batch(user_id="u1", name="b1")
    created_at = batch.created
    original_updated = batch.updated
    # Pin the clock so the bump is observable (back-to-back now() calls are
    # otherwise usually equal to the microsecond).
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: LATER)
    batch.set_status(BatchStatus.CREATED)
    assert batch.status is BatchStatus.CREATED
    assert batch.created == created_at  # creation time never moves
    assert batch.updated == LATER
    assert batch.updated != original_updated


def test_set_status_error_stores_message() -> None:
    batch = Batch(user_id="u1", name="b1")
    batch.set_status(BatchStatus.ERROR, error_message="boom")
    assert batch.status is BatchStatus.ERROR
    assert batch.error_message == "boom"


def test_set_status_clears_error_message_on_non_error() -> None:
    batch = Batch(user_id="u1", name="b1")
    batch.set_status(BatchStatus.ERROR, error_message="boom")
    # Moving away from error clears the message, even if one is passed.
    batch.set_status(BatchStatus.PROCESSED, error_message="ignored")
    assert batch.error_message is None


def test_set_status_allows_any_transition() -> None:
    # No lifecycle enforcement: a "backwards" or skipping move is permitted.
    batch = Batch(user_id="u1", name="b1")
    batch.set_status(BatchStatus.PROCESSED)
    batch.set_status(BatchStatus.CREATING)
    assert batch.status is BatchStatus.CREATING
