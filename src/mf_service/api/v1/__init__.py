"""Version 1 of the HTTP API.

:data:`router` aggregates every v1 route module (one module per resource) into
a single ``APIRouter``. The app factory mounts it under the ``/`` prefix today;
mounting it under ``/v1`` later is a one-line change that leaves the route
modules untouched.
"""

from __future__ import annotations

from fastapi import APIRouter

from mf_service.api.v1 import health, version

router = APIRouter()
router.include_router(health.router)
router.include_router(version.router)

__all__ = ["router"]
