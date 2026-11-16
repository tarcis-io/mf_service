"""Application configuration.

Settings are centralised here so the rest of the service never reads the
environment directly. :class:`Settings` is a pydantic-settings model: fields can
be overridden by an environment variable with the ``MF_SERVICE_`` prefix (e.g.
``MF_SERVICE_HOST``) or by a ``.env`` file.

``name``, ``description``, and ``version`` default to the installed package
metadata (``pyproject.toml`` is the single source of truth). ``name`` and
``description`` may be overridden via the environment; ``version`` may not — it
always reports the deployed build and ignores any provided value (see the
validator).
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
    # PackageMetadata supports __getitem__/__contains__ but not .get(); a missing
    # key yields None, so coerce the optional Summary to a plain string.
    summary = meta["Summary"] if "Summary" in meta else ""
    return meta["Name"], summary or "", version(_DISTRIBUTION)


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

    # Identity: empty defaults are backfilled from package metadata by the
    # validator below. See the class/module docstrings for the override rules.
    name: str = ""
    description: str = ""
    version: str = ""

    # HTTP server (consumed by the __main__ entry point / uvicorn).
    host: str = "0.0.0.0"
    port: int = 8000

    # Firestore. project is optional: the client auto-detects it from the
    # ambient GCP credentials/metadata when left blank (the normal case on
    # Cloud Run). The two collection names are overridable for multi-env setups.
    firestore_project: str | None = None
    batches_collection: str = "batches"
    batch_names_collection: str = "batch_names"

    @model_validator(mode="after")
    def _fill_identity_from_metadata(self) -> Settings:
        """Backfill empty identity fields from the package metadata.

        An explicitly set ``name`` / ``description`` is preserved; only empty
        ones fall back to metadata. ``version`` is always overwritten with the
        metadata value, so a stray ``MF_SERVICE_VERSION`` can never make the
        service misreport which build is running.
        """
        name, description, ver = _distribution_identity()
        self.name = self.name or name
        self.description = self.description or description
        self.version = ver
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton (read once per process).

    It is the FastAPI dependency used by routes. Tests override it by clearing
    the cache (:func:`get_settings.cache_clear`) after setting environment
    variables, or via ``app.dependency_overrides[get_settings]`` — which
    :func:`~mf_service.app.create_app` wires up when passed an explicit
    ``settings`` instance.
    """
    return Settings()
