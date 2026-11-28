"""Schema for the ``/version`` endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


__all__ = ["VersionResponse"]
