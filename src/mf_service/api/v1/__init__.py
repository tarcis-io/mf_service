"""Version 1 of the HTTP API.

:data:`router` aggregates every v1 route module (one module per resource) into
a single ``APIRouter``. The app factory mounts it under the ``/`` prefix today;
mounting it under ``/v1`` later is a one-line change that leaves the route
modules untouched. New endpoints are added as their own module and included
here — nothing else in the app has to change.
"""

from __future__ import annotations

from fastapi import APIRouter

from mf_service.api.v1 import batches, process, version

router = APIRouter()
router.include_router(version.router)
router.include_router(batches.router)
router.include_router(process.router)

__all__ = ["router"]
