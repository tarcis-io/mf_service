"""The storage-agnostic ``Batch`` entity and its lifecycle status.

:class:`Batch` is the service's own vocabulary for a unit of processing work; it
does not depend on FastAPI, pydantic, or any storage backend, so it has a single
definition the transport and persistence layers both map to.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

#: The exact set of files a batch upload must contain, in a stable order. The
#: create endpoint requires one upload part per name; the engine consumes them
#: downstream. Kept here so the route and its tests share one definition.
REQUIRED_BATCH_FILES: tuple[str, ...] = (
    "mzmine_annotations.csv",
    "mzmine_full_feature_table.csv",
    "mzmine_metadata.csv",
    "gnps_feature_table_reformated.csv",
    "gnps_merged_results.tsv",
)


class BatchStatus(str, Enum):
    """Lifecycle state of a batch.

    A ``str`` enum so values serialise as plain strings ("creating", ...) over
    HTTP and into storage, while staying type-safe in Python.

    The intended flow is ``creating -> created`` then
    ``created -> processing -> processed``, with ``error`` as the shared failure
    state carrying ``error_message``. :attr:`CREATING` is the initial state a
    freshly created batch is born in.
    """

    CREATING = "creating"
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

    ``id``, ``created``, and ``updated`` are assigned at construction so a
    freshly built ``Batch`` is already complete; ``id`` is a generated UUID4 and
    ``created``/``updated`` are equal at birth. ``status`` starts at
    :attr:`BatchStatus.CREATING` and advances through the lifecycle (see
    :class:`BatchStatus`). ``error_message`` is populated only when ``status`` is
    :attr:`BatchStatus.ERROR`.

    ``updated`` tracks the time of the last **status change**: call
    :meth:`set_status` to move the batch, so the timestamp and the status can
    never diverge.
    """

    user_id: str
    name: str
    id: str = field(default_factory=_new_id)
    created: datetime = field(default_factory=_now)
    status: BatchStatus = BatchStatus.CREATING
    error_message: str | None = None
    # Not a constructor argument: set from `created` in __post_init__, then only
    # ever moved by set_status.
    updated: datetime = field(init=False)

    def __post_init__(self) -> None:
        self.updated = self.created

    def set_status(
        self, status: BatchStatus, *, error_message: str | None = None
    ) -> None:
        """Move the batch to ``status`` and stamp :attr:`updated` with now.

        ``error_message`` stays coupled to the error state: it is stored only
        when moving to :attr:`BatchStatus.ERROR` and cleared on any other
        status, so a stale failure message can never linger on a healthy batch.
        Any transition is permitted — the lifecycle order is a convention, not
        an enforced constraint.
        """
        self.status = status
        self.error_message = (
            error_message if status is BatchStatus.ERROR else None
        )
        self.updated = _now()


__all__ = ["Batch", "BatchStatus", "REQUIRED_BATCH_FILES"]
