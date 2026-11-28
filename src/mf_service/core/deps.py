"""Shared FastAPI dependencies — the seam between routes and their backends.

Routes obtain the calling user's id and the batch repository through providers
declared here (via ``Depends``) rather than constructing them inline. This keeps
construction in one place, lets tests swap implementations with
``app.dependency_overrides``, and gives a single spot to decide a dependency's
lifetime. The ``*Dep`` ``Annotated`` aliases let routes write ``user_id:
UserIdDep`` instead of repeating the ``Depends`` wiring.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, HTTPException, status

from mf_service.core.config import Settings, get_settings
from mf_service.core.engine import get_gnps_processor
from mf_service.repository import BatchRepository, FirestoreBatchRepository
from mf_service.storage import BatchStorage, GCSBatchStorage

if TYPE_CHECKING:  # type-checker only; no runtime GCP / engine imports
    from google.cloud.firestore import Client as FirestoreClient
    from google.cloud.storage import Client as StorageClient
    from mf_engine.processors.gnps import GNPSProcessor


def get_user_id(x_user_id: Annotated[str, Header()]) -> str:
    """Return the calling user's id (trimmed) from the ``X-User-Id`` header.

    The service is called by another service that identifies the end user via
    this header, and every batch operation is scoped by this value — so one
    user's batches stay invisible to another. A blank or whitespace-only value
    is rejected with 422 (a missing header is FastAPI's own 422), so a blank id
    can never become a shared namespace that owns batches.
    """
    user_id = x_user_id.strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="X-User-Id header must not be blank.",
        )
    return user_id


UserIdDep = Annotated[str, Depends(get_user_id)]


@lru_cache
def _build_firestore_client(
    project: str | None, database: str
) -> FirestoreClient:  # pragma: no cover - constructs a live client (needs GCP)
    """Build (once per project+database) the Firestore client.

    Lazily imported so importing this module never requires the Firestore
    library. ``project`` left ``None`` lets the client auto-detect it from
    Application Default Credentials; ``database`` selects the database
    (``"(default)"`` for the unnamed one). A separate seam so tests can patch
    it and exercise the provider without a live connection.
    """
    from google.cloud import firestore

    if project:
        return firestore.Client(project=project, database=database)
    return firestore.Client(database=database)


def get_batch_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BatchRepository:
    """Return the :class:`~mf_service.repository.BatchRepository`.

    Builds the Firestore-backed repository from settings (database and
    collection name, project auto-detected unless configured). Tests override it
    with ``app.dependency_overrides[get_batch_repository]`` to supply the
    in-memory implementation, so no live Firestore is needed to exercise routes.
    """
    client = _build_firestore_client(
        settings.firestore_project, settings.firestore_database
    )
    return FirestoreBatchRepository(
        client, collection=settings.batches_collection
    )


BatchRepositoryDep = Annotated[BatchRepository, Depends(get_batch_repository)]


@lru_cache
def _build_storage_client(
    project: str | None,
) -> StorageClient:  # pragma: no cover - constructs a live client (needs GCP)
    """Build (once per project) the Cloud Storage client.

    Lazily imported so importing this module never requires the Storage library.
    ``project`` left ``None`` lets the client auto-detect it from Application
    Default Credentials (the same GCP project as Firestore). A separate seam so
    tests can patch it and exercise the provider without a live connection.
    """
    from google.cloud import storage

    if project:
        return storage.Client(project=project)
    return storage.Client()


def get_batch_storage(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BatchStorage:
    """Return the :class:`~mf_service.storage.BatchStorage`.

    Builds the GCS-backed storage from settings (bucket name, project
    auto-detected unless configured). Tests override it with
    ``app.dependency_overrides[get_batch_storage]`` to supply the in-memory
    implementation, so no live bucket is needed to exercise routes.
    """
    client = _build_storage_client(settings.firestore_project)
    return GCSBatchStorage(client, bucket=settings.storage_bucket)


BatchStorageDep = Annotated[BatchStorage, Depends(get_batch_storage)]


# The GNPS processor is built by the engine seam (core.engine); the alias lets
# the /process route write `processor: GNPSProcessorDep`. Tests override
# get_gnps_processor to supply a fake.
GNPSProcessorDep = Annotated["GNPSProcessor", Depends(get_gnps_processor)]


__all__ = [
    "get_user_id",
    "UserIdDep",
    "get_batch_repository",
    "BatchRepositoryDep",
    "get_batch_storage",
    "BatchStorageDep",
    "GNPSProcessorDep",
]
