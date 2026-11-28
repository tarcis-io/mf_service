"""Cross-cutting infrastructure: configuration, dependencies, external seams.

Holds the plumbing endpoints lean on but that are not themselves resources
(config, the engine-version seam, FastAPI dependencies). ``__version__`` is
re-exported from :mod:`mf_service.core.identity` for convenient access.
"""

from __future__ import annotations

from mf_service.core.identity import __version__

__all__ = ["__version__"]
