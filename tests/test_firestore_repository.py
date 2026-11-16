"""Tests for FirestoreBatchRepository using an in-process fake client.

A live Firestore (or its emulator) is not needed here: a minimal fake
implements just the client surface the repository uses — collections,
documents, get/set/update/delete, a where/stream query, and the
``transaction`` / ``transactional`` pair. It executes the "transactional"
function immediately (single-threaded tests don't need real isolation), which
is enough to drive every branch of the repository and confirm the read-then-
write uniqueness logic. The pure serialisation helpers are tested directly.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from mf_service.domain import Batch, BatchStatus
from mf_service.firestore_repository import (
    FirestoreBatchRepository,
    _from_document,
    _name_key,
    _to_document,
)
from mf_service.repository import BatchNotFoundError, DuplicateBatchNameError

# --------------------------------------------------------------------------
# A tiny in-process fake of the Firestore client surface used by the repo.
# --------------------------------------------------------------------------


class _FakeSnapshot:
    def __init__(self, data: dict | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return self._data


class _FakeDocument:
    def __init__(self, collection: _FakeCollection, doc_id: str) -> None:
        self._collection = collection
        self.id = doc_id

    def get(self, transaction=None) -> _FakeSnapshot:
        return _FakeSnapshot(self._collection.store.get(self.id))


class _FakeFieldFilter:
    """Stand-in for ``firestore.FieldFilter(field, op, value)``."""

    def __init__(self, field: str, _op: str, value) -> None:
        self.field = field
        self.value = value


class _FakeQuery:
    def __init__(self, docs: dict, field: str, value) -> None:
        self._docs = docs
        self._field = field
        self._value = value
        self._order_by: str | None = None
        self._descending = False
        self._limit: int | None = None

    def order_by(self, field: str, direction: str | None = None) -> _FakeQuery:
        self._order_by = field
        self._descending = direction == _FakeQuery.DESCENDING
        return self

    def limit(self, count: int) -> _FakeQuery:
        self._limit = count
        return self

    # Mirror firestore.Query.DESCENDING / ASCENDING so the repo's direction=
    # argument resolves against the fake as it does against the real client.
    DESCENDING = "DESCENDING"
    ASCENDING = "ASCENDING"

    def stream(self):
        matched = [
            data
            for data in self._docs.values()
            if data.get(self._field) == self._value
        ]
        if self._order_by is not None:
            matched.sort(
                key=lambda d: d[self._order_by], reverse=self._descending
            )
        if self._limit is not None:
            matched = matched[: self._limit]
        for data in matched:
            yield _FakeSnapshot(data)


class _FakeCollection:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def document(self, doc_id: str) -> _FakeDocument:
        return _FakeDocument(self, doc_id)

    def where(self, *, filter: _FakeFieldFilter) -> _FakeQuery:
        return _FakeQuery(self.store, filter.field, filter.value)


class _FakeTransaction:
    """Records writes and applies them when the transactional fn returns."""

    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    def set(self, ref: _FakeDocument, data: dict) -> None:
        ref._collection.store[ref.id] = dict(data)

    def update(self, ref: _FakeDocument, data: dict) -> None:
        ref._collection.store[ref.id].update(data)

    def delete(self, ref: _FakeDocument) -> None:
        ref._collection.store.pop(ref.id, None)


class _FakeClient:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def collection(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection())

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)


def _fake_transactional(fn):
    """Stand-in for the module-level ``firestore.transactional`` decorator.

    The real decorator handles retries/commit; single-threaded tests only need
    it to call the wrapped function with the transaction it is given.
    """

    def _wrapped(txn):
        return fn(txn)

    return _wrapped


@pytest.fixture
def repo(monkeypatch) -> FirestoreBatchRepository:
    # The repository does `from google.cloud import firestore` and calls
    # firestore.transactional(...). Install a fake module exposing just that, so
    # no live Firestore package is needed.
    fake = types.ModuleType("google.cloud.firestore")
    fake.transactional = _fake_transactional
    fake.FieldFilter = _FakeFieldFilter
    fake.Query = _FakeQuery  # carries the DESCENDING sentinel used by order_by
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake)
    return FirestoreBatchRepository(
        _FakeClient(),
        batches_collection="batches",
        names_collection="batch_names",
    )


# --------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------


def test_name_key_is_deterministic_and_scoped():
    assert _name_key("u1", "n") == _name_key("u1", "n")
    assert _name_key("u1", "n") != _name_key("u2", "n")
    assert _name_key("u1", "a") != _name_key("u1", "b")


def test_document_roundtrip():
    batch = Batch(user_id="u1", name="n", status=BatchStatus.ERROR,
                  error_message="boom")
    restored = _from_document(_to_document(batch))
    assert restored.id == batch.id
    assert restored.status is BatchStatus.ERROR
    assert restored.error_message == "boom"


def test_from_document_treats_naive_timestamp_as_utc():
    data = {
        "id": "x", "user_id": "u", "name": "n",
        "created": datetime(2026, 1, 1, 12, 0, 0),  # naive
        "status": "created", "error_message": None,
    }
    restored = _from_document(data)
    assert restored.created.tzinfo is timezone.utc


# --------------------------------------------------------------------------
# Repository behaviour (via the fake client)
# --------------------------------------------------------------------------


def test_create_and_get(repo: FirestoreBatchRepository):
    created = repo.create(Batch(user_id="u1", name="alpha"))
    fetched = repo.get("u1", created.id)
    assert fetched.name == "alpha"


def test_create_duplicate_name_raises(repo: FirestoreBatchRepository):
    repo.create(Batch(user_id="u1", name="dup"))
    with pytest.raises(DuplicateBatchNameError):
        repo.create(Batch(user_id="u1", name="dup"))


def test_get_scoped_to_owner(repo: FirestoreBatchRepository):
    created = repo.create(Batch(user_id="u1", name="mine"))
    with pytest.raises(BatchNotFoundError):
        repo.get("u2", created.id)


def test_get_missing_raises(repo: FirestoreBatchRepository):
    with pytest.raises(BatchNotFoundError):
        repo.get("u1", "missing")


def test_list_newest_first_and_scoped(repo: FirestoreBatchRepository):
    from datetime import timedelta

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo.create(Batch(user_id="u1", name="old", created=base))
    repo.create(Batch(user_id="u1", name="new", created=base + timedelta(days=1)))
    repo.create(Batch(user_id="u2", name="other"))
    assert [b.name for b in repo.list_for_user("u1")] == ["new", "old"]


def test_rename(repo: FirestoreBatchRepository):
    created = repo.create(Batch(user_id="u1", name="before"))
    renamed = repo.rename("u1", created.id, "after")
    assert renamed.name == "after"
    assert repo.get("u1", created.id).name == "after"
    # Old name is freed, new name is reserved.
    repo.create(Batch(user_id="u1", name="before"))
    with pytest.raises(DuplicateBatchNameError):
        repo.create(Batch(user_id="u1", name="after"))


def test_rename_to_same_name_is_noop(repo: FirestoreBatchRepository):
    created = repo.create(Batch(user_id="u1", name="stable"))
    assert repo.rename("u1", created.id, "stable").name == "stable"


def test_rename_duplicate_raises(repo: FirestoreBatchRepository):
    repo.create(Batch(user_id="u1", name="taken"))
    other = repo.create(Batch(user_id="u1", name="free"))
    with pytest.raises(DuplicateBatchNameError):
        repo.rename("u1", other.id, "taken")


def test_rename_missing_raises(repo: FirestoreBatchRepository):
    with pytest.raises(BatchNotFoundError):
        repo.rename("u1", "missing", "x")


def test_rename_scoped_to_owner(repo: FirestoreBatchRepository):
    created = repo.create(Batch(user_id="u1", name="mine"))
    with pytest.raises(BatchNotFoundError):
        repo.rename("u2", created.id, "hijack")


def test_update_status_sets_status_and_error(repo: FirestoreBatchRepository):
    created = repo.create(Batch(user_id="u1", name="b"))
    repo.update_status(created.id, BatchStatus.ERROR, error_message="boom")
    stored = repo.get("u1", created.id)
    assert stored.status is BatchStatus.ERROR
    assert stored.error_message == "boom"
    # Non-error status clears the message.
    repo.update_status(created.id, BatchStatus.CREATED, error_message="x")
    assert repo.get("u1", created.id).error_message is None


def test_claim_oldest_created_returns_none_when_none_waiting(
    repo: FirestoreBatchRepository,
):
    repo.create(Batch(user_id="u1", name="pending"))  # status CREATING
    assert repo.claim_oldest_created() is None


def test_claim_oldest_created_picks_oldest_and_flips(repo: FirestoreBatchRepository):
    from datetime import timedelta

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older = repo.create(Batch(user_id="u1", name="older", created=base))
    newer = repo.create(
        Batch(user_id="u2", name="newer", created=base + timedelta(hours=1))
    )
    repo.update_status(older.id, BatchStatus.CREATED)
    repo.update_status(newer.id, BatchStatus.CREATED)

    claimed = repo.claim_oldest_created()
    assert claimed is not None
    assert claimed.id == older.id
    assert claimed.status is BatchStatus.PROCESSING
    # No longer claimable; the next claim is the newer batch.
    assert repo.claim_oldest_created().id == newer.id
    # And once both are processing, nothing is left to claim.
    assert repo.claim_oldest_created() is None


def _race_claim(repo, created, mutate):
    """Run claim_oldest_created with a race injected between the query and the
    transactional re-read: ``mutate(store)`` is applied the first time the
    candidate doc is re-read inside the transaction, simulating another worker
    (or a delete) winning the gap.
    """
    collection = repo._batches
    store = collection.store
    original = collection.document

    def _document(doc_id):
        doc = original(doc_id)
        if doc_id == created.id:
            real_get = doc.get

            def _get(transaction=None):
                mutate(store)  # the raced change lands just before the re-read
                return real_get(transaction=transaction)

            doc.get = _get
        return doc

    collection.document = _document
    try:
        return repo.claim_oldest_created()
    finally:
        collection.document = original


def test_claim_skips_candidate_deleted_before_transaction(
    repo: FirestoreBatchRepository,
):
    # The candidate is gone by the time the transaction re-reads it.
    created = repo.create(Batch(user_id="u1", name="b"))
    repo.update_status(created.id, BatchStatus.CREATED)
    result = _race_claim(repo, created, lambda store: store.pop(created.id, None))
    assert result is None


def test_claim_skips_candidate_no_longer_created(repo: FirestoreBatchRepository):
    # Another worker flipped the candidate out of `created` first.
    created = repo.create(Batch(user_id="u1", name="b"))
    repo.update_status(created.id, BatchStatus.CREATED)

    def _steal(store):
        store[created.id]["status"] = BatchStatus.PROCESSING.value

    result = _race_claim(repo, created, _steal)
    assert result is None


def test_update_status_missing_raises(repo: FirestoreBatchRepository):
    with pytest.raises(BatchNotFoundError):
        repo.update_status("missing", BatchStatus.CREATED)


def test_delete(repo: FirestoreBatchRepository):
    created = repo.create(Batch(user_id="u1", name="temp"))
    repo.delete("u1", created.id)
    with pytest.raises(BatchNotFoundError):
        repo.get("u1", created.id)
    repo.create(Batch(user_id="u1", name="temp"))  # name freed


def test_delete_missing_raises(repo: FirestoreBatchRepository):
    with pytest.raises(BatchNotFoundError):
        repo.delete("u1", "missing")


def test_delete_scoped_to_owner(repo: FirestoreBatchRepository):
    created = repo.create(Batch(user_id="u1", name="mine"))
    with pytest.raises(BatchNotFoundError):
        repo.delete("u2", created.id)
