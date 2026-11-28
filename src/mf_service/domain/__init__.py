"""Domain models — the service's storage-agnostic vocabulary.

These types do not depend on FastAPI, pydantic, or any storage backend. The HTTP
layer (:mod:`mf_service.schemas`) and the persistence layer
(:mod:`mf_service.repository`) both map to and from them, so each entity has a
single definition that neither transport nor storage can drift from. One module
per entity keeps the domain easy to navigate as it grows.
"""

from __future__ import annotations

from mf_service.domain.batch import REQUIRED_BATCH_FILES, Batch, BatchStatus

__all__ = ["Batch", "BatchStatus", "REQUIRED_BATCH_FILES"]
