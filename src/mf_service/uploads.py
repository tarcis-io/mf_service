"""Batch file uploads — the required file set and the background upload task.

``POST /batches`` accepts a fixed set of input files (:data:`REQUIRED_FILES`)
and schedules :func:`upload_batch_files` to run after the response is sent. The
task pushes each file to object storage under ``<batch_id>/<filename>`` and
advances the batch's status from ``creating`` to ``created`` once every file is
stored; any failure flips the batch to ``error`` with the exception text.

The task takes already-read ``bytes`` rather than the request's ``UploadFile``
objects on purpose: a FastAPI ``BackgroundTask`` runs *after* the response, by
which point the upload files are closed. The route reads them while the request
is still open and hands the bytes here.
"""

from __future__ import annotations

from mf_service.domain import BatchStatus
from mf_service.repository import BatchNotFoundError, BatchRepository
from mf_service.storage import StorageGateway

# The exact files a batch must be created with, as {form field: stored filename}.
# This is the single source of truth: the create route declares one required
# UploadFile per field here, and maps each to its filename through this dict —
# so field and filename can never drift out of lockstep. A missing field is a
# 422 before this task ever runs.
REQUIRED_FILE_NAMES: dict[str, str] = {
    "mzmine_annotations": "mzmine_annotations.csv",
    "mzmine_full_feature_table": "mzmine_full_feature_table.csv",
    "mzmine_metadata": "mzmine_metadata.csv",
    "gnps_feature_table_reformated": "gnps_feature_table_reformated.csv",
    "gnps_merged_results": "gnps_merged_results.tsv",
}

# The stored filenames, derived from the mapping above (order follows it).
REQUIRED_FILES: tuple[str, ...] = tuple(REQUIRED_FILE_NAMES.values())


def _content_type(filename: str) -> str:
    """MIME type for a batch input file from its extension.

    Batch inputs are only ever ``.csv`` or ``.tsv`` (see
    :data:`REQUIRED_FILE_NAMES`), so anything that is not ``.tsv`` is CSV.
    """
    return (
        "text/tab-separated-values"
        if filename.endswith(".tsv")
        else "text/csv"
    )


def upload_batch_files(
    repository: BatchRepository,
    storage: StorageGateway,
    batch_id: str,
    files: dict[str, bytes],
) -> None:
    """Upload a batch's files to storage, tracking status on the repository.

    The batch is already ``creating`` when this runs (its born state). The task
    writes each ``(name, data)`` in ``files`` to ``<batch_id>/<name>`` and then
    marks it ``created``. On any error the batch is set to ``error`` with the
    failure text and the exception is swallowed — this runs as a background task
    with no caller to propagate to, so the status is how failure is reported.

    If the batch was deleted before the task runs, the status writes raise
    :class:`~mf_service.repository.BatchNotFoundError`; that is treated as a
    benign no-op rather than an error state on a batch that no longer exists.
    The leading write re-asserts ``creating`` purely to detect that case up
    front, so a batch deleted before the task starts never gets orphan files
    uploaded for it.
    """
    try:
        repository.update_status(batch_id, BatchStatus.CREATING)
        for name, data in files.items():
            storage.upload(
                f"{batch_id}/{name}", data, content_type=_content_type(name)
            )
        repository.update_status(batch_id, BatchStatus.CREATED)
    except BatchNotFoundError:
        # The batch was deleted mid-upload; nothing to mark.
        return
    except Exception as exc:  # background task is the last line of defence
        try:
            repository.update_status(
                batch_id, BatchStatus.ERROR, error_message=str(exc)
            )
        except BatchNotFoundError:
            return
