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

from mf_service.app import app, create_app
from mf_service.core import __version__

__all__ = ["app", "create_app", "__version__"]
