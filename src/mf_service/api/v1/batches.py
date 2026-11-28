"""The ``/batches`` routes — CRUD over :class:`~mf_service.domain.Batch`.

Every route is scoped to the calling user (the ``X-User-Id`` header, via
:data:`~mf_service.core.deps.UserIdDep`): a user only ever sees or affects their
own batches, and another user's batch id reads as *not found*.

Creating a batch is a multipart upload of its required files (see
:data:`~mf_service.domain.REQUIRED_BATCH_FILES`): the route validates and reads
them, persists the batch as ``creating``, returns it immediately, and uploads
the files to object storage in a background job that flips the batch to
``created`` (or ``error``) when it finishes. Deleting a batch also deletes its
stored files.

The routes stay thin. Persistence and the per-user name-uniqueness invariant
live in the :class:`~mf_service.repository.BatchRepository`; the routes only
translate domain errors into HTTP status codes — a duplicate name into
``409 Conflict`` and a missing batch or file into ``404 Not Found`` — in one
place via the handlers registered at the bottom of this module (wired up by the
app factory).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, Response

from mf_service.core.config import Settings, get_settings
from mf_service.core.deps import BatchRepositoryDep, BatchStorageDep, UserIdDep
from mf_service.domain import REQUIRED_BATCH_FILES, Batch, BatchStatus
from mf_service.repository import BatchRepository
from mf_service.schemas import BatchFileResponse, BatchResponse, BatchUpdateRequest
from mf_service.storage import BatchStorage

router = APIRouter(prefix="/batches", tags=["batches"])

# Public surface: the router plus the exception handlers the app factory
# registers (defined at the bottom of this module).
__all__ = [
    "router",
    "batch_not_found_handler",
    "duplicate_name_handler",
    "object_not_found_handler",
    "batch_not_processable_handler",
]


def _clean_name(name: str) -> str:
    """Trim ``name`` and reject a blank/whitespace-only or over-long value.

    Mirrors the JSON models' name rules (1–255 chars after trimming) for the
    multipart ``name`` form field, which pydantic does not validate for us.
    """
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="name must be 1–255 characters after trimming.",
        )
    return cleaned


def _read_within_limit(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload fully, rejecting one larger than ``max_bytes`` with 413."""
    data = file.file.read()
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"{file.filename or 'file'} exceeds the "
                f"{max_bytes}-byte per-file limit."
            ),
        )
    return data


def _upload_files_then_finish(
    repo: BatchRepository,
    storage: BatchStorage,
    batch_id: str,
    files: list[tuple[str, bytes, str | None]],
) -> None:
    """Background job: upload every file, then flip the batch's status.

    Uploads each file to ``<bucket>/<batch id>/<filename>``; on success moves the
    batch to ``created``, and on any failure to ``error`` with the message. Both
    outcomes stamp ``updated`` (the repository's ``update_status`` does that).
    """
    try:
        for filename, data, content_type in files:
            storage.upload(batch_id, filename, data, content_type=content_type)
    except Exception as exc:  # noqa: BLE001 - any upload failure → error state
        repo.update_status(
            batch_id, BatchStatus.ERROR, error_message=str(exc)
        )
    else:
        repo.update_status(batch_id, BatchStatus.CREATED)


