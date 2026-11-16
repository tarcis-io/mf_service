"""The ``/version`` route — service and engine identity.

Returns the application's name, description, and version together with the
version of the :mod:`mf_engine` core engine the service is running against.
The engine version is read from ``mf_engine.__version__`` (the engine's own
single source of truth), so the two projects never disagree about which engine
build is deployed.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from mf_service.config import Settings, get_settings
from mf_service.schemas import VersionResponse

router = APIRouter(tags=["version"])


@router.get("/version", response_model=VersionResponse, summary="Version info")
def version(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VersionResponse:
    """Report the service identity and the mf_engine version in use."""
    # Imported inside the handler, not at module scope, so mounting the router
    # (and starting the app) does not pull in the engine until /version is hit.
    import mf_engine

    return VersionResponse(
        name=settings.name,
        description=settings.description,
        version=settings.version,
        engine_version=mf_engine.__version__,
    )
