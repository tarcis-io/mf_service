"""Tests for FirestoreBatchRepository against a fake Firestore client.

The repository imports ``firestore.transactional``, ``FieldFilter`` and
``Query`` lazily from ``google.cloud.firestore``. The ``fake_firestore`` fixture
installs a minimal in-memory stand-in for that module (and provides a matching
fake client), so the full repository contract is exercised without the real
library or a live connection — the same way the route tests avoid GCP.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from mf_service.domain import Batch, BatchStatus
from mf_service.repository import (
    BatchNotFoundError,
    BatchNotProcessableError,
    DuplicateBatchNameError,
    FirestoreBatchRepository,
)

# --- a tiny in-memory fake of the Firestore surface the repo uses ------------


class _Snapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _DocumentRef:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store = store
        self.id = doc_id

    def get(self, transaction: Any = None) -> _Snapshot:
        data = self._store.get(self.id)
        return _Snapshot(self.id, data)


def _matches(value: Any, op: str, target: Any) -> bool:
    """Apply a Firestore filter operator the repo uses (``==`` or ``<``)."""
    if op == "<":
        return value is not None and value < target
    return value == target  # "=="


class _Query:
    def __init__(
        self,
        store: dict[str, dict[str, Any]],
        predicates: list[tuple[str, str, Any]],
    ) -> None:
        self._store = store
        self._predicates = predicates

    def where(self, *, filter: _FieldFilter) -> _Query:  # noqa: A002
        return _Query(
            self._store,
            [*self._predicates, (filter.field, filter.op, filter.value)],
        )

    def order_by(self, field: str, direction: str = "ASC") -> _Query:
        q = _Query(self._store, self._predicates)
        q._order = (field, direction)  # type: ignore[attr-defined]
        q._limit = getattr(self, "_limit", None)  # type: ignore[attr-defined]
        return q

    def limit(self, count: int) -> _Query:
        q = _Query(self._store, self._predicates)
        q._order = getattr(self, "_order", None)  # type: ignore[attr-defined]
        q._limit = count  # type: ignore[attr-defined]
        return q

    def stream(self, transaction: Any = None) -> Iterator[_Snapshot]:
        rows = [
            (doc_id, data)
            for doc_id, data in self._store.items()
            if all(
                _matches(data.get(f), op, v) for f, op, v in self._predicates
            )
        ]
        order = getattr(self, "_order", None)
        if order is not None:
            field, direction = order
            rows.sort(key=lambda r: r[1][field], reverse=direction == "DESCENDING")
        limit = getattr(self, "_limit", None)
        if limit is not None:
            rows = rows[:limit]
        return iter(_Snapshot(doc_id, data) for doc_id, data in rows)


class _Collection(_Query):
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        super().__init__(store, [])

    def document(self, doc_id: str) -> _DocumentRef:
        return _DocumentRef(self._store, doc_id)


class _Transaction:
    """Applies writes immediately — enough to exercise the repo's bodies."""

    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def set(self, ref: _DocumentRef, data: dict[str, Any]) -> None:
        self._store[ref.id] = dict(data)

    def update(self, ref: _DocumentRef, data: dict[str, Any]) -> None:
        self._store[ref.id].update(data)

    def delete(self, ref: _DocumentRef) -> None:
        self._store.pop(ref.id, None)


class _FakeClient:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _Collection:
        return _Collection(self._store)

    def transaction(self) -> _Transaction:
        return _Transaction(self._store)


class _FieldFilter:
    def __init__(self, field: str, op: str, value: Any) -> None:
        self.field = field
        self.op = op
        self.value = value


@pytest.fixture
def fake_firestore() -> Iterator[None]:
    """Install a fake ``google.cloud.firestore`` module for the test."""
    module = types.ModuleType("google.cloud.firestore")
    module.transactional = lambda fn: fn  # pass-through decorator
    module.FieldFilter = _FieldFilter
    module.Query = types.SimpleNamespace(ASCENDING="ASCENDING", DESCENDING="DESCENDING")

    google = sys.modules.get("google") or types.ModuleType("google")
    cloud = sys.modules.get("google.cloud") or types.ModuleType("google.cloud")
    saved = {
        name: sys.modules.get(name)
        for name in ("google", "google.cloud", "google.cloud.firestore")
    }
    cloud.firestore = module  # type: ignore[attr-defined]
    sys.modules["google"] = google
    sys.modules["google.cloud"] = cloud
    sys.modules["google.cloud.firestore"] = module
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


@pytest.fixture
def repo(fake_firestore: None) -> FirestoreBatchRepository:
    return FirestoreBatchRepository(_FakeClient(), collection="batches")


# --- create ------------------------------------------------------------------


