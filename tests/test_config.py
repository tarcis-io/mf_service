"""Tests for Settings and the get_settings dependency."""

from __future__ import annotations

import pytest

from mf_service.core.config import Settings, get_settings
from mf_service.core.identity import distribution_identity


def test_identity_defaults_to_package_metadata() -> None:
    name, description, version = distribution_identity()
    settings = Settings()
    assert settings.name == name
    assert settings.description == description
    assert settings.version == version


def test_explicit_identity_is_preserved() -> None:
    settings = Settings(name="custom", description="custom desc")
    assert settings.name == "custom"
    assert settings.description == "custom desc"


def test_version_is_never_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even a stray MF_SERVICE_VERSION cannot change the reported version: it is
    # always the metadata value, so the service can't misreport its build.
    monkeypatch.setenv("MF_SERVICE_VERSION", "9.9.9")
    _name, _description, version = distribution_identity()
    assert Settings().version == version


def test_server_defaults() -> None:
    settings = Settings()
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000


def test_firestore_defaults() -> None:
    settings = Settings()
    assert settings.firestore_project is None  # auto-detect from ADC
    assert settings.firestore_database == "(default)"
    assert settings.batches_collection == "batches"


def test_firestore_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MF_SERVICE_FIRESTORE_PROJECT", "proj-x")
    monkeypatch.setenv("MF_SERVICE_FIRESTORE_DATABASE", "db-x")
    monkeypatch.setenv("MF_SERVICE_BATCHES_COLLECTION", "my_batches")
    settings = Settings()
    assert settings.firestore_project == "proj-x"
    assert settings.firestore_database == "db-x"
    assert settings.batches_collection == "my_batches"


def test_storage_defaults() -> None:
    settings = Settings()
    assert settings.storage_bucket == "batches"
    assert settings.max_upload_bytes == 10 * 1024 * 1024


def test_storage_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MF_SERVICE_STORAGE_BUCKET", "my-bucket")
    monkeypatch.setenv("MF_SERVICE_MAX_UPLOAD_BYTES", "1048576")
    settings = Settings()
    assert settings.storage_bucket == "my-bucket"
    assert settings.max_upload_bytes == 1048576


def test_server_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MF_SERVICE_HOST", "127.0.0.1")
    monkeypatch.setenv("MF_SERVICE_PORT", "9001")
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 9001


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
