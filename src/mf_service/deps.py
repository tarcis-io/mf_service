"""Shared FastAPI dependencies — the seam between the HTTP layer and the engine.

Routes that need an :mod:`mf_engine` processor should obtain it through a
provider declared here (via ``Depends``), rather than importing and constructing
the engine inline. Centralising construction keeps the wiring in one place,
lets tests swap a processor with ``app.dependency_overrides`` (exactly as
:func:`mf_service.config.get_settings` is overridden today), and gives a single
spot to decide a processor's lifetime — process-wide singleton vs. per-request.

:mod:`mf_engine` is imported lazily inside the provider, not at module import,
so importing this module (and starting the app) stays cheap and only pulls the
engine in when an engine-backed route is actually exercised — mirroring the
engine's own lazy-import discipline for its pandas-backed processors.

Example — a future GNPS route
-----------------------------
    from typing import Annotated

    from fastapi import APIRouter, Depends

    from mf_service.deps import GNPSProcessorDep

    router = APIRouter(tags=["gnps"])

    @router.post("/gnps")
    def run_gnps(processor: GNPSProcessorDep, body: GNPSRequest) -> GNPSResponse:
        # Stays a plain `def`: the engine does CPU-bound pandas work, which
        # FastAPI runs in its threadpool (see the convention note below). Making
        # this `async def` would block the event loop.
        result = processor.process(body.to_engine_input())
        return GNPSResponse.from_engine_result(result)

Route style convention
-----------------------
Engine-backed routes stay **synchronous** (``def``, not ``async def``). The
engine's work is CPU-bound (pandas merges), and FastAPI offloads sync routes to
a worker threadpool, keeping the event loop free. Use ``async def`` only for a
route that genuinely ``await``\\s I/O (an async client, DB driver, etc.); never
wrap blocking/CPU-bound work in ``async def``, where it would stall every other
request.
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

    Cached so the processor is built once per process (it is stateless and
    thread-safe to reuse). Imported lazily so neither importing this module nor
    starting the app pulls in the engine's pandas dependency until a GNPS route
    is first used. Tests can bypass it with
    ``app.dependency_overrides[get_gnps_processor] = ...``.
    """
    from mf_engine.processors.gnps import GNPSProcessor

    return GNPSProcessor()


# Annotated alias so routes can write `processor: GNPSProcessorDep` instead of
# repeating the Depends wiring. New engine providers follow the same shape.
GNPSProcessorDep = Annotated["GNPSProcessor", Depends(get_gnps_processor)]