def test_create_persists_and_is_retrievable(
    repo: FirestoreBatchRepository,
) -> None:
    batch = Batch(user_id="u1", name="b1")
    created = repo.create(batch)
    assert created.id == batch.id
    fetched = repo.get("u1", batch.id)
    assert fetched.name == "b1"
    assert fetched.status is BatchStatus.CREATING


def test_create_round_trips_timestamps_and_fields(
    repo: FirestoreBatchRepository,
) -> None:
    batch = Batch(user_id="u1", name="b1")
    repo.create(batch)
    fetched = repo.get("u1", batch.id)
    # created and updated survive the Firestore round-trip as UTC datetimes.
    assert fetched.created == batch.created
    assert fetched.updated == batch.updated
    assert fetched.error_message is None


def test_create_duplicate_name_same_user_raises(
    repo: FirestoreBatchRepository,
) -> None:
    repo.create(Batch(user_id="u1", name="dup"))
    with pytest.raises(DuplicateBatchNameError):
        repo.create(Batch(user_id="u1", name="dup"))


def test_create_same_name_different_users_both_succeed(
    repo: FirestoreBatchRepository,
) -> None:
    a = repo.create(Batch(user_id="u1", name="same"))
    b = repo.create(Batch(user_id="u2", name="same"))
    assert a.id != b.id


# --- get ---------------------------------------------------------------------


def test_get_missing_raises(repo: FirestoreBatchRepository) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.get("u1", "missing")


def test_get_other_users_batch_raises(repo: FirestoreBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    with pytest.raises(BatchNotFoundError):
        repo.get("u2", batch.id)


# --- list --------------------------------------------------------------------


def test_list_empty(repo: FirestoreBatchRepository) -> None:
    assert repo.list_for_user("nobody") == []


def test_list_scoped_and_newest_first(repo: FirestoreBatchRepository) -> None:
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo.create(Batch(user_id="u1", name="older", created=base))
    repo.create(
        Batch(user_id="u1", name="newer", created=base + timedelta(hours=1))
    )
    repo.create(Batch(user_id="u2", name="theirs"))
    names = [b.name for b in repo.list_for_user("u1")]
    assert names == ["newer", "older"]


# --- rename ------------------------------------------------------------------


def test_rename_changes_name(repo: FirestoreBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="old"))
    renamed = repo.rename("u1", batch.id, "new")
    assert renamed.name == "new"
    assert repo.get("u1", batch.id).name == "new"


def test_rename_missing_raises(repo: FirestoreBatchRepository) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.rename("u1", "missing", "new")


def test_rename_other_user_raises(repo: FirestoreBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    with pytest.raises(BatchNotFoundError):
        repo.rename("u2", batch.id, "new")


def test_rename_to_existing_name_raises(repo: FirestoreBatchRepository) -> None:
    repo.create(Batch(user_id="u1", name="taken"))
    other = repo.create(Batch(user_id="u1", name="free"))
    with pytest.raises(DuplicateBatchNameError):
        repo.rename("u1", other.id, "taken")


def test_rename_to_same_name_is_noop(repo: FirestoreBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="keep"))
    assert repo.rename("u1", batch.id, "keep").name == "keep"


# --- update_status -----------------------------------------------------------


