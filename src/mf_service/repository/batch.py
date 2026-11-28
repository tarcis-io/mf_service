"""The :class:`BatchRepository` contract, its errors, and an in-memory impl.

The repository owns batch storage and the per-user name-uniqueness invariant:
the check and the write stay together here (not in the route) so they can be
made atomic. Operations raise :class:`DuplicateBatchNameError` /
:class:`BatchNotFoundError` rather than returning sentinels, so the route maps
each to an HTTP status once.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime
from typing import Protocol, runtime_checkable

from mf_service.domain import Batch, BatchStatus

# Statuses that mean a batch is mid-work and could get stuck if its background
# job dies: the create upload (``creating``) and the processing pipeline
# (``processing``). The timeout sweep targets exactly these.
IN_PROGRESS_STATUSES: tuple[BatchStatus, ...] = (
    BatchStatus.CREATING,
    BatchStatus.PROCESSING,
)


class BatchNotFoundError(Exception):
    """Raised when a batch id does not exist (for this user)."""


class DuplicateBatchNameError(Exception):
    """Raised when a batch name already exists for the user."""


class BatchNotProcessableError(Exception):
    """Raised when a batch cannot be processed because it is not ``created``."""


@runtime_checkable
class BatchRepository(Protocol):
    """Storage contract for :class:`~mf_service.domain.Batch` entities.

    Reads and writes are scoped by ``user_id`` — an id belonging to another user
    reads as not found — except :meth:`update_status`, which is server-side.
    Returned batches are snapshots; mutate the store only through these methods.
    """

    def create(self, batch: Batch) -> Batch:
        """Persist a new batch, or raise :class:`DuplicateBatchNameError`."""

    def get(self, user_id: str, batch_id: str) -> Batch:
        """Return the user's batch by id, or raise :class:`BatchNotFoundError`."""

    def list_for_user(self, user_id: str) -> list[Batch]:
        """Return all of the user's batches, newest first."""

    def rename(self, user_id: str, batch_id: str, name: str) -> Batch:
        """Rename the user's batch and return it.

        Raises :class:`BatchNotFoundError` if absent, or
        :class:`DuplicateBatchNameError` if the new name is taken by another of
        the user's batches. A rename is not a status change, so ``updated`` is
        left untouched.
        """

    def update_status(
        self,
        batch_id: str,
        status: BatchStatus,
        *,
        error_message: str | None = None,
    ) -> Batch:
        """Set a batch's ``status`` (and optional ``error_message``), stamping
        ``updated``. Any transition is allowed. ``error_message`` is kept only
        for :attr:`~mf_service.domain.BatchStatus.ERROR` and cleared otherwise.

        Not scoped by ``user_id``: it is for server-side work (a future
        processing worker) that already holds a batch id. Raises
        :class:`BatchNotFoundError` if the id does not exist.
        """

    def claim_for_processing(self, batch_id: str) -> Batch:
        """Atomically move a specific ``created`` batch to ``processing``.

        For the ``/process`` endpoint, which is not user-scoped: the batch is
        looked up globally by id. Raises :class:`BatchNotFoundError` if the id
        does not exist, or :class:`BatchNotProcessableError` if the batch is not
        in :attr:`~mf_service.domain.BatchStatus.CREATED`. The check and the flip
        are one atomic step, so two concurrent claims can't both win. ``updated``
        is stamped by the status change.
        """

    def claim_oldest_created(self) -> Batch | None:
        """Atomically move the oldest ``created`` batch to ``processing``.

        Picks the oldest batch (by ``created``) in
        :attr:`~mf_service.domain.BatchStatus.CREATED` across all users, flips it
        to ``processing``, and returns it — or ``None`` if none is waiting. The
        select-and-flip is atomic, so overlapping calls never claim the same
        batch.
        """

    def fail_timed_out(
        self, older_than: datetime, *, error_message: str
    ) -> list[Batch]:
        """Move every stuck in-progress batch to ``error`` and return them.

        A batch is stuck if its status is in :data:`IN_PROGRESS_STATUSES`
        (``creating`` or ``processing``) and its ``updated`` timestamp is
        strictly before ``older_than`` — i.e. it has not changed status in too
        long, so its background job likely died. Each is moved to
        :attr:`~mf_service.domain.BatchStatus.ERROR` with ``error_message`` (which
        stamps ``updated``). Not user-scoped: the sweep is global. Returns the
        batches it timed out (empty if none).
        """

    def delete(self, user_id: str, batch_id: str) -> None:
        """Delete the user's batch, or raise :class:`BatchNotFoundError`."""


