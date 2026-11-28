"""The FastAPI application factory.

:func:`create_app` builds a fresh app: it reads configuration, sets the OpenAPI
metadata from it, registers exception handlers, and mounts the versioned router.
``app`` is the conventional module-level instance for ``uvicorn mf_service:app``.
"""

from __future__ import annotations

from fastapi import FastAPI

from mf_service.api.v1 import router as v1_router
from mf_service.api.v1.batches import (
    batch_not_found_handler,
    batch_not_processable_handler,
    duplicate_name_handler,
    object_not_found_handler,
)
from mf_service.core.config import Settings, get_settings
from mf_service.repository import (
    BatchNotFoundError,
    BatchNotProcessableError,
    DuplicateBatchNameError,
)
from mf_service.storage import ObjectNotFoundError


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured FastAPI application.

    Parameters
    ----------
    settings:
        Optional settings. Defaults to the cached process settings from
        :func:`get_settings`; pass an explicit instance (e.g. in tests) and it
        is also registered as a ``get_settings`` dependency override, so routes
        and the OpenAPI metadata share the one instance.
    """
    resolved = settings or get_settings()

    app = FastAPI(
        title=resolved.name,
        description=resolved.description,
        version=resolved.version,
    )

    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: resolved

    # Map the repository's and storage's domain errors to HTTP status codes in
    # one place, so the routes stay free of try/except. (CORS / request-id /
    # timing middleware would be registered here too, before the routers mount.)
    app.add_exception_handler(BatchNotFoundError, batch_not_found_handler)
    app.add_exception_handler(DuplicateBatchNameError, duplicate_name_handler)
    app.add_exception_handler(ObjectNotFoundError, object_not_found_handler)
    app.add_exception_handler(
        BatchNotProcessableError, batch_not_processable_handler
    )

    app.include_router(v1_router)

    return app


app = create_app()
