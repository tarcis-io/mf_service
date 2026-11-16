"""API schemas — the typed request/response contracts for the HTTP API.

Keeping the models in one place (rather than inline in the routes) means the
API surface is documented and validated from a single source, and new endpoints
reuse or extend these models instead of passing around loose dicts. Response
models also map from the storage-agnostic :mod:`mf_service.domain` types, so the
wire format has one definition.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import Form
from pydantic import BaseModel, Field, StringConstraints

from mf_service.domain import Batch, BatchStatus


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


def _batch_name_field() -> Any:
    """A fresh batch-name ``Field`` (required, 1–255 chars).

    A factory, not a shared ``Field`` instance, so each model gets its own
    ``FieldInfo`` (reusing one across models is a Pydantic footgun) while the
    constraint stays defined in one place. The return is typed ``Any`` because
    ``Field()`` yields a ``FieldInfo`` default; the ``name: str`` annotation on
    each model types the attribute.
    """
    return Field(
        min_length=1,
        max_length=255,
        description="Human-readable batch name; unique per user.",
        examples=["Spring run 2026"],
    )


# Batch-name type for the multipart create endpoint. ``POST /batches`` is
# multipart (it also carries the input files), so ``name`` is a Form field
# rather than a JSON body. StringConstraints strips whitespace first — so an
# all-blank name trims to empty and fails min_length — then enforces the same
# 1–255-char bound as the JSON models; Form carries the OpenAPI docs.
BatchNameForm = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    Form(
        description="Human-readable batch name; unique per user.",
        examples=["Spring run 2026"],
    ),
]


class BatchUpdateRequest(BaseModel):
    """Body of ``PATCH /batches/{id}``. Only the name may be updated."""

    model_config = {"str_strip_whitespace": True}

    name: str = _batch_name_field()


class BatchResponse(BaseModel):
    """A batch as returned by the API."""

    id: str = Field(
        description="Server-assigned batch id (UUID4).",
        examples=["3f2504e0-4f89-41d3-9a0c-0305e82c3301"],
    )
    user_id: str = Field(
        description="Id of the user who owns the batch.",
        examples=["user-123"],
    )
    name: str = Field(
        description="Human-readable batch name; unique per user.",
        examples=["Spring run 2026"],
    )
    created: datetime = Field(
        description="When the batch was created (UTC).",
        examples=["2026-01-15T09:30:00Z"],
    )
    status: BatchStatus = Field(
        description="Lifecycle status of the batch.",
        examples=[BatchStatus.CREATING],
    )
    error_message: str | None = Field(
        default=None,
        description="Failure detail; set only when status is 'error'.",
        examples=[None],
    )

    @classmethod
    def from_domain(cls, batch: Batch) -> BatchResponse:
        """Build the response model from a domain :class:`Batch`."""
        return cls(
            id=batch.id,
            user_id=batch.user_id,
            name=batch.name,
            created=batch.created,
            status=batch.status,
            error_message=batch.error_message,
        )
