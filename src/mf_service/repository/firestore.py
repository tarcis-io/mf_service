"""Firestore-backed :class:`~mf_service.repository.BatchRepository`.

One document per batch, keyed by the batch UUID, in a single collection. The
README's "Persistence" section covers the design (the uniqueness-race trade-off,
the composite index); ``google-cloud-firestore`` is imported lazily so importing
this module never requires the library.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar, cast

from mf_service.domain import Batch, BatchStatus
from mf_service.repository.batch import (
    IN_PROGRESS_STATUSES,
    BatchNotFoundError,
    BatchNotProcessableError,
    DuplicateBatchNameError,
)

if TYPE_CHECKING:  # type-checker only; no runtime Firestore import
    from google.cloud.firestore import (
        Client,
        CollectionReference,
        Transaction,
    )

# Return type of a transactional body, carried through the untyped decorator.
_T = TypeVar("_T")


def _to_document(batch: Batch) -> dict[str, Any]:
    """Serialise a :class:`Batch` to a Firestore document dict."""
    return {
        "id": batch.id,
        "user_id": batch.user_id,
        "name": batch.name,
        "created": batch.created,
        "updated": batch.updated,
        "status": batch.status.value,
        "error_message": batch.error_message,
    }


def _as_utc(value: datetime) -> datetime:
    """Normalise a Firestore timestamp to timezone-aware UTC."""
    if value.tzinfo is None:  # defensive: treat a naive value as UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _from_document(data: dict[str, Any] | None) -> Batch:
    """Rebuild a :class:`Batch` from a Firestore document dict.

    ``data`` is optional because ``snapshot.to_dict()`` is, but every call site
    confirms the document exists first; ``None`` means it vanished mid-operation,
    so we fail loudly. ``updated`` is assigned after construction because it is
    not an ``__init__`` argument (see :class:`~mf_service.domain.Batch`).
    """
    if data is None:
        raise ValueError("document has no data")
    batch = Batch(
        id=data["id"],
        user_id=data["user_id"],
        name=data["name"],
        created=_as_utc(data["created"]),
        status=BatchStatus(data["status"]),
        error_message=data.get("error_message"),
    )
    batch.updated = _as_utc(data["updated"])
    return batch


def _status_fields(batch: Batch) -> dict[str, Any]:
    """The document fields a status change writes (status, message, updated)."""
    return {
        "status": batch.status.value,
        "error_message": batch.error_message,
        "updated": batch.updated,
    }


class FirestoreBatchRepository:
    """A :class:`~mf_service.repository.BatchRepository` stored in Firestore."""

    def __init__(self, client: Client, *, collection: str) -> None:
        self._client = client
        self._collection_name = collection
        # The module-level ``firestore.transactional`` decorator, resolved and
        # cached on first use so building a repository never imports Firestore.
        self._transactional_fn: Callable[..., Any] | None = None

    @property
    def _batches(self) -> CollectionReference:
        return self._client.collection(self._collection_name)

    def _transactional(
        self, fn: Callable[[Transaction], _T]
    ) -> Callable[[Transaction], _T]:
        """Wrap ``fn`` with Firestore's module-level transactional decorator."""
        if self._transactional_fn is None:
            from google.cloud import firestore

            self._transactional_fn = firestore.transactional
        return cast("Callable[[Transaction], _T]", self._transactional_fn(fn))

    def _name_is_taken(
        self,
        txn: Transaction,
        user_id: str,
        name: str,
        *,
        exclude_id: str | None = None,
    ) -> bool:
        """Whether ``user_id`` already has a batch named ``name``.

        Runs inside the transaction (reads before writes) so the check and the
        subsequent write are part of one commit.
        """
        from google.cloud.firestore import FieldFilter

        query = self._batches.where(
            filter=FieldFilter("user_id", "==", user_id)
        ).where(filter=FieldFilter("name", "==", name))
        return any(
            doc.id != exclude_id for doc in query.stream(transaction=txn)
        )

    def create(self, batch: Batch) -> Batch:
        batch_ref = self._batches.document(batch.id)

        @self._transactional
        def _create(txn: Transaction) -> Batch:
            # No exclude_id: this batch's id is not persisted yet, so the name
            # check can't match the row we're about to write (unlike rename).
            if self._name_is_taken(txn, batch.user_id, batch.name):
                raise DuplicateBatchNameError(batch.name)
            txn.set(batch_ref, _to_document(batch))
            return batch

        return _create(self._client.transaction())

    def get(self, user_id: str, batch_id: str) -> Batch:
        # A single point-read needs no transaction (unlike the read-then-write
        # mutators below), so this reads the document directly.
        snapshot = self._batches.document(batch_id).get()
        if not snapshot.exists:
            raise BatchNotFoundError(batch_id)
        batch = _from_document(snapshot.to_dict())
        if batch.user_id != user_id:  # another user's id is not visible here
            raise BatchNotFoundError(batch_id)
        return batch

    def list_for_user(self, user_id: str) -> list[Batch]:
        # Filter by owner and sort newest-first in Firestore. This needs a
        # composite index on (user_id, created); Firestore's first-run error
        # links straight to creating it.
        from google.cloud.firestore import FieldFilter, Query

        query = self._batches.where(
            filter=FieldFilter("user_id", "==", user_id)
        ).order_by("created", direction=Query.DESCENDING)
        return [_from_document(doc.to_dict()) for doc in query.stream()]

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
            if name == current.name:  # no-op rename
                return current
            if self._name_is_taken(txn, user_id, name, exclude_id=batch_id):
                raise DuplicateBatchNameError(name)
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

        @self._transactional
        def _update(txn: Transaction) -> Batch:
            snapshot = batch_ref.get(transaction=txn)
            if not snapshot.exists:
                raise BatchNotFoundError(batch_id)
            current = _from_document(snapshot.to_dict())
            # set_status owns the updated-stamp + error_message coupling.
            current.set_status(status, error_message=error_message)
            txn.update(batch_ref, _status_fields(current))
            return current

        return _update(self._client.transaction())

    def claim_for_processing(self, batch_id: str) -> Batch:
        batch_ref = self._batches.document(batch_id)

        @self._transactional
        def _claim(txn: Transaction) -> Batch:
            snapshot = batch_ref.get(transaction=txn)
            if not snapshot.exists:
                raise BatchNotFoundError(batch_id)
            current = _from_document(snapshot.to_dict())
            if current.status is not BatchStatus.CREATED:
                raise BatchNotProcessableError(batch_id)
            current.set_status(BatchStatus.PROCESSING)
            txn.update(batch_ref, _status_fields(current))
            return current

        return _claim(self._client.transaction())

    def claim_oldest_created(self) -> Batch | None:
        # Oldest batch in `created`, filtered + ordered in Firestore. Needs a
        # composite index on (status, created); Firestore's first-run error
        # links straight to creating it.
        from google.cloud.firestore import FieldFilter, Query

        query = (
            self._batches.where(
                filter=FieldFilter("status", "==", BatchStatus.CREATED.value)
            )
            .order_by("created", direction=Query.ASCENDING)
            .limit(1)
        )

        @self._transactional
        def _claim(txn: Transaction) -> Batch | None:
            # Read the oldest created batch inside the transaction (reads before
            # writes), so the flip commits atomically with the selection.
            docs = list(query.stream(transaction=txn))
            if not docs:
                return None
            current = _from_document(docs[0].to_dict())
            current.set_status(BatchStatus.PROCESSING)
            txn.update(
                self._batches.document(current.id), _status_fields(current)
            )
            return current

        return _claim(self._client.transaction())

    def fail_timed_out(
        self, older_than: datetime, *, error_message: str
    ) -> list[Batch]:
        # One query per in-progress status (status == s AND updated < cutoff),
        # each needing a composite index on (status, updated); Firestore's
        # first-run error links straight to creating it. A batch holds one
        # status, so the per-status results never overlap.
        from google.cloud.firestore import FieldFilter

        queries = [
            self._batches.where(
                filter=FieldFilter("status", "==", status.value)
            ).where(filter=FieldFilter("updated", "<", older_than))
            for status in IN_PROGRESS_STATUSES
        ]

        @self._transactional
        def _sweep(txn: Transaction) -> list[Batch]:
            # Read all matches inside the transaction (reads before writes), then
            # flip each — so the sweep's reads and writes commit together.
            docs = [
                doc for query in queries for doc in query.stream(transaction=txn)
            ]
            timed_out: list[Batch] = []
            for doc in docs:
                current = _from_document(doc.to_dict())
                current.set_status(
                    BatchStatus.ERROR, error_message=error_message
                )
                txn.update(
                    self._batches.document(current.id),
                    _status_fields(current),
                )
                timed_out.append(current)
            return timed_out

        return _sweep(self._client.transaction())

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
            txn.delete(batch_ref)

        _delete(self._client.transaction())


__all__ = ["FirestoreBatchRepository"]
