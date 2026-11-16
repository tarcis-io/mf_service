"""Tests for Settings and the get_settings cache."""

from __future__ import annotations

import pytest

from mf_service.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    # get_settings is lru_cached; clear it around each test so env changes made
    # with monkeypatch.setenv are observed and don't leak between tests.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_env_overrides_identity(monkeypatch):
    # An env var with the MF_SERVICE_ prefix wins over the metadata default.
    monkeypatch.setenv("MF_SERVICE_NAME", "custom-name")
    monkeypatch.setenv("MF_SERVICE_DESCRIPTION", "custom description")
    settings = get_settings()
    assert settings.name == "custom-name"
    assert settings.description == "custom description"


def test_env_overrides_server(monkeypatch):
    monkeypatch.setenv("MF_SERVICE_HOST", "127.0.0.1")
    monkeypatch.setenv("MF_SERVICE_PORT", "9001")
    settings = get_settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 9001  # coerced from str by pydantic


def test_version_is_not_env_overridable(monkeypatch):
    # version always reports the deployed build: a stray MF_SERVICE_VERSION is
    # ignored, unlike name/description which the env can override.
    monkeypatch.setenv("MF_SERVICE_VERSION", "9.9.9")
    settings = get_settings()
    assert settings.version != "9.9.9"
    # It equals the package metadata version (or the source-tree fallback).
    assert settings.version == Settings().version


def test_identity_backfilled_from_metadata():
    # With nothing set, name/description/version come from the package metadata
    # (or the source-tree fallback) — never left blank.
    settings = Settings()
    assert settings.name
    assert settings.version


def test_explicit_value_beats_metadata():
    # A value passed directly is preserved; only unset fields are backfilled.
    settings = Settings(name="explicit")
    assert settings.name == "explicit"
    assert settings.description  # still backfilled


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