def test_update_status_sets_status_and_message(
    repo: FirestoreBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    updated = repo.update_status(
        batch.id, BatchStatus.ERROR, error_message="boom"
    )
    assert updated.status is BatchStatus.ERROR
    assert updated.error_message == "boom"
    assert repo.get("u1", batch.id).error_message == "boom"


def test_update_status_clears_message_on_non_error(
    repo: FirestoreBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    repo.update_status(batch.id, BatchStatus.ERROR, error_message="boom")
    repo.update_status(batch.id, BatchStatus.PROCESSED)
    assert repo.get("u1", batch.id).error_message is None


def test_update_status_advances_updated(
    repo: FirestoreBatchRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone

    batch = repo.create(Batch(user_id="u1", name="b1"))
    before = repo.get("u1", batch.id).updated
    later = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: later)
    repo.update_status(batch.id, BatchStatus.CREATED)
    assert repo.get("u1", batch.id).updated == later
    assert repo.get("u1", batch.id).updated != before


def test_update_status_missing_raises(repo: FirestoreBatchRepository) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.update_status("missing", BatchStatus.CREATED)


# --- claim_for_processing / claim_oldest_created -----------------------------


def test_claim_for_processing_flips_created(
    repo: FirestoreBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    repo.update_status(batch.id, BatchStatus.CREATED)
    claimed = repo.claim_for_processing(batch.id)
    assert claimed.status is BatchStatus.PROCESSING
    assert repo.get("u1", batch.id).status is BatchStatus.PROCESSING


def test_claim_for_processing_missing_raises(
    repo: FirestoreBatchRepository,
) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.claim_for_processing("missing")


def test_claim_for_processing_rejects_non_created(
    repo: FirestoreBatchRepository,
) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))  # status creating
    with pytest.raises(BatchNotProcessableError):
        repo.claim_for_processing(batch.id)


def test_claim_oldest_created_none_when_empty(
    repo: FirestoreBatchRepository,
) -> None:
    repo.create(Batch(user_id="u1", name="creating-only"))  # not created
    assert repo.claim_oldest_created() is None


def test_claim_oldest_created_picks_oldest_and_flips(
    repo: FirestoreBatchRepository,
) -> None:
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older = repo.create(Batch(user_id="u1", name="older", created=base))
    newer = repo.create(
        Batch(user_id="u2", name="newer", created=base + timedelta(hours=1))
    )
    repo.update_status(older.id, BatchStatus.CREATED)
    repo.update_status(newer.id, BatchStatus.CREATED)
    claimed = repo.claim_oldest_created()
    assert claimed is not None and claimed.id == older.id
    assert claimed.status is BatchStatus.PROCESSING
    # The older one is no longer 'created', so the next claim takes the newer.
    second = repo.claim_oldest_created()
    assert second is not None and second.id == newer.id


# --- fail_timed_out ----------------------------------------------------------


def test_fail_timed_out_sweeps_old_in_progress(
    repo: FirestoreBatchRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timedelta, timezone

    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # creating + processing stamped 'old' (pin the clock so updated == old).
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: old)
    c = repo.create(Batch(user_id="u1", name="c"))
    repo.update_status(c.id, BatchStatus.CREATING)
    p = repo.create(Batch(user_id="u2", name="p"))
    repo.update_status(p.id, BatchStatus.PROCESSING)
    # A recent processing batch (updated after the cutoff) must be left alone.
    recent = old + timedelta(hours=2)
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: recent)
    r = repo.create(Batch(user_id="u1", name="r"))
    repo.update_status(r.id, BatchStatus.PROCESSING)

    cutoff = old + timedelta(hours=1)
    timed_out = repo.fail_timed_out(cutoff, error_message="timed out")

    assert {b.id for b in timed_out} == {c.id, p.id}
    for b in timed_out:
        assert b.status is BatchStatus.ERROR
        assert b.error_message == "timed out"
    assert repo.get("u1", c.id).status is BatchStatus.ERROR
    assert repo.get("u1", r.id).status is BatchStatus.PROCESSING  # untouched


def test_fail_timed_out_empty_when_nothing_stuck(
    repo: FirestoreBatchRepository,
) -> None:
    from datetime import datetime, timezone

    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert repo.fail_timed_out(cutoff, error_message="x") == []


# --- delete ------------------------------------------------------------------


def test_delete_removes(repo: FirestoreBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    repo.delete("u1", batch.id)
    with pytest.raises(BatchNotFoundError):
        repo.get("u1", batch.id)


def test_delete_missing_raises(repo: FirestoreBatchRepository) -> None:
    with pytest.raises(BatchNotFoundError):
        repo.delete("u1", "missing")


def test_delete_other_user_raises(repo: FirestoreBatchRepository) -> None:
    batch = repo.create(Batch(user_id="u1", name="b1"))
    with pytest.raises(BatchNotFoundError):
        repo.delete("u2", batch.id)
    assert repo.get("u1", batch.id).id == batch.id


# --- document (de)serialisation ----------------------------------------------


def test_from_document_normalises_naive_timestamps_to_utc() -> None:
    # Firestore can hand back a naive datetime; it must be normalised to UTC so
    # the API always emits aware, consistent values.
    from datetime import datetime, timezone

    from mf_service.repository.firestore import _from_document

    naive = datetime(2026, 1, 1, 9, 30)  # no tzinfo
    batch = _from_document(
        {
            "id": "abc",
            "user_id": "u1",
            "name": "b1",
            "created": naive,
            "updated": naive,
            "status": "creating",
            "error_message": None,
        }
    )
    assert batch.created == naive.replace(tzinfo=timezone.utc)
    assert batch.created.tzinfo is timezone.utc
    assert batch.updated.tzinfo is timezone.utc


def test_to_document_round_trips_through_from_document() -> None:
    from mf_service.repository.firestore import _from_document, _to_document

    original = Batch(user_id="u1", name="b1")
    restored = _from_document(_to_document(original))
    assert restored.id == original.id
    assert restored.created == original.created
    assert restored.updated == original.updated
    assert restored.status is original.status


def test_from_document_none_fails_loudly() -> None:
    # Call sites guard with .exists, but if a document vanishes between the
    # existence check and the read (a TOCTOU race) to_dict() returns None;
    # _from_document must raise rather than build a half-empty batch.
    from mf_service.repository.firestore import _from_document

    with pytest.raises(ValueError):
        _from_document(None)
