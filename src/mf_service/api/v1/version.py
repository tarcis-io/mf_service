"""The ``/version`` route — service and engine identity.

Returns the application's name, description, and version together with the
version of the :mod:`mf_engine` core engine the service is running against. The
service identity comes from the injected :class:`~mf_service.core.config.Settings`
(sourced from the package metadata), and the engine version from
:func:`~mf_service.core.engine.engine_version` (the engine's own single source
of truth), so neither value is duplicated here and the projects never disagree
about which build is deployed.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from mf_service.core.config import Settings, get_settings
from mf_service.core.engine import engine_version
from mf_service.schemas import VersionResponse

router = APIRouter(tags=["version"])


@router.get("/version", response_model=VersionResponse, summary="Version info")
def get_version(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VersionResponse:
    """Report the service identity and the mf_engine version in use."""
    return VersionResponse(
        name=settings.name,
        description=settings.description,
        version=settings.version,
        engine_version=engine_version(),
    )
