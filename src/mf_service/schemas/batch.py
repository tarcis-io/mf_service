"""Schemas for the ``/batches`` endpoints — the request/response contracts.

The wire format for a :class:`~mf_service.domain.Batch` is defined once in
:class:`BatchResponse`, mapped from the entity by :meth:`BatchResponse.from_domain`.
Creating a batch is a multipart file upload (its ``name`` is a form field and
its validation lives in the route), so only the rename body is modelled here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from mf_service.domain import Batch, BatchStatus
from mf_service.storage import StoredFile


class BatchUpdateRequest(BaseModel):
    """Body of ``PATCH /batches/{id}``. Only the name may be updated.

    The name is trimmed and must be 1–255 characters. An unknown field is
    rejected (``extra="forbid"``) so a caller's typo — or an attempt to set a
    server-managed field — surfaces as a 422 rather than being silently dropped.
    """

    model_config = {"str_strip_whitespace": True, "extra": "forbid"}

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable batch name; unique per user.",
        examples=["Spring run 2026"],
    )


class BatchResponse(BaseModel):
    """A batch as returned by the API."""

    # Populate straight from a domain Batch's attributes (see from_domain), so a
    # new field flows through without a hand-written copy to keep in sync.
    model_config = {"from_attributes": True}

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
    updated: datetime = Field(
        description="When the batch's status last changed (UTC); equals "
        "'created' until the first status change.",
        examples=["2026-01-15T09:30:00Z"],
    )
    status: BatchStatus = Field(
        description="Lifecycle status of the batch.",
        examples=["creating"],
    )
    error_message: str | None = Field(
        default=None,
        description="Failure detail; set only when status is 'error'.",
        examples=["Processing failed: engine timed out"],
    )

    @classmethod
    def from_domain(cls, batch: Batch) -> BatchResponse:
        """Build the response model from a domain :class:`Batch`."""
        return cls.model_validate(batch)


class BatchFileResponse(BaseModel):
    """One of a batch's stored files, as returned by the files listing."""

    # Populated from a storage StoredFile's attributes (see from_stored); the
    # field names match, so no hand-written copy to keep in sync.
    model_config = {"from_attributes": True}

    filename: str = Field(
        description="The file's name within the batch.",
        examples=["mzmine_annotations.csv"],
    )
    size: int = Field(
        description="Size of the file in bytes.",
        examples=[20480],
    )
    content_type: str = Field(
        description="The file's MIME type.",
        examples=["text/csv"],
    )
    updated: datetime | None = Field(
        default=None,
        description="When the file was last written (UTC), if known.",
        examples=["2026-01-15T09:30:05Z"],
    )

    @classmethod
    def from_stored(cls, stored: StoredFile) -> BatchFileResponse:
        """Build the response model from a storage :class:`StoredFile`."""
        return cls.model_validate(stored)


__all__ = ["BatchUpdateRequest", "BatchResponse", "BatchFileResponse"]
