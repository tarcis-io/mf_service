"""Tests for the package surface and the application factory."""

from __future__ import annotations

from fastapi import FastAPI

import mf_service
from mf_service import app, create_app
from mf_service.core.identity import (
    DISTRIBUTION,
    VERSION_FALLBACK,
    distribution_identity,
)


def test_public_surface() -> None:
    assert set(mf_service.__all__) == {"app", "create_app", "__version__"}
    assert isinstance(app, FastAPI)
    assert callable(create_app)


def test_version_is_a_string() -> None:
    assert isinstance(mf_service.__version__, str)
    assert mf_service.__version__


def test_create_app_builds_distinct_apps() -> None:
    # The factory returns a fresh instance each call (isolation for tests and
    # alternate entry points), and the module-level `app` is one such instance.
    first = create_app()
    second = create_app()
    assert isinstance(first, FastAPI)
    assert first is not second
    assert isinstance(app, FastAPI)


def test_app_metadata_comes_from_identity() -> None:
    # OpenAPI metadata is sourced from the package identity (single source).
    name, description, version = distribution_identity()
    built = create_app()
    assert built.title == name
    assert built.version == version


def test_identity_falls_back_for_uninstalled_tree() -> None:
    # distribution_identity never raises; version is a non-empty string and the
    # fallback constants are coherent.
    name, _description, version = distribution_identity()
    assert name == DISTRIBUTION
    assert version
    assert VERSION_FALLBACK == "0.0.0"
