"""Persistence seam for domain entities.

The ``repository`` package defines storage *contracts* (as ``Protocol``s) and
their errors, plus lightweight in-memory implementations for tests and local
runs. Routes depend on the contract (via ``Depends``), never on a concrete
backend, so the HTTP layer is unaware of how batches are stored and a real
backend (e.g. Firestore) can be added later without touching the routes.
"""

from __future__ import annotations

from mf_service.repository.batch import (
    IN_PROGRESS_STATUSES,
    BatchNotFoundError,
    BatchNotProcessableError,
    BatchRepository,
    DuplicateBatchNameError,
    InMemoryBatchRepository,
)
from mf_service.repository.firestore import FirestoreBatchRepository

__all__ = [
    "BatchRepository",
    "BatchNotFoundError",
    "DuplicateBatchNameError",
    "BatchNotProcessableError",
    "InMemoryBatchRepository",
    "FirestoreBatchRepository",
    "IN_PROGRESS_STATUSES",
]
