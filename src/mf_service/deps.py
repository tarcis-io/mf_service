"""Shared FastAPI dependencies — the seam between the HTTP layer and the engine.

Routes that need an :mod:`mf_engine` processor obtain it through a provider
declared here (via ``Depends``) instead of constructing the engine inline. This
keeps construction in one place, lets tests swap a processor with
``app.dependency_overrides`` (as :func:`mf_service.config.get_settings` is
overridden today), and gives a single spot to decide a processor's lifetime.

The engine is imported lazily inside each provider, not at module import, so
importing this module (and starting the app) stays cheap and only pulls in the
engine when an engine-backed route is first exercised.

Engine-backed routes should stay synchronous (``def``, not ``async def``): the
engine does CPU-bound pandas work, which FastAPI offloads to a worker
threadpool. Reserve ``async def`` for routes that genuinely ``await`` I/O.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Header

from mf_service.config import Settings, get_settings
from mf_service.repository import BatchRepository
from mf_service.storage import StorageGateway

if TYPE_CHECKING:  # import for type checkers only; no engine import at runtime
    from mf_engine.processors.gnps import GNPSProcessor


@lru_cache
def get_gnps_processor() -> GNPSProcessor:
    """Return the process-wide :class:`~mf_engine.processors.gnps.GNPSProcessor`.

    Cached so the processor is built once per process (it is stateless and safe
    to reuse). Imported lazily so starting the app does not pull in the engine's
    pandas dependency until a GNPS route is first used. Tests can bypass it with
    ``app.dependency_overrides[get_gnps_processor] = ...``.
    """
    from mf_engine.processors.gnps import GNPSProcessor

    return GNPSProcessor()


# Annotated alias so routes can write `processor: GNPSProcessorDep` instead of
# repeating the Depends wiring. New engine providers follow the same shape.
GNPSProcessorDep = Annotated["GNPSProcessor", Depends(get_gnps_processor)]


def get_user_id(x_user_id: Annotated[str, Header()]) -> str:
    """Return the calling user's id from the required ``X-User-Id`` header.

    This service is called by another service on GCP, which identifies the end
    user via this header; FastAPI returns 422 automatically when it is absent.
    Scoping every batch operation by this value is what keeps one user's batches
    invisible to another.
    """
    return x_user_id


# Route alias: `user_id: UserIdDep`.
UserIdDep = Annotated[str, Depends(get_user_id)]


@lru_cache
def _get_firestore_client(project: str | None) -> Any:
    """Build (once per process) the Firestore client.

    Lazily imported (see :func:`get_gnps_processor`). ``project`` is passed
    rather than read from settings so the cache key reflects it; ``None`` lets
    the client auto-detect the project from GCP credentials.
    """
    from google.cloud import firestore

    return firestore.Client(project=project) if project else firestore.Client()


def get_batch_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BatchRepository:
    """Return the :class:`~mf_service.repository.BatchRepository`.

    Builds the Firestore-backed repository from settings. Tests override this
    with ``app.dependency_overrides[get_batch_repository]`` to supply the
    in-memory implementation, so no live Firestore is needed to exercise routes.
    """
    from mf_service.firestore_repository import FirestoreBatchRepository

    client = _get_firestore_client(settings.firestore_project)
    return FirestoreBatchRepository(
        client,
        batches_collection=settings.batches_collection,
        names_collection=settings.batch_names_collection,
    )


# Route alias: `repo: BatchRepositoryDep`.
BatchRepositoryDep = Annotated[BatchRepository, Depends(get_batch_repository)]


@lru_cache
def _get_storage_client(project: str | None) -> Any:
    """Build (once per process) the Cloud Storage client.

    Lazily constructed via :func:`~mf_service.storage.build_gcs_client` (see
    :func:`get_gnps_processor`); ``project`` is the cache key (``None`` lets the
    client auto-detect it from GCP credentials).
    """
    from mf_service.storage import build_gcs_client

    return build_gcs_client(project)


def get_storage_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StorageGateway:
    """Return the :class:`~mf_service.storage.StorageGateway`.

    Builds the GCS-backed gateway from settings. Tests override this with
    ``app.dependency_overrides[get_storage_gateway]`` to supply the in-memory
    implementation, so no live bucket is needed to exercise the upload path.
    """
    from mf_service.storage import GCSStorageGateway

    # Reuse the configured GCP project (shared with Firestore); blank/None lets
    # the client auto-detect it from credentials, as on Cloud Run. Bucket names
    # are globally unique, so this only scopes billing/permissions, not routing.
    client = _get_storage_client(settings.firestore_project)
    return GCSStorageGateway(client, bucket=settings.storage_bucket)


# Route alias: `storage: StorageGatewayDep`.
StorageGatewayDep = Annotated[StorageGateway, Depends(get_storage_gateway)]
