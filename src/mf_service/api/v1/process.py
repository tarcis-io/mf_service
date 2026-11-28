"""The ``POST /process`` route — start processing a batch.

Processing is a **general** operation, not scoped to a user: there is no
``X-User-Id`` here. The route selects a batch (a specific id, or the oldest batch
in the ``created`` status when none is given), flips it to ``processing``,
returns it immediately, and runs the engine pipeline
(:mod:`mf_service.processing`) in a background job that moves the batch to
``processed`` when the pipeline finishes — or to ``error`` with the failure text
if any task raises. Produced files (today just ``annotations.csv``) are uploaded
to the batch's storage folder.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from mf_service.core.config import Settings, get_settings
from mf_service.core.deps import (
    BatchRepositoryDep,
    BatchStorageDep,
    GNPSProcessorDep,
)
from mf_service.domain import BatchStatus
from mf_service.processing import run_pipeline
from mf_service.repository import BatchNotFoundError, BatchRepository
from mf_service.schemas import BatchResponse, ProcessRequest
from mf_service.storage import BatchStorage

if TYPE_CHECKING:  # type-checker only; no runtime engine import
    from mf_engine.processors.gnps import GNPSProcessor

router = APIRouter(tags=["process"])

__all__ = ["router"]


def _content_type_for(filename: str) -> str:
    """MIME type for a produced file from its extension (``.tsv`` → TSV, else CSV)."""
    return (
        "text/tab-separated-values"
        if filename.endswith(".tsv")
        else "text/csv"
    )


def _run_pipeline_then_finish(
    repo: BatchRepository,
    storage: BatchStorage,
    processor: GNPSProcessor,
    batch_id: str,
) -> None:
    """Background job: run the pipeline, upload outputs, then finish the batch.

    Runs every pipeline task over the batch, uploads whatever files they
    produced to ``<batch id>/<name>``, and moves the batch to ``processed``. Any
    failure moves it to ``error`` with the exception text instead. Both outcomes
    stamp ``updated``. If the batch was deleted mid-run, the status write raises
    :class:`~mf_service.repository.BatchNotFoundError`, which is a benign no-op.
    """
    try:
        outputs = run_pipeline(batch_id, storage, processor)
        for name, data in outputs.items():
            storage.upload(
                batch_id, name, data, content_type=_content_type_for(name)
            )
        repo.update_status(batch_id, BatchStatus.PROCESSED)
    except BatchNotFoundError:
        return  # batch deleted mid-processing; nothing to mark
    except Exception as exc:  # noqa: BLE001 - any task failure → error state
        try:
            repo.update_status(
                batch_id, BatchStatus.ERROR, error_message=str(exc)
            )
        except BatchNotFoundError:
            return  # deleted after the failure; nothing to mark


@router.post(
    "/process",
    response_model=BatchResponse,
    summary="Start processing a batch",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "No such batch, or no batch waiting to process"
        },
        status.HTTP_409_CONFLICT: {
            "description": "The batch is not in the 'created' status"
        },
    },
)
def process_batch(
    repo: BatchRepositoryDep,
    storage: BatchStorageDep,
    processor: GNPSProcessorDep,
    background: BackgroundTasks,
    body: ProcessRequest | None = None,
) -> BatchResponse:
    """Start processing a batch and return it (status ``processing``).

    With a ``batch_id`` in the body, processes that batch — ``404`` if it does
    not exist, ``409`` if it is not in the ``created`` status. With no body (or
    no ``batch_id``), processes the oldest ``created`` batch — ``404`` if none is
    waiting. Not scoped to a user: any batch is eligible.

    The batch is flipped to ``processing`` and returned at once; the engine
    pipeline runs in a background job that then moves it to ``processed`` (or
    ``error`` on failure).
    """
    batch_id = body.batch_id if body is not None else None
    if batch_id is not None:
        batch = repo.claim_for_processing(batch_id)
    else:
        claimed = repo.claim_oldest_created()
        if claimed is None:
            raise BatchNotFoundError("no batch waiting to process")
        batch = claimed

    background.add_task(
        _run_pipeline_then_finish, repo, storage, processor, batch.id
    )
    return BatchResponse.from_domain(batch)


@router.post(
    "/process/timeouts",
    response_model=list[BatchResponse],
    summary="Time out stuck in-progress batches",
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "older_than_seconds must be a positive integer"
        },
    },
)
def timeout_stuck_batches(
    repo: BatchRepositoryDep,
    settings: Annotated[Settings, Depends(get_settings)],
    older_than_seconds: Annotated[
        int | None,
        Query(
            ge=1,
            description=(
                "Age in seconds past which an in-progress batch is considered "
                "stuck. Omit to use MF_SERVICE_PROCESSING_TIMEOUT_SECONDS."
            ),
        ),
    ] = None,
) -> list[BatchResponse]:
    """Move batches stuck mid-processing to ``error`` and return them.

    Sweeps every batch whose status is ``creating`` or ``processing`` and whose
    ``updated`` is older than the threshold — a batch whose background job died
    leaves it wedged in such a state — setting each to ``error`` with a timeout
    message. Not scoped to a user; it is a global maintenance operation (so no
    ``X-User-Id``), meant to be run on a schedule.

    The threshold is ``older_than_seconds`` when given (must be ``>= 1``),
    otherwise ``MF_SERVICE_PROCESSING_TIMEOUT_SECONDS`` (default one hour).
    Returns the batches it timed out (empty if none were stuck).
    """
    seconds = (
        older_than_seconds
        if older_than_seconds is not None
        else settings.processing_timeout_seconds
    )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    timed_out = repo.fail_timed_out(
        cutoff,
        error_message=(
            f"Processing timed out: no status change in over {seconds} seconds."
        ),
    )
    return [BatchResponse.from_domain(b) for b in timed_out]
