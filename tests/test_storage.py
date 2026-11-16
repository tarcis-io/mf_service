"""Tests for the StorageGateway seam (in-memory + GCS-backed)."""

from __future__ import annotations

from mf_service.storage import (
    GCSStorageGateway,
    InMemoryStorageGateway,
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


# --------------------------------------------------------------------------
# GCS gateway against a tiny fake client surface (bucket -> blob -> upload).
# --------------------------------------------------------------------------


class _FakeBlob:
    def __init__(self, store: dict, path: str) -> None:
        self._store = store
        self._path = path

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self._store[self._path] = (data, content_type)


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


def test_gcs_gateway_uploads_to_bucket_and_path():
    client = _FakeStorageClient()
    gw = GCSStorageGateway(client, bucket="my-bucket")
    gw.upload("b1/file.tsv", b"rows", content_type="text/tab-separated-values")

    assert client.requested_bucket == "my-bucket"
    assert client.store["b1/file.tsv"] == (b"rows", "text/tab-separated-values")
