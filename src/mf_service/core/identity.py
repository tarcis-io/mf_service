"""The service's own identity — name, description, and version.

The single place that reads the installed package metadata (``pyproject.toml``
is the source of truth). Both the package ``__version__`` and
:mod:`mf_service.core.config` consume :func:`distribution_identity` from here.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, metadata, version

#: The installed distribution name (matches ``[project].name`` in pyproject).
DISTRIBUTION = "mf_service"

#: Fallback version for an uninstalled source tree (mirrors mf_engine's
#: behaviour). Seen only when the package has not been installed.
VERSION_FALLBACK = "0.0.0"


def distribution_identity() -> tuple[str, str, str]:
    """Return ``(name, description, version)`` from the package metadata.

    Falls back to ``(DISTRIBUTION, "", VERSION_FALLBACK)`` for an uninstalled
    source tree, so importing the package never fails just because it has not
    been ``pip install``-ed.
    """
    try:
        meta = metadata(DISTRIBUTION)
    except PackageNotFoundError:  # pragma: no cover - source tree, not installed
        return DISTRIBUTION, "", VERSION_FALLBACK
    # PackageMetadata supports __getitem__/__contains__ but not .get(); a missing
    # key yields None, so coerce the optional Summary to a plain string.
    summary = meta["Summary"] if "Summary" in meta else ""
    return meta["Name"], summary or "", version(DISTRIBUTION)


#: The service version — the single source of truth, re-exported as
#: ``mf_service.__version__``. Read once at import from the package metadata.
__version__ = distribution_identity()[2]

__all__ = ["DISTRIBUTION", "VERSION_FALLBACK", "distribution_identity", "__version__"]
