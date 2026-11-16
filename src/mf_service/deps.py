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
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

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