def _content_disposition(filename: str) -> str:
    """Build a safe ``attachment`` ``Content-Disposition`` for a download.

    ``filename`` is caller-controlled (a URL path segment), so interpolating it
    raw would be unsafe — a ``"`` breaks out of the quoted form and a control
    character could corrupt the header. Per RFC 6266 we emit a plain ``filename``
    (ASCII, quotes/backslashes/control chars stripped) as a legacy fallback plus
    an RFC 5987 ``filename*`` carrying the full UTF-8 name percent-encoded.
    """
    ascii_fallback = (
        "".join(ch for ch in filename if ch.isprintable() and ch not in '"\\')
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


@router.post(
    "",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a batch",
    responses={
        status.HTTP_409_CONFLICT: {"description": "Name already in use"},
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {
            "description": "An uploaded file exceeds the per-file size limit"
        },
    },
)
def create_batch(
    user_id: UserIdDep,
    repo: BatchRepositoryDep,
    storage: BatchStorageDep,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    name: Annotated[str, Form(description="Batch name; unique per user.")],
    mzmine_annotations: Annotated[
        UploadFile, File(alias="mzmine_annotations.csv")
    ],
    mzmine_full_feature_table: Annotated[
        UploadFile, File(alias="mzmine_full_feature_table.csv")
    ],
    mzmine_metadata: Annotated[UploadFile, File(alias="mzmine_metadata.csv")],
    gnps_feature_table_reformated: Annotated[
        UploadFile, File(alias="gnps_feature_table_reformated.csv")
    ],
    gnps_merged_results: Annotated[
        UploadFile, File(alias="gnps_merged_results.tsv")
    ],
) -> BatchResponse:
    """Create a batch from its required file uploads.

    A ``multipart/form-data`` request with a ``name`` field and one part per
    file in :data:`~mf_service.domain.REQUIRED_BATCH_FILES` (each at most
    ``MF_SERVICE_MAX_UPLOAD_BYTES``). The owning user comes from the
    ``X-User-Id`` header; the id, timestamps, and initial ``creating`` status
    are server-assigned.

    The batch is returned as soon as it is persisted (status ``creating``); the
    files are uploaded to storage in a background job that then moves it to
    ``created`` — or ``error`` if an upload fails. Returns ``409`` if the name is
    taken, ``413`` if a file is too large.
    """
    cleaned_name = _clean_name(name)

    # Collect the uploads in the canonical order and read each within the limit.
    # (A missing part is already a 422 from FastAPI's required-parameter check.)
    uploads = {
        "mzmine_annotations.csv": mzmine_annotations,
        "mzmine_full_feature_table.csv": mzmine_full_feature_table,
        "mzmine_metadata.csv": mzmine_metadata,
        "gnps_feature_table_reformated.csv": gnps_feature_table_reformated,
        "gnps_merged_results.tsv": gnps_merged_results,
    }
    files = [
        (
            filename,
            _read_within_limit(uploads[filename], settings.max_upload_bytes),
            uploads[filename].content_type,
        )
        for filename in REQUIRED_BATCH_FILES
    ]

    batch = Batch(user_id=user_id, name=cleaned_name)
    created = repo.create(batch)

    # Upload after responding; the task owns the created→created/error move.
    background.add_task(
        _upload_files_then_finish, repo, storage, created.id, files
    )
    return BatchResponse.from_domain(created)


@router.get(
    "",
    response_model=list[BatchResponse],
    summary="List the caller's batches",
)
def list_batches(
    user_id: UserIdDep, repo: BatchRepositoryDep
) -> list[BatchResponse]:
    """Return all of the calling user's batches, newest first."""
    return [BatchResponse.from_domain(b) for b in repo.list_for_user(user_id)]


@router.get(
    "/{batch_id}",
    response_model=BatchResponse,
    summary="Get a batch",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No such batch"}},
)
def get_batch(
    batch_id: str,
    user_id: UserIdDep,
    repo: BatchRepositoryDep,
) -> BatchResponse:
    """Return one of the caller's batches by id (``404`` if it does not exist)."""
    batch = repo.get(user_id, batch_id)
    return BatchResponse.from_domain(batch)


@router.get(
    "/{batch_id}/files",
    response_model=list[BatchFileResponse],
    summary="List a batch's files",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No such batch"}},
)
def list_batch_files(
    batch_id: str,
    user_id: UserIdDep,
    repo: BatchRepositoryDep,
    storage: BatchStorageDep,
) -> list[BatchFileResponse]:
    """List the files stored for one of the caller's batches.

    Resolves the batch by ``(user_id, batch_id)`` first — a missing batch, or one
    owned by another user, is a ``404`` — then lists the objects actually in
    storage under ``<batch_id>/``. A batch whose upload has not finished (or
    failed) may list fewer than the full set.
    """
    repo.get(user_id, batch_id)  # ownership/existence check (raises -> 404)
    return [
        BatchFileResponse.from_stored(f) for f in storage.list_files(batch_id)
    ]


# Declared before ``/files/{filename}`` so the literal ``/files/archive`` path
# wins over the catch-all filename parameter (routes match in declaration order).
@router.get(
    "/{batch_id}/files/archive",
    response_class=Response,
    summary="Download all of a batch's files as a zip",
    responses={
        status.HTTP_200_OK: {
            "content": {"application/zip": {}},
            "description": "A zip archive of the batch's stored files.",
        },
        status.HTTP_404_NOT_FOUND: {"description": "No such batch"},
    },
)
def download_batch_archive(
    batch_id: str,
    user_id: UserIdDep,
    repo: BatchRepositoryDep,
    storage: BatchStorageDep,
) -> Response:
    """Download all of one of the caller's batch files as a single zip.

    Resolves the batch by ``(user_id, batch_id)`` first — a missing batch, or one
    owned by another user, is a ``404`` — then zips whatever is stored under
    ``<batch_id>/`` (each entry named by its bare filename). A batch whose upload
    has not finished, or failed, yields fewer entries — an empty batch yields a
    valid empty zip.

    The archive is built in memory, so the whole set of files (each capped at the
    per-file upload limit) is held at once; fine at the current handful of files,
    but the spot to revisit (streamed zip, or signed URLs) if the set grows large.
    """
    repo.get(user_id, batch_id)  # ownership/existence check (raises -> 404)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for stored in storage.list_files(batch_id):
            archive.writestr(
                stored.filename, storage.download(batch_id, stored.filename)
            )
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(f"{batch_id}.zip")},
    )


