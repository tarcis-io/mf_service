"""Background jobs — the recurring batch-processing worker.

:func:`process_next_batch` claims the oldest ``created`` batch (flipping it to
``processing``), runs the engine :mod:`~mf_service.processing` pipeline over its
files, uploads the results, and moves the batch to ``processed`` — or to
``error`` with the failure text if the pipeline raises. It processes **one**
batch per call and returns the batch it handled (or ``None`` when the queue is
empty), so a scheduler can invoke it on a fixed cadence (e.g. every 5 minutes).

The claim is atomic (see
:meth:`~mf_service.repository.BatchRepository.claim_oldest_created`), so
overlapping runs never pick up the same batch. Results are uploaded only after
the whole pipeline succeeds, so a failure leaves no partial output behind.

:func:`main` is the console entry point (the ``mf-service-worker`` script): it
builds the repository, storage, and processor from settings and runs one pass.
Point a scheduler at it — Cloud Scheduler hitting a tiny runner, a cron entry,
or a Cloud Run job on a 5-minute schedule (see the README).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mf_service.domain import Batch, BatchStatus
from mf_service.processing import DEFAULT_PIPELINE, PipelineTask, run_pipeline
from mf_service.repository import BatchNotFoundError, BatchRepository
from mf_service.storage import StorageGateway

if TYPE_CHECKING:  # type-checker only; no runtime engine import
    from mf_engine.processors.gnps import GNPSProcessor


def process_next_batch(
    repository: BatchRepository,
    storage: StorageGateway,
    processor: GNPSProcessor,
    *,
    pipeline: tuple[PipelineTask, ...] = DEFAULT_PIPELINE,
) -> Batch | None:
    """Claim and process the oldest waiting batch; return it, or ``None``.

    Flips the oldest ``created`` batch to ``processing`` (atomically), runs the
    pipeline, uploads its outputs, and marks the batch ``processed``. Any
    failure marks it ``error`` with the exception text instead. Returns the
    batch handled, or ``None`` when nothing is waiting.
    """
    batch = repository.claim_oldest_created()
    if batch is None:
        return None
    try:
        outputs = run_pipeline(
            batch.id, storage, processor, pipeline=pipeline
        )
        for name, data in outputs.items():
            storage.upload(f"{batch.id}/{name}", data, content_type="text/csv")
        return repository.update_status(batch.id, BatchStatus.PROCESSED)
    except BatchNotFoundError:
        # The batch was deleted mid-processing; nothing to mark.
        return None
    except Exception as exc:
        # Record the failure on the batch. If it was deleted in the meantime,
        # the status write raises BatchNotFoundError, which is a benign no-op.
        try:
            return repository.update_status(
                batch.id, BatchStatus.ERROR, error_message=str(exc)
            )
        except BatchNotFoundError:
            return None


def main() -> None:  # pragma: no cover - thin wiring around tested pieces
    """Run one processing pass, building dependencies from settings."""
    from mf_service.config import get_settings
    from mf_service.deps import (
        get_batch_repository,
        get_gnps_processor,
        get_storage_gateway,
    )

    settings = get_settings()
    process_next_batch(
        get_batch_repository(settings),
        get_storage_gateway(settings),
        get_gnps_processor(),
    )


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    main()
