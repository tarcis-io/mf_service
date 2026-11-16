"""Response schemas — the typed contracts for the HTTP API.

Keeping the response models in one place (rather than inline in the routes)
means the API surface is documented and validated from a single source, and
new endpoints reuse or extend these models instead of returning loose dicts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness payload returned by ``GET /health``."""

    status: str = Field(
        default="ok",
        description="Service health status; 'ok' when the service is live.",
        examples=["ok"],
    )


class VersionResponse(BaseModel):
    """Identity/version payload returned by ``GET /version``."""

    name: str = Field(
        description="The application name.",
        examples=["mf_service"],
    )
    description: str = Field(
        description="The application description.",
        examples=[
            "MassFlow service: a FastAPI service exposing the MassFlow "
            "engine over HTTP."
        ],
    )
    version: str = Field(
        description="The application version.",
        examples=["0.1.0"],
    )
    engine_version: str = Field(
        description="The version of the mf_engine core engine in use.",
        examples=["0.1.0"],
    )
