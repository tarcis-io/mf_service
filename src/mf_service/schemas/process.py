"""Schema for the ``/process`` endpoint — its optional request body."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProcessRequest(BaseModel):
    """Body of ``POST /process``. All fields optional; the body itself may be
    omitted entirely to process the oldest ``created`` batch.
    """

    model_config = {"str_strip_whitespace": True, "extra": "forbid"}

    batch_id: str | None = Field(
        default=None,
        description=(
            "Id of the batch to process. Omit to process the oldest batch in "
            "the 'created' status."
        ),
        examples=["3f2504e0-4f89-41d3-9a0c-0305e82c3301"],
    )


__all__ = ["ProcessRequest"]
