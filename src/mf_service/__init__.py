"""mf_service — the MassFlow service.

A thin FastAPI layer that exposes the MassFlow engine (:mod:`mf_engine`) over
HTTP. The public surface is intentionally small:

    from mf_service import app          # the ASGI application
    from mf_service import create_app   # the application factory

The application is assembled by :func:`create_app` (see
:mod:`mf_service.app`); ``app`` is a ready-to-serve instance for ``uvicorn
mf_service:app``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from mf_service.app import app, create_app

__all__ = ["app", "create_app", "__version__"]

# Single source of truth: the version declared in pyproject.toml, read from the
# installed package metadata. Falls back to "0.0.0" when running from a source
# tree that has not been installed.
try:
    __version__ = version("mf_service")
except PackageNotFoundError:  # pragma: no cover - source tree, not installed
    __version__ = "0.0.0"
