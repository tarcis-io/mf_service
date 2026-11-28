"""Tests for the in-memory BatchRepository and its domain errors."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from mf_service.domain import Batch, BatchStatus
from mf_service.repository import (
    BatchNotFoundError,
    BatchNotProcessableError,
    BatchRepository,
    DuplicateBatchNameError,
    InMemoryBatchRepository,
)

LATER = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo() -> InMemoryBatchRepository:
    return InMemoryBatchRepository()


def test_in_memory_satisfies_the_protocol(repo: InMemoryBatchRepository) -> None:
    # The runtime_checkable Protocol: the concrete impl is a BatchRepository.
    assert isinstance(repo, BatchRepository)


# --- create ------------------------------------------------------------------


def test_create_persists_and_returns_the_batch(
    repo: InMemoryBatchRepository,
) -> None:
    batch = Batch(user_id="u1", name="b1")
    created = repo.create(batch)
    assert created.id == batch.id
    assert repo.get("u1", batch.id).name == "b1"


def test_returned_batches_are_snapshots(repo: InMemoryBatchRepository) -> None:
    # Mutating a returned batch must not leak into the store (a real backend
    # returns fresh objects, so callers can't hold a live reference).
    batch = repo.create(Batch(user_id="u1", name="b1"))
    batch.name = "mutated"
    assert repo.get("u1", batch.id).name == "b1"


def test_snapshot_preserves_a_distinct_updated(
    repo: InMemoryBatchRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: `updated` is init=False, so a naive dataclasses.replace() copy
    # would reset it to `created`. A status change must survive get()/list().
    batch = repo.create(Batch(user_id="u1", name="b1"))
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: LATER)
    repo.update_status(batch.id, BatchStatus.CREATED)
    fetched = repo.get("u1", batch.id)
    assert fetched.updated == LATER
    assert fetched.updated != fetched.created


def test_create_rejects_duplicate_name_for_same_user(
    repo: InMemoryBatchRepository,
) -> None:
    repo.create(Batch(user_id="u1", name="dup"))
    with pytest.raises(DuplicateBatchNameError):
        repo.create(Batch(user_id="u1", name="dup"))


def test_create_allows_same_name_for_different_users(
    repo: InMemoryBatchRepository,
) -> None:
    a = repo.create(Batch(user_id="u1", name="same"))
    b = repo.create(Batch(user_id="u2", name="same"))
    assert a.id != b.id


def test_name_uniqueness_is_case_sensitive(
    repo: InMemoryBatchRepository,
) -> None:
    # Uniqueness is an exact match, so names differing only in case are distinct
    # ("Run" and "run" can coexist). Pins the behaviour as a deliberate choice.
    a = repo.create(Batch(user_id="u1", name="Run"))
    b = repo.create(Batch(user_id="u1", name="run"))
    assert a.id != b.id
    assert {x.name for x in repo.list_for_user("u1")} == {"Run", "run"}


def test_concurrent_create_of_same_name_lets_exactly_one_win(
    repo: InMemoryBatchRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lock exists to make check-and-write atomic. Fire many threads creating
    # the same (user, name) at once, aligned on a barrier to maximise contention:
    # exactly one must win and the rest must raise DuplicateBatchNameError.
    #
    # A sleep is injected into the name check so it straddles a scheduler yield
    # (CPython's GIL otherwise hides the check-then-write gap). With the lock the
    # check runs inside it, so threads still serialise and one wins; remove the
    # lock and every thread clears the check before any write, so all would win —
    # which is exactly what this test would then catch.
    real_name_taken = repo._name_taken

    def slow_name_taken(*args: object, **kwargs: object) -> bool:
        taken = real_name_taken(*args, **kwargs)  # type: ignore[arg-type]
        time.sleep(0.01)
        return taken

    monkeypatch.setattr(repo, "_name_taken", slow_name_taken)

    n = 16
    barrier = threading.Barrier(n)
    successes: list[Batch] = []
    conflicts: list[DuplicateBatchNameError] = []
    guard = threading.Lock()

    def worker() -> None:
        barrier.wait()  # release all threads into create() together
        try:
            created = repo.create(Batch(user_id="u1", name="dup"))
        except DuplicateBatchNameError as exc:
            with guard:
                conflicts.append(exc)
        else:
            with guard:
                successes.append(created)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1
    assert len(conflicts) == n - 1
    # And the store really holds just the one winning batch.
    assert len(repo.list_for_user("u1")) == 1


# --- get ---------------------------------------------------------------------


def test_get_returns_the_owned_batch(repo: InMemoryBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    assert repo.get("u1", batch.id).id == batch.id


def test_get_missing_id_raises_not_found(repo: InMemoryBatchRepository) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.get("u1", "does-not-exist")


def test_get_other_users_batch_raises_not_found(
    repo: InMemoryBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    # Another user may not see it — it reads as not found, not forbidden.
    with pytest.raises(BatchNotFoundError):
        repo.get("u2", batch.id)


# --- list_for_user -----------------------------------------------------------


def test_list_is_empty_for_unknown_user(repo: InMemoryBatchRepository) -> None:
    assert repo.list_for_user("nobody") == []


def test_list_returns_only_the_users_batches(
    repo: InMemoryBatchRepository,
) -> None:
    repo.create(Batch(user_id="u1", name="a"))
    repo.create(Batch(user_id="u1", name="b"))
    repo.create(Batch(user_id="u2", name="c"))
    names = {b.name for b in repo.list_for_user("u1")}
    assert names == {"a", "b"}


def test_list_orders_newest_first(repo: InMemoryBatchRepository) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older = Batch(user_id="u1", name="older", created=base)
    newer = Batch(user_id="u1", name="newer", created=base + timedelta(hours=1))
    repo.create(older)
    repo.create(newer)
    ordered = repo.list_for_user("u1")
    assert [b.name for b in ordered] == ["newer", "older"]


# --- rename ------------------------------------------------------------------


def test_rename_changes_the_name(repo: InMemoryBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="old"))
    renamed = repo.rename("u1", batch.id, "new")
    assert renamed.name == "new"
    assert repo.get("u1", batch.id).name == "new"


def test_rename_missing_batch_raises_not_found(
    repo: InMemoryBatchRepository,
) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.rename("u1", "missing", "new")


def test_rename_other_users_batch_raises_not_found(
    repo: InMemoryBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    with pytest.raises(BatchNotFoundError):
        repo.rename("u2", batch.id, "new")


def test_rename_to_an_existing_name_raises_duplicate(
    repo: InMemoryBatchRepository,
) -> None:
    repo.create(Batch(user_id="u1", name="taken"))
    other = repo.create(Batch(user_id="u1", name="free"))
    with pytest.raises(DuplicateBatchNameError):
        repo.rename("u1", other.id, "taken")


def test_rename_to_same_name_is_allowed(repo: InMemoryBatchRepository) -> None:
    # Renaming a batch to its own current name is a no-op, not a dup conflict
    # (the uniqueness check excludes the batch itself).
    batch = repo.create(Batch(user_id="u1", name="keep"))
    renamed = repo.rename("u1", batch.id, "keep")
    assert renamed.name == "keep"


def test_rename_does_not_bump_updated(repo: InMemoryBatchRepository) -> None:
    # updated tracks status changes; a rename is not one.
    batch = repo.create(Batch(user_id="u1", name="old"))
    before = batch.updated
    repo.rename("u1", batch.id, "new")
    assert repo.get("u1", batch.id).updated == before


# --- update_status (not user-scoped) ----------------------------------------


def test_update_status_changes_status_and_bumps_updated(
    repo: InMemoryBatchRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    before = batch.updated
    # Pin the clock so the bump is observable (back-to-back now() calls are
    # otherwise usually equal to the microsecond).
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: LATER)
    updated = repo.update_status(batch.id, BatchStatus.CREATED)
    assert updated.status is BatchStatus.CREATED
    assert updated.updated == LATER
    assert updated.updated != before


def test_update_status_is_not_user_scoped(
    repo: InMemoryBatchRepository,
) -> None:
    # update_status takes only the batch id (server-side work), no user_id.
    batch = repo.create(Batch(user_id="u1", name="b1"))
    updated = repo.update_status(batch.id, BatchStatus.PROCESSING)
    assert updated.status is BatchStatus.PROCESSING


def test_update_status_missing_batch_raises_not_found(
    repo: InMemoryBatchRepository,
) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.update_status("missing", BatchStatus.CREATED)


def test_update_status_sets_and_clears_error_message(
    repo: InMemoryBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    repo.update_status(batch.id, BatchStatus.ERROR, error_message="boom")
    assert repo.get("u1", batch.id).error_message == "boom"
    # Any non-error status clears it.
    repo.update_status(batch.id, BatchStatus.PROCESSED)
    assert repo.get("u1", batch.id).error_message is None


def test_update_status_allows_any_transition(
    repo: InMemoryBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    repo.update_status(batch.id, BatchStatus.PROCESSED)
    # Jump straight back to creating — permitted (no lifecycle enforcement).
    out = repo.update_status(batch.id, BatchStatus.CREATING)
    assert out.status is BatchStatus.CREATING


# --- claim_for_processing / claim_oldest_created (not user-scoped) -----------


def _created_batch(
    repo: InMemoryBatchRepository, user_id: str = "u1", name: str = "b1"
) -> Batch:
    """Create a batch and move it to the 'created' status."""
    batch = repo.create(Batch(user_id=user_id, name=name))
    return repo.update_status(batch.id, BatchStatus.CREATED)


def test_claim_for_processing_flips_created_to_processing(
    repo: InMemoryBatchRepository,
) -> None:
    batch = _created_batch(repo)
    claimed = repo.claim_for_processing(batch.id)
    assert claimed.status is BatchStatus.PROCESSING
    # The flip is persisted.
    assert repo.get("u1", batch.id).status is BatchStatus.PROCESSING


def test_claim_for_processing_missing_raises_not_found(
    repo: InMemoryBatchRepository,
) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.claim_for_processing("missing")


def test_claim_for_processing_rejects_non_created_status(
    repo: InMemoryBatchRepository,
) -> None:
    # A batch still 'creating' (or already processing/processed/error) cannot be
    # claimed — only 'created' is processable.
    creating = repo.create(Batch(user_id="u1", name="fresh"))  # status creating
    with pytest.raises(BatchNotProcessableError):
        repo.claim_for_processing(creating.id)
    # And a 'processing' batch can't be claimed twice.
    claimed = repo.claim_for_processing(_created_batch(repo, name="b2").id)
    with pytest.raises(BatchNotProcessableError):
        repo.claim_for_processing(claimed.id)


def test_claim_for_processing_is_not_user_scoped(
    repo: InMemoryBatchRepository,
) -> None:
    # Processing is global: a batch owned by any user can be claimed by id.
    batch = _created_batch(repo, user_id="someone-else")
    assert repo.claim_for_processing(batch.id).status is BatchStatus.PROCESSING


def test_claim_oldest_created_returns_none_when_empty(
    repo: InMemoryBatchRepository,
) -> None:
    # No batches at all, and a non-created batch is not eligible.
    assert repo.claim_oldest_created() is None
    repo.create(Batch(user_id="u1", name="creating-only"))  # status creating
    assert repo.claim_oldest_created() is None


def test_claim_oldest_created_picks_oldest_and_flips(
    repo: InMemoryBatchRepository,
) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older = repo.create(Batch(user_id="u1", name="older", created=base))
    newer = repo.create(
        Batch(user_id="u2", name="newer", created=base + timedelta(hours=1))
    )
    repo.update_status(older.id, BatchStatus.CREATED)
    repo.update_status(newer.id, BatchStatus.CREATED)
    claimed = repo.claim_oldest_created()
    assert claimed is not None
    assert claimed.id == older.id  # oldest by created, across users
    assert claimed.status is BatchStatus.PROCESSING
    # Claiming again now returns the next oldest (the first is no longer created).
    second = repo.claim_oldest_created()
    assert second is not None and second.id == newer.id


# --- fail_timed_out ----------------------------------------------------------


def _at_status(
    repo: InMemoryBatchRepository,
    monkeypatch: pytest.MonkeyPatch,
    status: BatchStatus,
    when: datetime,
    *,
    name: str,
) -> Batch:
    """Create a batch and drive it to ``status`` with ``updated`` pinned to ``when``."""
    batch = repo.create(Batch(user_id="u1", name=name))
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: when)
    return repo.update_status(batch.id, status)


def test_fail_timed_out_sweeps_old_in_progress_batches(
    repo: InMemoryBatchRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Two stuck batches (creating + processing) stamped 'old'.
    creating = _at_status(repo, monkeypatch, BatchStatus.CREATING, old, name="c")
    processing = _at_status(
        repo, monkeypatch, BatchStatus.PROCESSING, old, name="p"
    )
    cutoff = old + timedelta(hours=1)
    # Advance the clock so the sweep's error-flip stamps a fresh 'updated'.
    now = old + timedelta(hours=2)
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: now)

    timed_out = repo.fail_timed_out(cutoff, error_message="timed out")

    assert {b.id for b in timed_out} == {creating.id, processing.id}
    for b in timed_out:
        assert b.status is BatchStatus.ERROR
        assert b.error_message == "timed out"
        assert b.updated == now  # the flip re-stamped updated
    # Persisted.
    assert repo.get("u1", creating.id).status is BatchStatus.ERROR


def test_fail_timed_out_ignores_recent_and_terminal_batches(
    repo: InMemoryBatchRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    recent = old + timedelta(hours=2)
    cutoff = old + timedelta(hours=1)

    # In-progress but recent (updated after cutoff) — not swept.
    _at_status(repo, monkeypatch, BatchStatus.PROCESSING, recent, name="recent")
    # Terminal statuses, stamped old — not in-progress, so never swept.
    _at_status(repo, monkeypatch, BatchStatus.CREATED, old, name="created")
    _at_status(repo, monkeypatch, BatchStatus.PROCESSED, old, name="done")
    _at_status(repo, monkeypatch, BatchStatus.ERROR, old, name="errored")

    assert repo.fail_timed_out(cutoff, error_message="x") == []


def test_fail_timed_out_empty_when_nothing_stuck(
    repo: InMemoryBatchRepository,
) -> None:
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert repo.fail_timed_out(cutoff, error_message="x") == []


# --- delete ------------------------------------------------------------------


def test_delete_removes_the_batch(repo: InMemoryBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    repo.delete("u1", batch.id)
    with pytest.raises(BatchNotFoundError):
        repo.get("u1", batch.id)


def test_delete_missing_batch_raises_not_found(
    repo: InMemoryBatchRepository,
) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.delete("u1", "missing")


def test_delete_other_users_batch_raises_not_found(
    repo: InMemoryBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    with pytest.raises(BatchNotFoundError):
        repo.delete("u2", batch.id)
    # Still present for its real owner.
    assert repo.get("u1", batch.id).id == batch.id
