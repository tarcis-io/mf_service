"""Tests for the in-memory BatchRepository and its domain errors."""

from __future__ import annotations

import pytest

from mf_service.domain import Batch, BatchStatus
from mf_service.repository import (
    BatchNotFoundError,
    BatchRepository,
    DuplicateBatchNameError,
    InMemoryBatchRepository,
)


@pytest.fixture
def repo() -> InMemoryBatchRepository:
    return InMemoryBatchRepository()


def test_in_memory_satisfies_the_protocol(repo: InMemoryBatchRepository):
    # The runtime-checkable Protocol lets us assert the fake is a valid repo.
    assert isinstance(repo, BatchRepository)


def test_create_and_get_roundtrip(repo: InMemoryBatchRepository):
    created = repo.create(Batch(user_id="u1", name="alpha"))
    fetched = repo.get("u1", created.id)
    assert fetched.id == created.id
    assert fetched.name == "alpha"
    assert fetched.status is BatchStatus.CREATED
    assert fetched.error_message is None


def test_create_rejects_duplicate_name_for_same_user(repo: InMemoryBatchRepository):
    repo.create(Batch(user_id="u1", name="dup"))
    with pytest.raises(DuplicateBatchNameError):
        repo.create(Batch(user_id="u1", name="dup"))


def test_same_name_allowed_for_different_users(repo: InMemoryBatchRepository):
    a = repo.create(Batch(user_id="u1", name="shared"))
    b = repo.create(Batch(user_id="u2", name="shared"))
    assert a.id != b.id  # names are unique per user, not globally


def test_get_is_scoped_to_owner(repo: InMemoryBatchRepository):
    created = repo.create(Batch(user_id="u1", name="mine"))
    # Another user may not read it — it reads as not found, not forbidden.
    with pytest.raises(BatchNotFoundError):
        repo.get("u2", created.id)


def test_get_missing_raises(repo: InMemoryBatchRepository):
    with pytest.raises(BatchNotFoundError):
        repo.get("u1", "no-such-id")


def test_list_returns_only_owner_batches_newest_first(repo: InMemoryBatchRepository):
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older = Batch(user_id="u1", name="older", created=base)
    newer = Batch(user_id="u1", name="newer", created=base + timedelta(hours=1))
    repo.create(older)
    repo.create(newer)
    repo.create(Batch(user_id="u2", name="other-user"))

    listed = repo.list_for_user("u1")
    assert [b.name for b in listed] == ["newer", "older"]


def test_rename_changes_name(repo: InMemoryBatchRepository):
    created = repo.create(Batch(user_id="u1", name="before"))
    renamed = repo.rename("u1", created.id, "after")
    assert renamed.name == "after"
    assert repo.get("u1", created.id).name == "after"


def test_rename_rejects_duplicate_name(repo: InMemoryBatchRepository):
    repo.create(Batch(user_id="u1", name="taken"))
    other = repo.create(Batch(user_id="u1", name="free"))
    with pytest.raises(DuplicateBatchNameError):
        repo.rename("u1", other.id, "taken")


def test_rename_to_same_name_is_allowed(repo: InMemoryBatchRepository):
    # Renaming a batch to the name it already has must not trip the duplicate
    # guard against itself.
    created = repo.create(Batch(user_id="u1", name="stable"))
    renamed = repo.rename("u1", created.id, "stable")
    assert renamed.name == "stable"


def test_rename_missing_raises(repo: InMemoryBatchRepository):
    with pytest.raises(BatchNotFoundError):
        repo.rename("u1", "no-such-id", "whatever")


def test_rename_is_scoped_to_owner(repo: InMemoryBatchRepository):
    created = repo.create(Batch(user_id="u1", name="mine"))
    with pytest.raises(BatchNotFoundError):
        repo.rename("u2", created.id, "hijack")


def test_delete_removes_batch(repo: InMemoryBatchRepository):
    created = repo.create(Batch(user_id="u1", name="temp"))
    repo.delete("u1", created.id)
    with pytest.raises(BatchNotFoundError):
        repo.get("u1", created.id)


def test_delete_frees_the_name(repo: InMemoryBatchRepository):
    # After deletion the name is available again for the same user.
    created = repo.create(Batch(user_id="u1", name="recycle"))
    repo.delete("u1", created.id)
    repo.create(Batch(user_id="u1", name="recycle"))  # must not raise


def test_delete_missing_raises(repo: InMemoryBatchRepository):
    with pytest.raises(BatchNotFoundError):
        repo.delete("u1", "no-such-id")


def test_delete_is_scoped_to_owner(repo: InMemoryBatchRepository):
    created = repo.create(Batch(user_id="u1", name="mine"))
    with pytest.raises(BatchNotFoundError):
        repo.delete("u2", created.id)
