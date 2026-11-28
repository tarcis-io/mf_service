"""Object storage seam for batch file uploads.

Defines the :class:`BatchStorage` contract (a ``Protocol``), a Google Cloud
Storage implementation (the default), and an in-memory implementation for tests
and local runs. Routes depend on the contract, never a concrete backend.
"""

from __future__ import annotations

from mf_service.storage.batch import (
    BatchStorage,
    InMemoryBatchStorage,
    ObjectNotFoundError,
    StoredFile,
)
from mf_service.storage.gcs import GCSBatchStorage

__all__ = [
    "BatchStorage",
    "InMemoryBatchStorage",
    "GCSBatchStorage",
    "StoredFile",
    "ObjectNotFoundError",
]
