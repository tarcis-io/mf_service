"""Tests for the StorageGateway seam (in-memory + GCS-backed)."""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from mf_service.storage import (
    GCSStorageGateway,
    InMemoryStorageGateway,
    ObjectNotFoundError,
    StorageGateway,
)


def test_in_memory_satisfies_protocol():
    assert isinstance(InMemoryStorageGateway(), StorageGateway)


def test_in_memory_records_uploads():
    gw = InMemoryStorageGateway()
    gw.upload("b1/file.csv", b"payload", content_type="text/csv")
    assert gw.objects["b1/file.csv"] == b"payload"
    assert gw.content_types["b1/file.csv"] == "text/csv"


def test_in_memory_overwrites():
    gw = InMemoryStorageGateway()
    gw.upload("k", b"old", content_type="text/csv")
    gw.upload("k", b"new", content_type="text/csv")
    assert gw.objects["k"] == b"new"


def test_in_memory_list_strips_prefix_and_reports_metadata():
    gw = InMemoryStorageGateway()
    gw.upload("b1/a.csv", b"12345", content_type="text/csv")
    gw.upload("b1/b.tsv", b"x", content_type="text/tab-separated-values")
    gw.upload("b2/other.csv", b"zz", content_type="text/csv")  # different batch

    listed = {o.name: o for o in gw.list("b1/")}
    assert set(listed) == {"a.csv", "b.tsv"}  # scoped to the prefix, names bare
    assert listed["a.csv"].size == 5
    assert listed["b.tsv"].content_type == "text/tab-separated-values"
    assert listed["a.csv"].updated is not None


def test_in_memory_download_returns_bytes():
    gw = InMemoryStorageGateway()
    gw.upload("b1/a.csv", b"payload", content_type="text/csv")
    assert gw.download("b1/a.csv") == b"payload"


def test_in_memory_download_missing_raises():
    gw = InMemoryStorageGateway()
    with pytest.raises(ObjectNotFoundError):
        gw.download("b1/missing.csv")


# --------------------------------------------------------------------------
# GCS gateway against a tiny fake client surface (bucket -> blob -> upload).
# --------------------------------------------------------------------------


class _FakeNotFound(Exception):
    """Stand-in for ``google.cloud.exceptions.NotFound``."""


@pytest.fixture
def _fake_gcs_exceptions(monkeypatch):
    """Install a fake ``google.cloud.exceptions`` exposing ``NotFound``.

    ``GCSStorageGateway.download`` does ``from google.cloud.exceptions import
    NotFound`` to map a missing object; stub that so no live package is needed.
    """
    fake = types.ModuleType("google.cloud.exceptions")
    fake.NotFound = _FakeNotFound
    monkeypatch.setitem(sys.modules, "google.cloud.exceptions", fake)
    return fake


class _FakeBlob:
    def __init__(self, store: dict, path: str) -> None:
        self._store = store
        self._path = path

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self._store[self._path] = (data, content_type)

    def download_as_bytes(self) -> bytes:
        if self._path not in self._store:
            raise _FakeNotFound(self._path)
        return self._store[self._path][0]


class _FakeListedBlob:
    """What ``list_blobs`` yields — name + metadata, no download."""

    def __init__(self, name: str, data: bytes, content_type: str) -> None:
        self.name = name
        self.size = len(data)
        self.content_type = content_type
        self.updated = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeBucket:
    def __init__(self, store: dict) -> None:
        self._store = store

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self._store, path)


class _FakeStorageClient:
    def __init__(self) -> None:
        self.store: dict[str, tuple[bytes, str]] = {}
        self.requested_bucket: str | None = None

    def bucket(self, name: str) -> _FakeBucket:
        self.requested_bucket = name
        return _FakeBucket(self.store)

    def list_blobs(self, bucket_name: str, prefix: str = ""):
        self.requested_bucket = bucket_name
        return [
            _FakeListedBlob(path, data, content_type)
            for path, (data, content_type) in self.store.items()
            if path.startswith(prefix)
        ]


def test_gcs_gateway_uploads_to_bucket_and_path():
    client = _FakeStorageClient()
    gw = GCSStorageGateway(client, bucket="my-bucket")
    gw.upload("b1/file.tsv", b"rows", content_type="text/tab-separated-values")

    assert client.requested_bucket == "my-bucket"
    assert client.store["b1/file.tsv"] == (b"rows", "text/tab-separated-values")


def test_gcs_gateway_lists_prefix_with_stripped_names():
    client = _FakeStorageClient()
    gw = GCSStorageGateway(client, bucket="my-bucket")
    gw.upload("b1/a.csv", b"12345", content_type="text/csv")
    gw.upload("b2/other.csv", b"zz", content_type="text/csv")  # other batch

    listed = gw.list("b1/")
    assert [o.name for o in listed] == ["a.csv"]  # scoped + prefix stripped
    assert listed[0].size == 5
    assert client.requested_bucket == "my-bucket"


def test_gcs_gateway_downloads_bytes():
    client = _FakeStorageClient()
    gw = GCSStorageGateway(client, bucket="my-bucket")
    gw.upload("b1/a.csv", b"payload", content_type="text/csv")
    assert gw.download("b1/a.csv") == b"payload"


def test_gcs_gateway_download_missing_raises(_fake_gcs_exceptions):
    # A GCS NotFound is mapped to the gateway's own ObjectNotFoundError.
    client = _FakeStorageClient()
    gw = GCSStorageGateway(client, bucket="my-bucket")
    with pytest.raises(ObjectNotFoundError):
        gw.download("b1/missing.csv")
