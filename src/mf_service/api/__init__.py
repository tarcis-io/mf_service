"""The HTTP API package.

Routes are grouped by API version so the service can evolve without breaking
existing clients. The current version lives in :mod:`mf_service.api.v1`; future
versions get their own subpackage and are mounted alongside it in the app
factory.
"""

from __future__ import annotations