class InMemoryBatchRepository:
    """A dict-backed :class:`BatchRepository` for tests and local runs.

    Stores and returns copies, so callers never hold a live reference into the
    store — matching how a real backend (which deserializes fresh objects)
    behaves. A lock makes each check-and-write atomic.
    """

    def __init__(self) -> None:
        self._batches: dict[str, Batch] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _snapshot(batch: Batch) -> Batch:
        """Return an independent copy of ``batch``.

        ``updated`` is ``init=False`` so ``replace()`` cannot carry it through
        the constructor (``__post_init__`` would reset it to ``created``); it is
        restored explicitly so a copy preserves the real last-change time.
        """
        copy = replace(batch)
        copy.updated = batch.updated
        return copy

    def _name_taken(
        self, user_id: str, name: str, *, exclude_id: str | None = None
    ) -> bool:
        return any(
            b.user_id == user_id and b.name == name and b.id != exclude_id
            for b in self._batches.values()
        )

    def create(self, batch: Batch) -> Batch:
        with self._lock:
            # No exclude_id: the batch is not in the store yet (unlike rename).
            if self._name_taken(batch.user_id, batch.name):
                raise DuplicateBatchNameError(batch.name)
            self._batches[batch.id] = self._snapshot(batch)
            return self._snapshot(batch)

    def get(self, user_id: str, batch_id: str) -> Batch:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None or batch.user_id != user_id:
                raise BatchNotFoundError(batch_id)
            return self._snapshot(batch)

    def list_for_user(self, user_id: str) -> list[Batch]:
        with self._lock:
            owned = [
                self._snapshot(b)
                for b in self._batches.values()
                if b.user_id == user_id
            ]
        return sorted(owned, key=lambda b: b.created, reverse=True)

    def rename(self, user_id: str, batch_id: str, name: str) -> Batch:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None or batch.user_id != user_id:
                raise BatchNotFoundError(batch_id)
            if self._name_taken(user_id, name, exclude_id=batch_id):
                raise DuplicateBatchNameError(name)
            batch.name = name
            return self._snapshot(batch)

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
            # Delegate to the entity so the updated-stamp and error_message
            # coupling live only in the domain.
            batch.set_status(status, error_message=error_message)
            return self._snapshot(batch)

    def claim_for_processing(self, batch_id: str) -> Batch:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                raise BatchNotFoundError(batch_id)
            if batch.status is not BatchStatus.CREATED:
                raise BatchNotProcessableError(batch_id)
            batch.set_status(BatchStatus.PROCESSING)
            return self._snapshot(batch)

    def claim_oldest_created(self) -> Batch | None:
        with self._lock:
            created = [
                b
                for b in self._batches.values()
                if b.status is BatchStatus.CREATED
            ]
            if not created:
                return None
            batch = min(created, key=lambda b: b.created)
            batch.set_status(BatchStatus.PROCESSING)
            return self._snapshot(batch)

    def fail_timed_out(
        self, older_than: datetime, *, error_message: str
    ) -> list[Batch]:
        with self._lock:
            stuck = [
                b
                for b in self._batches.values()
                if b.status in IN_PROGRESS_STATUSES and b.updated < older_than
            ]
            for batch in stuck:
                batch.set_status(BatchStatus.ERROR, error_message=error_message)
            return [self._snapshot(b) for b in stuck]

    def delete(self, user_id: str, batch_id: str) -> None:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None or batch.user_id != user_id:
                raise BatchNotFoundError(batch_id)
            del self._batches[batch_id]
