"""Domain model — the storage-agnostic ``Batch`` entity.

These types are the service's own vocabulary; they do not depend on FastAPI,
pydantic, or Firestore. The HTTP layer (:mod:`mf_service.schemas`) and the
persistence layer (:mod:`mf_service.repository`) both map to and from them, so
the entity has a single definition that neither transport nor storage can drift
from.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class BatchStatus(str, Enum):
    """Lifecycle state of a batch.

    A ``str`` enum so values serialise as plain strings ("created", ...) over
    HTTP and into Firestore, while staying type-safe in Python.
    """

    CREATED = "created"
    PROCESSING = "processing"
    PROCESSED = "processed"
    ERROR = "error"


def _new_id() -> str:
    """Return a fresh batch id (a UUID4 string)."""
    return str(uuid.uuid4())


def _now() -> datetime:
    """Return the current time as a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass
class Batch:
    """A processing batch owned by a user.

    ``id`` and ``created`` are assigned at construction so a freshly built
    ``Batch`` is already complete. ``status`` starts at
    :attr:`BatchStatus.CREATED`; it advances later as the engine processes the
    batch and is not set through the CRUD API. ``error_message`` is populated
    only when ``status`` is :attr:`BatchStatus.ERROR`.
    """

    user_id: str
    name: str
    id: str = field(default_factory=_new_id)
    created: datetime = field(default_factory=_now)
    status: BatchStatus = BatchStatus.CREATED
    error_message: str | None = None
