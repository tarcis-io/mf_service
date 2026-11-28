"""Application configuration.

Settings are centralised here so the rest of the service never reads the
environment directly. :class:`Settings` is a pydantic-settings model: fields are
overridable via an ``MF_SERVICE_``-prefixed environment variable or a ``.env``
file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mf_service.core.identity import distribution_identity


class Settings(BaseSettings):
    """Runtime configuration, overridable via environment or a ``.env`` file.

    Identity fields default to the package metadata; see the module docstring
    for the override rules and :meth:`_fill_identity_from_metadata` for how they
    are applied.
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

    # Firestore (batch storage). project is optional: left blank, the client
    # auto-detects it from Application Default Credentials (the Cloud Run norm).
    # database defaults to Firestore's built-in unnamed "(default)" database; set
    # a name to target a named one. batches_collection is the collection batches
    # are stored in.
    firestore_project: str | None = None
    firestore_database: str = "(default)"
    batches_collection: str = "batches"

    # Cloud Storage (batch file uploads). project auto-detects from Application
    # Default Credentials when blank (the Firestore project is reused). The
    # bucket holds one folder per batch (<bucket>/<batch id>/<file>).
    storage_bucket: str = "batches"
    # Max size accepted per uploaded file; a larger file is rejected with 413.
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MiB

    # Processing. A batch stuck in an in-progress status (creating/processing)
    # longer than this is considered timed out by POST /process/timeouts, which
    # moves it to error. Overridable per-request on that endpoint. Constrained
    # to >= 1 so a misconfigured env can't time out batches that just started.
    processing_timeout_seconds: int = Field(default=3600, ge=1)  # 1 hour

    @model_validator(mode="after")
    def _fill_identity_from_metadata(self) -> Settings:
        """Backfill empty identity fields from the package metadata.

        An explicitly set ``name`` / ``description`` is preserved; only empty
        ones fall back to metadata. ``version`` is always overwritten with the
        metadata value, so a stray ``MF_SERVICE_VERSION`` can never make the
        service misreport which build is running.
        """
        name, description, ver = distribution_identity()
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


__all__ = ["Settings", "get_settings"]
