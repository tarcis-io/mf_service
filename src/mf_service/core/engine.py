"""The mf_engine seam — the one place the service reaches into the engine.

Exposes the engine version and the GNPS processor the processing pipeline runs.
``mf_engine`` is imported inside the functions (never at module load), so
importing the app does not pull in the engine or its heavier dependencies until
a feature actually needs them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-checker only; no runtime engine import
    from mf_engine.processors.gnps import GNPSProcessor


def engine_version() -> str:
    """Return ``mf_engine.__version__``, the engine's reported version."""
    # Imported inside the function so importing the app does not pull in the
    # engine until its version is actually requested.
    import mf_engine

    return mf_engine.__version__


@lru_cache
def get_gnps_processor() -> GNPSProcessor:  # pragma: no cover - builds real engine
    """Return the process-wide ``GNPSProcessor`` (built once, lazily).

    Cached because the processor is stateless and safe to reuse, and imported
    lazily so starting the app never pulls in the engine's processing
    dependencies until the first ``/process`` run. Tests bypass it with
    ``app.dependency_overrides[get_gnps_processor]``, so its body is not
    exercised in-suite (mirroring the GCP client builders in ``deps``).
    """
    from mf_engine.processors.gnps import GNPSProcessor

    return GNPSProcessor()


__all__ = ["engine_version", "get_gnps_processor"]
