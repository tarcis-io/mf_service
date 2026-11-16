"""Firestore-backed :class:`~mf_service.repository.BatchRepository`.

Two collections back the entity:

* **batches** — one document per batch, keyed by the batch UUID, holding the
  serialised :class:`~mf_service.domain.Batch`.
* **batch_names** — a *uniqueness guard*: one document per ``(user_id, name)``
  pair, keyed by a deterministic id derived from both, holding the owning
  batch id. Firestore has no unique constraints, so this guard is how "one name
  per user" is enforced: creating the guard document is what reserves the name.

Every mutation that touches a name runs in a **Firestore transaction** that
reads the guard first and writes the batch and guard together. Because a
transaction aborts and retries when a document it read is changed concurrently,
two simultaneous creates of the same name cannot both succeed — one commits and
the other sees the guard and raises. This is the race-safety a plain
query-then-write cannot give.

``google-cloud-firestore`` is imported lazily (inside the provider that builds
this repository, not at module import) so importing the service stays cheap and
only pulls the client in when Firestore is actually used — mirroring the
engine-integration seam's lazy-import discipline.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar, cast

from mf_service.domain import Batch, BatchStatus
from mf_service.repository import BatchNotFoundError, DuplicateBatchNameError

if TYPE_CHECKING:  # type-checker only; no runtime Firestore import
    from google.cloud.firestore import (
        Client,
        CollectionReference,
        Transaction,
    )

# Return type of a transactional body — preserved through the decorator so the
# call site gets the body's type (Batch / None) rather than Any.
_T = TypeVar("_T")


def _name_key(user_id: str, name: str) -> str:
    """Return a deterministic, collision-resistant guard-doc id for a name.

    Firestore document ids can't contain ``/`` and are length-limited, and a
    user id or name could contain either problem. Hashing ``user_id`` and
    ``name`` together yields a fixed-length, filesystem-safe id that is unique
    per pair — which is exactly the uniqueness scope we need. A plain SHA-256 is
    used for a stable key, not for security.
    """
    return hashlib.sha256(f"{user_id}\x00{name}".encode()).hexdigest()


def _to_document(batch: Batch) -> dict[str, Any]:
    """Serialise a :class:`Batch` to a Firestore document dict."""
    return {
        "id": batch.id,
        "user_id": batch.user_id,
        "name": batch.name,
        "created": batch.created,
        "status": batch.status.value,
        "error_message": batch.error_message,
    }


def _from_document(data: dict[str, Any] | None) -> Batch:
    """Rebuild a :class:`Batch` from a Firestore document dict.

    ``data`` is typed optional because ``snapshot.to_dict()`` is — but every
    call site has already confirmed the document exists (via ``.exists`` or by
    streaming a query that only yields present docs), so ``None`` here means a
    document vanished mid-operation; we fail loudly rather than return a half
    batch.

    The ``created`` timestamp comes back from Firestore as a timezone-aware
    datetime; it is normalised to UTC so the API always emits consistent values.
    """
    if data is None:  # pragma: no cover - guarded by .exists at every call site
        raise ValueError("document has no data")
    created: datetime = data["created"]
    if created.tzinfo is None:  # defensive: treat a naive value as UTC
        created = created.replace(tzinfo=timezone.utc)
    return Batch(
        id=data["id"],
        user_id=data["user_id"],
        name=data["name"],
        created=created.astimezone(timezone.utc),
        status=BatchStatus(data["status"]),
        error_message=data.get("error_message"),
    )


class FirestoreBatchRepository:
    """A :class:`~mf_service.repository.BatchRepository` stored in Firestore."""

    def __init__(
        self,
        client: Client,
        *,
        batches_collection: str,
        names_collection: str,
    ) -> None:
        self._client = client
        self._batches_name = batches_collection
        self._names_name = names_collection

    @property
    def _batches(self) -> CollectionReference:
        return self._client.collection(self._batches_name)

    @property
    def _names(self) -> CollectionReference:
        return self._client.collection(self._names_name)

    @staticmethod
    def _transactional(
        fn: Callable[[Transaction], _T],
    ) -> Callable[[Transaction], _T]:
        """Wrap ``fn`` with the Firestore transactional decorator.

        ``firestore.transactional`` is a module-level decorator (not a client
        method); it is imported lazily here so the module stays free of a
        runtime Firestore import until a transactional operation actually runs.
        The wrapped function takes the transaction as its first argument and is
        invoked by passing ``client.transaction()``. The ``_T`` return type is
        carried through the untyped decorator (via ``cast``) so each call site
        gets the body's own type (``Batch`` / ``None``), not ``Any``.
        """
        from google.cloud import firestore

        return cast("Callable[[Transaction], _T]", firestore.transactional(fn))

    def create(self, batch: Batch) -> Batch:
        guard_ref = self._names.document(_name_key(batch.user_id, batch.name))
        batch_ref = self._batches.document(batch.id)

        @self._transactional
        def _create(txn: Transaction) -> Batch:
            # Read before write (Firestore requires it). If the guard already
            # exists the name is taken; the transaction's conflict detection
            # makes this safe against a concurrent create of the same name.
            if guard_ref.get(transaction=txn).exists:
                raise DuplicateBatchNameError(batch.name)
            txn.set(guard_ref, {"batch_id": batch.id})
            txn.set(batch_ref, _to_document(batch))
            return batch

        return _create(self._client.transaction())

    def get(self, user_id: str, batch_id: str) -> Batch:
        snapshot = self._batches.document(batch_id).get()
        if not snapshot.exists:
            raise BatchNotFoundError(batch_id)
        batch = _from_document(snapshot.to_dict())
        if batch.user_id != user_id:  # another user's id is not visible here
            raise BatchNotFoundError(batch_id)
        return batch

    def list_for_user(self, user_id: str) -> list[Batch]:
        # Filter by owner and sort newest-first in Firestore. Ordering by
        # `created DESC` on a filtered query needs a composite index on
        # (user_id, created) — Firestore's error names the exact index to
        # create the first time this runs against a real backend.
        from google.cloud.firestore import FieldFilter, Query

        query = self._batches.where(
            filter=FieldFilter("user_id", "==", user_id)
        ).order_by("created", direction=Query.DESCENDING)
        return [_from_document(doc.to_dict()) for doc in query.stream()]

    def claim_oldest_created(self) -> Batch | None:
        # Query for the oldest `created` batch (needs a composite index on
        # (status, created) — Firestore names it on first run). Then flip it to
        # `processing` inside a transaction that re-reads the doc and only
        # commits if it is *still* created, so a batch another worker claimed in
        # the gap is skipped rather than double-processed.
        from google.cloud.firestore import FieldFilter, Query

        query = (
            self._batches.where(
                filter=FieldFilter("status", "==", BatchStatus.CREATED.value)
            )
            .order_by("created", direction=Query.ASCENDING)
            .limit(1)
        )
        candidates = list(query.stream())
        if not candidates:
            return None
        batch_ref = self._batches.document(candidates[0].to_dict()["id"])

        @self._transactional
        def _claim(txn: Transaction) -> Batch | None:
            snapshot = batch_ref.get(transaction=txn)
            if not snapshot.exists:
                return None
            current = _from_document(snapshot.to_dict())
            if current.status is not BatchStatus.CREATED:
                return None  # another worker claimed it first
            txn.update(
                batch_ref,
                {
                    "status": BatchStatus.PROCESSING.value,
                    "error_message": None,
                },
            )
            current.status = BatchStatus.PROCESSING
            current.error_message = None
            return current

        return _claim(self._client.transaction())

    def rename(self, user_id: str, batch_id: str, name: str) -> Batch:
        batch_ref = self._batches.document(batch_id)

        @self._transactional
        def _rename(txn: Transaction) -> Batch:
            snapshot = batch_ref.get(transaction=txn)
            if not snapshot.exists:
                raise BatchNotFoundError(batch_id)
            current = _from_document(snapshot.to_dict())
            if current.user_id != user_id:
                raise BatchNotFoundError(batch_id)
            if name == current.name:  # no-op rename: nothing to reserve
                return current

            new_guard = self._names.document(_name_key(user_id, name))
            old_guard = self._names.document(_name_key(user_id, current.name))
            if new_guard.get(transaction=txn).exists:
                raise DuplicateBatchNameError(name)

            txn.set(new_guard, {"batch_id": batch_id})
            txn.delete(old_guard)
            txn.update(batch_ref, {"name": name})
            current.name = name
            return current

        return _rename(self._client.transaction())

    def update_status(
        self,
        batch_id: str,
        status: BatchStatus,
        *,
        error_message: str | None = None,
    ) -> Batch:
        batch_ref = self._batches.document(batch_id)
        # error_message stays coupled to the error state (see the protocol).
        message = error_message if status is BatchStatus.ERROR else None

        @self._transactional
        def _update(txn: Transaction) -> Batch:
            snapshot = batch_ref.get(transaction=txn)
            if not snapshot.exists:
                raise BatchNotFoundError(batch_id)
            txn.update(
                batch_ref,
                {"status": status.value, "error_message": message},
            )
            current = _from_document(snapshot.to_dict())
            current.status = status
            current.error_message = message
            return current

        return _update(self._client.transaction())

    def delete(self, user_id: str, batch_id: str) -> None:
        batch_ref = self._batches.document(batch_id)

        @self._transactional
        def _delete(txn: Transaction) -> None:
            snapshot = batch_ref.get(transaction=txn)
            if not snapshot.exists:
                raise BatchNotFoundError(batch_id)
            current = _from_document(snapshot.to_dict())
            if current.user_id != user_id:
                raise BatchNotFoundError(batch_id)
            txn.delete(self._names.document(_name_key(user_id, current.name)))
            txn.delete(batch_ref)

        _delete(self._client.transaction())
