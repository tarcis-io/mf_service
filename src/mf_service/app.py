"""The FastAPI application factory.

:func:`create_app` builds and wires a fresh :class:`~fastapi.FastAPI` instance:
it reads configuration, sets the OpenAPI metadata from it, and mounts the
versioned API router. Keeping assembly in a factory (rather than a module-level
singleton only) means tests can build isolated apps and future entry points
can customise construction, while ``app`` remains the conventional instance for
``uvicorn mf_service:app``.
"""

from __future__ import annotations

from fastapi import FastAPI

from mf_service.api.v1 import router as v1_router
from mf_service.api.v1.batches import (
    batch_not_found_handler,
    duplicate_name_handler,
)
from mf_service.config import Settings, get_settings
from mf_service.repository import BatchNotFoundError, DuplicateBatchNameError


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured FastAPI application.

    Parameters
    ----------
    settings:
        Optional settings to use. Defaults to the cached process settings from
        :func:`get_settings`; pass an explicit instance to build a customised
        app (e.g. in tests). When passed, it is also registered as a dependency
        override for :func:`get_settings`, so routes that depend on it (such as
        ``/version``) see the same instance that configures the OpenAPI
        metadata — the factory argument and the route dependency never diverge.
    """
    resolved = settings or get_settings()

    app = FastAPI(
        title=resolved.name,
        description=resolved.description,
        version=resolved.version,
    )

    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: resolved

    # Map the repository's domain errors to HTTP status codes in one place, so
    # the routes stay free of try/except. (CORS / request-id / timing
    # middleware would be registered here too, before the routers are mounted.)
    app.add_exception_handler(BatchNotFoundError, batch_not_found_handler)
    app.add_exception_handler(DuplicateBatchNameError, duplicate_name_handler)

    app.include_router(v1_router)

    return app


# The conventional ASGI instance: `uvicorn mf_service:app`.
app = create_app()