@router.get(
    "/{batch_id}/files/{filename}",
    response_class=Response,
    summary="Download one of a batch's files",
    responses={
        status.HTTP_200_OK: {
            "content": {"application/octet-stream": {}},
            "description": "The file's bytes.",
        },
        status.HTTP_404_NOT_FOUND: {"description": "No such batch or file"},
    },
)
def download_batch_file(
    batch_id: str,
    filename: str,
    user_id: UserIdDep,
    repo: BatchRepositoryDep,
    storage: BatchStorageDep,
) -> Response:
    """Download one file from one of the caller's batches.

    Resolves the batch by ``(user_id, batch_id)`` first — a missing batch, or one
    owned by another user, is a ``404`` — then returns the object at
    ``<batch_id>/<filename>``. A missing file is also a ``404``. ``filename`` is a
    single path segment (it cannot contain ``/``), so it cannot escape the
    batch's folder.
    """
    repo.get(user_id, batch_id)  # ownership/existence check (raises -> 404)
    data = storage.download(batch_id, filename)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.patch(
    "/{batch_id}",
    response_model=BatchResponse,
    summary="Rename a batch",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No such batch"},
        status.HTTP_409_CONFLICT: {"description": "Name already in use"},
    },
)
def update_batch(
    batch_id: str,
    body: BatchUpdateRequest,
    user_id: UserIdDep,
    repo: BatchRepositoryDep,
) -> BatchResponse:
    """Rename a batch.

    Only the name is mutable; all other fields are server-managed. Fails with
    ``404`` if it does not exist or ``409`` if the new name is already taken by
    another of the user's batches.
    """
    batch = repo.rename(user_id, batch_id, body.name)
    return BatchResponse.from_domain(batch)


@router.delete(
    "/{batch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a batch",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No such batch"}},
)
def delete_batch(
    batch_id: str,
    user_id: UserIdDep,
    repo: BatchRepositoryDep,
    storage: BatchStorageDep,
) -> None:
    """Delete one of the caller's batches and its stored files.

    Deletes the batch record (``404`` if it does not exist or is another user's),
    then removes the batch's whole storage folder (``<bucket>/<batch id>/``). The
    record is deleted first, so a 404 never touches storage; the folder delete is
    a no-op when the batch had no uploaded files.
    """
    repo.delete(user_id, batch_id)
    storage.delete_batch(batch_id)


# Error-to-HTTP mapping, registered by the app factory so the routes can raise
# the repository's domain errors and get consistent responses in one place.


def _error_handler(
    status_code: int, detail_prefix: str
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Build a handler whose body is ``{"detail": "<prefix>: <exc>"}``."""

    async def _handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"detail": f"{detail_prefix}: {exc}"},
        )

    return _handler


batch_not_found_handler = _error_handler(
    status.HTTP_404_NOT_FOUND, "Batch not found"
)
duplicate_name_handler = _error_handler(
    status.HTTP_409_CONFLICT, "Batch name already in use"
)
object_not_found_handler = _error_handler(
    status.HTTP_404_NOT_FOUND, "File not found"
)
batch_not_processable_handler = _error_handler(
    status.HTTP_409_CONFLICT, "Batch is not processable"
)
