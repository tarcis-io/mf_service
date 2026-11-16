"""Tests for the engine- and storage-integration dependency seams."""

from __future__ import annotations

import sys
import types

import pytest
from mf_engine.processors.gnps import GNPSProcessor

from mf_service.config import Settings
from mf_service.deps import (
    _get_firestore_client,
    _get_storage_client,
    get_batch_repository,
    get_gnps_processor,
    get_storage_gateway,
    get_user_id,
)
from mf_service.firestore_repository import FirestoreBatchRepository
from mf_service.storage import GCSStorageGateway


def test_provider_returns_gnps_processor():
    assert isinstance(get_gnps_processor(), GNPSProcessor)


def test_provider_is_cached():
    # One processor per process: the provider is lru_cached so routes share a
    # single stateless instance rather than rebuilding it per request.
    assert get_gnps_processor() is get_gnps_processor()


def test_get_user_id_returns_header_value():
    # The dependency simply surfaces the validated header value.
    assert get_user_id("user-42") == "user-42"


@pytest.fixture
def _fake_firestore(monkeypatch):
    """Install a fake ``google.cloud.firestore`` so no live client is built.

    The repository provider imports ``from google.cloud import firestore`` and
    calls ``firestore.Client(...)``. We stub that module and record the project
    passed, then clear the lru_cache so each test sees the fake.
    """
    calls: list[object] = []

    class _Client:
        def __init__(self, project=None):
            calls.append(project)
            self.project = project

    fake = types.ModuleType("google.cloud.firestore")
    fake.Client = _Client
    # Patch only the submodule: `from google.cloud import firestore` finds an
    # already-imported submodule in sys.modules, so we don't disturb the real
    # google.cloud namespace package.
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake)
    _get_firestore_client.cache_clear()
    yield calls
    _get_firestore_client.cache_clear()


def test_get_batch_repository_builds_firestore_repo(_fake_firestore):
    settings = Settings(
        firestore_project="proj-x",
        batches_collection="b",
        batch_names_collection="n",
    )
    repo = get_batch_repository(settings)
    assert isinstance(repo, FirestoreBatchRepository)
    assert _fake_firestore == ["proj-x"]  # project forwarded to the client


def test_get_firestore_client_without_project(_fake_firestore):
    # When no project is configured the client auto-detects (called with no
    # project argument) — the None branch of the builder.
    _get_firestore_client(None)
    assert _fake_firestore == [None]


def test_get_firestore_client_is_cached(_fake_firestore):
    first = _get_firestore_client("proj-x")
    second = _get_firestore_client("proj-x")
    assert first is second  # lru_cached per project
    assert _fake_firestore == ["proj-x"]  # built only once


@pytest.fixture
def _fake_storage(monkeypatch):
    """Install a fake ``google.cloud.storage`` so no live client is built.

    ``build_gcs_client`` does ``from google.cloud import storage`` and calls
    ``storage.Client(...)``; stub that and record the project, clearing the
    lru_cache so each test sees the fake.
    """
    calls: list[object] = []

    class _Client:
        def __init__(self, project=None):
            calls.append(project)
            self.project = project

    fake = types.ModuleType("google.cloud.storage")
    fake.Client = _Client
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake)
    _get_storage_client.cache_clear()
    yield calls
    _get_storage_client.cache_clear()


def test_get_storage_gateway_builds_gcs_gateway(_fake_storage):
    settings = Settings(firestore_project="proj-x", storage_bucket="bkt")
    gateway = get_storage_gateway(settings)
    assert isinstance(gateway, GCSStorageGateway)
    assert _fake_storage == ["proj-x"]  # project forwarded to the client


def test_get_storage_client_without_project(_fake_storage):
    # No project configured -> client auto-detects (None branch of the builder).
    _get_storage_client(None)
    assert _fake_storage == [None]


def test_get_storage_client_is_cached(_fake_storage):
    first = _get_storage_client("proj-x")
    second = _get_storage_client("proj-x")
    assert first is second  # lru_cached per project
    assert _fake_storage == ["proj-x"]  # built only once
