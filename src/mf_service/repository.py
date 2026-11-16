"""Persistence seam — the :class:`BatchRepository` contract and its errors.

The repository owns the storage of :class:`~mf_service.domain.Batch` entities
*and* the invariants that storage must guarantee — chiefly that a name is
unique per user. Routes depend on this interface (via ``Depends``), never on a
concrete backend, so the HTTP layer is unaware of Firestore and tests can swap
in the in-memory implementation below.

Enforcing uniqueness here (rather than in the route) keeps the check and the
write together, which is what makes it race-safe: the Firestore implementation
does both inside a transaction, and :class:`InMemoryBatchRepository` does both
under a lock. A route that checked separately before writing could not close
that window.

Operations raise :class:`DuplicateBatchNameError` or :class:`BatchNotFoundError`
rather than returning sentinels, so the route maps each to an HTTP status in one
place.
"""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from mf_service.domain import Batch, BatchStatus


class BatchNotFoundError(Exception):
    """Raised when a batch id does not exist (for this user)."""


class DuplicateBatchNameError(Exception):
    """Raised when a batch name already exists for the user."""


@runtime_checkable
class BatchRepository(Protocol):
    """Storage contract for :class:`~mf_service.domain.Batch` entities.

    All reads and writes are scoped by ``user_id``: a user can only see and
    affect their own batches, so an id belonging to another user is treated as
    not found.
    """

    def create(self, batch: Batch) -> Batch:
        """Persist a new batch.

        Raises :class:`DuplicateBatchNameError` if the user already has a batch
        with the same name.
        """

    def get(self, user_id: str, batch_id: str) -> Batch:
        """Return the user's batch by id, or raise :class:`BatchNotFoundError`."""

    def list_for_user(self, user_id: str) -> list[Batch]:
        """Return all of the user's batches, newest first."""

    def rename(self, user_id: str, batch_id: str, name: str) -> Batch:
        """Rename the user's batch and return the updated entity.

        Raises :class:`BatchNotFoundError` if it does not exist, or
        :class:`DuplicateBatchNameError` if the new name is already taken by
        another of the user's batches.
        """

    def update_status(
        self,
        batch_id: str,
        status: BatchStatus,
        *,
        error_message: str | None = None,
    ) -> Batch:
        """Set a batch's ``status`` (and optional ``error_message``).

        Unlike the other operations this is **not** scoped by ``user_id``: it is
        called by server-side background work (the upload task, later the
        engine) that already holds a batch id and acts on the owner's behalf,
        not by a user request. Raises :class:`BatchNotFoundError` if the id does
        not exist. ``error_message`` is written as given — pass it only when
        moving to :attr:`~mf_service.domain.BatchStatus.ERROR`, and it is
        cleared on any non-error status.
        """

    def delete(self, user_id: str, batch_id: str) -> None:
        """Delete the user's batch, or raise :class:`BatchNotFoundError`."""


class InMemoryBatchRepository:
    """A dict-backed :class:`BatchRepository` for tests and local runs.

    Mirrors the Firestore implementation's semantics — per-user name uniqueness,
    ownership scoping, newest-first listing — without any external dependency. A
    lock makes its check-and-write atomic, matching the race-safety the
    Firestore transaction provides, so tests exercise the same guarantees.
    """

    def __init__(self) -> None:
        self._batches: dict[str, Batch] = {}
        self._lock = threading.Lock()

    def _name_taken(
        self, user_id: str, name: str, *, exclude_id: str | None = None
    ) -> bool:
        return any(
            b.user_id == user_id and b.name == name and b.id != exclude_id
            for b in self._batches.values()
        )

    def create(self, batch: Batch) -> Batch:
        with self._lock:
            if self._name_taken(batch.user_id, batch.name):
                raise DuplicateBatchNameError(batch.name)
            self._batches[batch.id] = batch
            return batch

    def get(self, user_id: str, batch_id: str) -> Batch:
        batch = self._batches.get(batch_id)
        if batch is None or batch.user_id != user_id:
            raise BatchNotFoundError(batch_id)
        return batch

    def list_for_user(self, user_id: str) -> list[Batch]:
        owned = [b for b in self._batches.values() if b.user_id == user_id]
        return sorted(owned, key=lambda b: b.created, reverse=True)

    def rename(self, user_id: str, batch_id: str, name: str) -> Batch:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None or batch.user_id != user_id:
                raise BatchNotFoundError(batch_id)
            if self._name_taken(user_id, name, exclude_id=batch_id):
                raise DuplicateBatchNameError(name)
            batch.name = name
            return batch

    def update_status(
        self,
        batch_id: str,
        status: BatchStatus,
        *,
        error_message: str | None = None,
    ) -> Batch:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                raise BatchNotFoundError(batch_id)
            batch.status = status
            # error_message stays coupled to the error state (see the protocol).
            batch.error_message = (
                error_message if status is BatchStatus.ERROR else None
            )
            return batch

    def delete(self, user_id: str, batch_id: str) -> None:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None or batch.user_id != user_id:
                raise BatchNotFoundError(batch_id)
            del self._batches[batch_id]
