"""Application configuration.

Settings are centralised here so the rest of the service never reads the
environment directly. :class:`Settings` is a pydantic-settings model: most
fields can be overridden by an environment variable with the ``MF_SERVICE_``
prefix (e.g. ``MF_SERVICE_HOST``, ``MF_SERVICE_PORT``) or by a ``.env`` file,
which keeps configuration out of the code and makes the service portable
across environments (local, CI, staging, production).

The application name, description, and version are sourced from the installed
package metadata (``pyproject.toml`` is the single source of truth) rather than
duplicated here, so they cannot drift. When the package is not installed (a bare
source tree), they degrade the same way ``mf_service.__version__`` does.

``name`` and ``description`` may still be overridden via the environment (for
branding or multi-tenant deployments). ``version`` deliberately may **not**: it
reports which build is actually deployed, so it is always metadata-derived and
never read from the environment — a hand-set version would misreport reality
and defeat the single-source-of-truth guarantee.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, metadata, version

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DISTRIBUTION = "mf_service"
_VERSION_FALLBACK = "0.0.0"


def _distribution_identity() -> tuple[str, str, str]:
    """Return ``(name, description, version)`` from the package metadata.

    Falls back to ``(_DISTRIBUTION, "", "0.0.0")`` when the package is not
    installed (running from a bare source tree), mirroring how
    ``mf_service.__version__`` degrades. Reading the version here — rather than
    importing it from :mod:`mf_service` — keeps this module free of an import
    cycle with the package ``__init__``.
    """
    try:
        meta = metadata(_DISTRIBUTION)
    except PackageNotFoundError:  # pragma: no cover - source tree, not installed
        return _DISTRIBUTION, "", _VERSION_FALLBACK
    return meta["Name"], meta.get("Summary", ""), version(_DISTRIBUTION)


class Settings(BaseSettings):
    """Runtime configuration, overridable via environment or a ``.env`` file.

    ``name`` and ``description`` default to the installed package metadata; an
    explicitly provided value — including an environment variable
    (``MF_SERVICE_NAME`` / ``MF_SERVICE_DESCRIPTION``) — takes precedence.
    ``version`` is always metadata-derived and ignores any provided value (see
    the validator), so it can never misreport the deployed build.
    """

    model_config = SettingsConfigDict(
        env_prefix="MF_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Identity. name/description: empty defaults backfilled from package
    # metadata by the validator below; an env override (MF_SERVICE_NAME, etc.)
    # wins. version: never env-overridable — always set from metadata by the
    # validator, so it reports the build that is actually deployed.
    name: str = ""
    description: str = ""
    version: str = ""

    # HTTP server (consumed by the __main__ entry point / uvicorn).
    host: str = "0.0.0.0"
    port: int = 8000

    @model_validator(mode="after")
    def _fill_identity_from_metadata(self) -> Settings:
        """Resolve the identity fields from the package metadata.

        Runs inside pydantic's lifecycle (unlike overriding ``__init__``), so an
        explicitly set ``name`` / ``description`` — e.g. ``MF_SERVICE_NAME`` — is
        preserved and only the empty ones fall back to metadata. ``version`` is
        the exception: it is always overwritten with the metadata value (any
        provided value is discarded), so a stray ``MF_SERVICE_VERSION`` can
        never make the service misreport which build is running.
        """
        name, description, ver = _distribution_identity()
        self.name = self.name or name
        self.description = self.description or description
        self.version = ver
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton.

    Caching means configuration is read once per process. It is the FastAPI
    dependency used by routes, so tests override it either by clearing the cache
    (:func:`get_settings.cache_clear`) after setting environment variables, or
    via ``app.dependency_overrides[get_settings]`` — which
    :func:`~mf_service.app.create_app` wires up automatically when passed an
    explicit ``settings`` instance.
    """
    return Settings()
