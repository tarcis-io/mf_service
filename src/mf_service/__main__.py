"""Console entry point: run the service with uvicorn.

Enables both ``python -m mf_service`` and the ``mf-service`` script declared in
``pyproject.toml``. Host and port come from
:class:`~mf_service.core.config.Settings` (overridable via ``MF_SERVICE_HOST`` /
``MF_SERVICE_PORT``).
"""

from __future__ import annotations

import uvicorn

from mf_service.core.config import get_settings


def main() -> None:
    """Start the ASGI server."""
    settings = get_settings()
    uvicorn.run("mf_service:app", host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    main()
