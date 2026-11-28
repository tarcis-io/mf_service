"""The :class:`BatchStorage` contract and an in-memory implementation.

Batch files live under one folder per batch (``<batch id>/<filename>``) in an
object store. Routes depend on this contract (via ``Depends``), never on a
concrete backend, so the HTTP layer is unaware of how files are stored and the
in-memory impl can back the tests without any cloud access.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


class ObjectNotFoundError(Exception):
    """Raised when a batch file does not exist in storage."""


@dataclass
class StoredFile:
    """Metadata for one stored batch file (storage-agnostic).

    ``filename`` is the bare name within the batch (not the full
    ``<batch id>/<filename>`` key).
    """

    filename: str
    size: int
    content_type: str
    updated: datetime | None


@runtime_checkable
class BatchStorage(Protocol):
    """Object-store contract for a batch's uploaded files.

    Objects are addressed by ``(batch_id, filename)`` and stored under the
    batch's folder. :meth:`delete_batch` removes the whole folder.
    """

    def upload(
        self,
        batch_id: str,
        filename: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        """Store ``data`` as ``<batch_id>/<filename>`` (overwriting)."""

    def list_files(self, batch_id: str) -> list[StoredFile]:
        """Return metadata for every file stored under ``<batch_id>/``.

        Each :class:`StoredFile` carries the bare filename. An empty list if the
        batch has no stored files (e.g. its upload has not finished or failed).
        """

    def download(self, batch_id: str, filename: str) -> bytes:
        """Return the bytes of ``<batch_id>/<filename>``.

        Raises :class:`ObjectNotFoundError` if no such file exists.
        """

    def delete_batch(self, batch_id: str) -> None:
        """Delete every object under ``<batch_id>/``.

        A no-op if the batch has no stored files, so it is safe to call for a
        batch whose upload never ran or already errored.
        """


class InMemoryBatchStorage:
    """A dict-backed :class:`BatchStorage` for tests and local runs.

    Keyed by ``(batch_id, filename)``, tracking each object's bytes, content
    type, and write time. A lock guards the dicts so concurrent uploads (the
    create flow uploads several files in one background job) and a delete don't
    race.
    """

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}
        self._content_types: dict[tuple[str, str], str] = {}
        self._updated: dict[tuple[str, str], datetime] = {}
        self._lock = threading.Lock()

    def upload(
        self,
        batch_id: str,
        filename: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        with self._lock:
            key = (batch_id, filename)
            self._objects[key] = data
            self._content_types[key] = content_type or "application/octet-stream"
            self._updated[key] = datetime.now(timezone.utc)

    def list_files(self, batch_id: str) -> list[StoredFile]:
        with self._lock:
            return [
                StoredFile(
                    filename=filename,
                    size=len(data),
                    content_type=self._content_types[(bid, filename)],
                    updated=self._updated.get((bid, filename)),
                )
                for (bid, filename), data in self._objects.items()
                if bid == batch_id
            ]

    def download(self, batch_id: str, filename: str) -> bytes:
        with self._lock:
            try:
                return self._objects[(batch_id, filename)]
            except KeyError as exc:
                raise ObjectNotFoundError(f"{batch_id}/{filename}") from exc

    def delete_batch(self, batch_id: str) -> None:
        with self._lock:
            for key in [k for k in self._objects if k[0] == batch_id]:
                del self._objects[key]
                self._content_types.pop(key, None)
                self._updated.pop(key, None)

    # --- test helpers (not part of the Protocol) ----------------------------

    def files_for(self, batch_id: str) -> dict[str, bytes]:
        """Return ``{filename: data}`` currently stored for ``batch_id``."""
        with self._lock:
            return {
                filename: data
                for (bid, filename), data in self._objects.items()
                if bid == batch_id
            }


__all__ = [
    "BatchStorage",
    "InMemoryBatchStorage",
    "StoredFile",
    "ObjectNotFoundError",
]
