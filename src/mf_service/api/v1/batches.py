"""The ``/batches`` routes — CRUD over :class:`~mf_service.domain.Batch`.

Every route is scoped to the calling user (the ``X-User-Id`` header, via
:data:`~mf_service.deps.UserIdDep`): a user only ever sees or affects their own
batches, and another user's batch id reads as *not found*.

The routes stay thin. Persistence and the per-user name-uniqueness invariant
live in the :class:`~mf_service.repository.BatchRepository`; the routes only
translate its two domain errors into HTTP status codes — a duplicate name into
``409 Conflict`` and a missing batch into ``404 Not Found`` — in one place via
the handlers registered below.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mf_service.deps import BatchRepositoryDep, UserIdDep
from mf_service.domain import Batch
from mf_service.schemas import (
    BatchCreateRequest,
    BatchResponse,
    BatchUpdateRequest,
)

router = APIRouter(prefix="/batches", tags=["batches"])


@router.post(
    "",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a batch",
    responses={status.HTTP_409_CONFLICT: {"description": "Name already in use"}},
)
def create_batch(
    body: BatchCreateRequest,
    user_id: UserIdDep,
    repo: BatchRepositoryDep,
) -> BatchResponse:
    """Create a batch for the calling user.

    The id, creation time, and initial ``created`` status are assigned by the
    server. Fails with ``409`` if the user already has a batch with this name.
    """
    batch = Batch(user_id=user_id, name=body.name)
    created = repo.create(batch)
    return BatchResponse.from_domain(created)


@router.get(
    "",
    response_model=list[BatchResponse],
    summary="List the caller's batches",
)
def list_batches(user_id: UserIdDep, repo: BatchRepositoryDep) -> list[BatchResponse]:
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
    """Rename a batch. Only the name is mutable; all other fields are
    server-managed. Fails with ``404`` if it does not exist or ``409`` if the
    new name is already taken by another of the user's batches.
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
) -> None:
    """Delete one of the caller's batches (``404`` if it does not exist)."""
    repo.delete(user_id, batch_id)


# Error-to-HTTP mapping. Registered on the router so the routes above can raise
# the repository's domain errors (indirectly) and get consistent responses,
# keeping status-code policy in one spot rather than in every handler.
__all__ = [
    "router",
    "batch_not_found_handler",
    "duplicate_name_handler",
]


async def batch_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map a missing batch to ``404 Not Found``."""
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"Batch not found: {exc}"},
    )


async def duplicate_name_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map a duplicate name to ``409 Conflict``."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": f"Batch name already in use: {exc}"},
    )
