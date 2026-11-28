"""API schemas — the typed request/response contracts for the HTTP API.

Keeping the models in their own package (rather than inline in the routes) means
the API surface is documented and validated from a single source, and new
endpoints reuse or extend these models instead of passing around loose dicts.
One module per resource keeps the contracts easy to find as the surface grows.
"""

from __future__ import annotations

from mf_service.schemas.batch import (
    BatchFileResponse,
    BatchResponse,
    BatchUpdateRequest,
)
from mf_service.schemas.process import ProcessRequest
from mf_service.schemas.version import VersionResponse

__all__ = [
    "VersionResponse",
    "BatchUpdateRequest",
    "BatchResponse",
    "BatchFileResponse",
    "ProcessRequest",
]
