"""Tests for the batch storage backends.

``InMemoryBatchStorage`` is exercised directly. ``GCSBatchStorage`` is driven
against a tiny in-memory fake of the google-cloud-storage client surface it uses
(``bucket``/``blob``/``upload_from_string``/``download_as_bytes`` and
``list_blobs`` + ``blob.delete``), plus a fake ``google.cloud.exceptions.NotFound``
installed for the download path — so the full contract runs without the real
library or a live bucket.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from mf_service.storage import (
    BatchStorage,
    GCSBatchStorage,
    InMemoryBatchStorage,
    ObjectNotFoundError,
    StoredFile,
)

# --- in-memory ---------------------------------------------------------------


def test_in_memory_satisfies_the_protocol() -> None:
    assert isinstance(InMemoryBatchStorage(), BatchStorage)


def test_in_memory_upload_then_read_back() -> None:
    store = InMemoryBatchStorage()
    store.upload("b1", "a.csv", b"hello", content_type="text/csv")
    assert store.files_for("b1") == {"a.csv": b"hello"}


def test_in_memory_upload_overwrites() -> None:
    store = InMemoryBatchStorage()
    store.upload("b1", "a.csv", b"one")
    store.upload("b1", "a.csv", b"two")
    assert store.files_for("b1") == {"a.csv": b"two"}


def test_in_memory_delete_batch_removes_only_that_batch() -> None:
    store = InMemoryBatchStorage()
    store.upload("b1", "a.csv", b"x")
    store.upload("b1", "b.csv", b"y")
    store.upload("b2", "a.csv", b"z")
    store.delete_batch("b1")
    assert store.files_for("b1") == {}
    assert store.files_for("b2") == {"a.csv": b"z"}  # untouched


def test_in_memory_delete_unknown_batch_is_noop() -> None:
    store = InMemoryBatchStorage()
    store.delete_batch("nope")  # must not raise


def test_in_memory_list_files_returns_metadata() -> None:
    store = InMemoryBatchStorage()
    store.upload("b1", "a.csv", b"hello", content_type="text/csv")
    store.upload("b2", "other.csv", b"x")  # another batch, must not appear
    files = store.list_files("b1")
    assert [f.filename for f in files] == ["a.csv"]
    f = files[0]
    assert isinstance(f, StoredFile)
    assert f.size == 5
    assert f.content_type == "text/csv"
    assert f.updated is not None  # upload stamps a write time


def test_in_memory_list_files_defaults_content_type() -> None:
    # An upload with no content_type falls back to octet-stream.
    store = InMemoryBatchStorage()
    store.upload("b1", "a.bin", b"x")
    assert store.list_files("b1")[0].content_type == "application/octet-stream"


def test_in_memory_list_files_empty_for_unknown_batch() -> None:
    assert InMemoryBatchStorage().list_files("nope") == []


def test_in_memory_download_returns_bytes() -> None:
    store = InMemoryBatchStorage()
    store.upload("b1", "a.csv", b"hello")
    assert store.download("b1", "a.csv") == b"hello"


def test_in_memory_download_missing_raises() -> None:
    store = InMemoryBatchStorage()
    with pytest.raises(ObjectNotFoundError):
        store.download("b1", "ghost.csv")


# --- a tiny fake of the GCS surface the repo uses ----------------------------


class _FakeNotFound(Exception):
    """Stand-in for ``google.cloud.exceptions.NotFound``."""


class _Blob:
    def __init__(self, client: _FakeStorageClient, path: str) -> None:
        self._client = client
        self.name = path

    # Metadata the list path reads (populated on upload).
    @property
    def size(self) -> int | None:
        data = self._client._store.get(self.name)
        return None if data is None else len(data)

    @property
    def content_type(self) -> str | None:
        return self._client._ctype.get(self.name)

    @property
    def updated(self) -> Any:
        return self._client._updated.get(self.name)

    def upload_from_string(
        self, data: bytes, content_type: str | None = None
    ) -> None:
        self._client._store[self.name] = data
        if content_type is not None:
            self._client._ctype[self.name] = content_type
        self._client._updated[self.name] = "2026-01-15T09:30:05Z"

    def download_as_bytes(self) -> bytes:
        try:
            return self._client._store[self.name]
        except KeyError as exc:
            raise _FakeNotFound(self.name) from exc

    def delete(self) -> None:
        self._client._store.pop(self.name, None)
        self._client._ctype.pop(self.name, None)
        self._client._updated.pop(self.name, None)


class _Bucket:
    def __init__(self, client: _FakeStorageClient, name: str) -> None:
        self._client = client
        self.name = name

    def blob(self, path: str) -> _Blob:
        return _Blob(self._client, path)


class _FakeStorageClient:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._ctype: dict[str, str] = {}
        self._updated: dict[str, Any] = {}

    def bucket(self, name: str) -> _Bucket:
        return _Bucket(self, name)

    def list_blobs(self, bucket: str, prefix: str = "") -> list[_Blob]:
        return [
            _Blob(self, path)
            for path in list(self._store)
            if path.startswith(prefix)
        ]


@pytest.fixture
def fake_gcs_exceptions() -> Iterator[None]:
    """Install a fake ``google.cloud.exceptions`` exposing ``NotFound``.

    The GCS download path imports ``NotFound`` lazily; the real library is not
    installed in tests, so stand one in (restoring any prior modules after).
    """
    module = types.ModuleType("google.cloud.exceptions")
    module.NotFound = _FakeNotFound  # type: ignore[attr-defined]
    google = sys.modules.get("google") or types.ModuleType("google")
    cloud = sys.modules.get("google.cloud") or types.ModuleType("google.cloud")
    saved = {
        name: sys.modules.get(name)
        for name in ("google", "google.cloud", "google.cloud.exceptions")
    }
    sys.modules["google"] = google
    sys.modules["google.cloud"] = cloud
    sys.modules["google.cloud.exceptions"] = module
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


@pytest.fixture
def gcs() -> GCSBatchStorage:
    return GCSBatchStorage(_FakeStorageClient(), bucket="batches")


# --- GCS-backed --------------------------------------------------------------


def test_gcs_satisfies_the_protocol(gcs: GCSBatchStorage) -> None:
    assert isinstance(gcs, BatchStorage)


def test_gcs_upload_writes_under_batch_folder(gcs: GCSBatchStorage) -> None:
    gcs.upload("b1", "a.csv", b"hello", content_type="text/csv")
    # Stored at <batch id>/<filename>.
    assert gcs._client._store == {"b1/a.csv": b"hello"}  # type: ignore[attr-defined]


def test_gcs_delete_batch_removes_the_whole_folder(
    gcs: GCSBatchStorage,
) -> None:
    gcs.upload("b1", "a.csv", b"x")
    gcs.upload("b1", "b.csv", b"y")
    gcs.upload("b2", "a.csv", b"z")
    gcs.delete_batch("b1")
    store = gcs._client._store  # type: ignore[attr-defined]
    assert store == {"b2/a.csv": b"z"}  # only b1/* removed


def test_gcs_delete_unknown_batch_is_noop(gcs: GCSBatchStorage) -> None:
    gcs.delete_batch("nope")  # nothing to delete, must not raise
    assert gcs._client._store == {}  # type: ignore[attr-defined]


def test_gcs_list_files_strips_prefix_and_carries_metadata(
    gcs: GCSBatchStorage,
) -> None:
    gcs.upload("b1", "a.csv", b"hello", content_type="text/csv")
    gcs.upload("b2", "other.csv", b"x")  # another batch, must not appear
    files = gcs.list_files("b1")
    assert [f.filename for f in files] == ["a.csv"]  # <batch id>/ stripped
    assert files[0].size == 5
    assert files[0].content_type == "text/csv"
    assert files[0].updated == "2026-01-15T09:30:05Z"


def test_gcs_list_files_empty_for_unknown_batch(gcs: GCSBatchStorage) -> None:
    assert gcs.list_files("nope") == []


def test_gcs_download_returns_bytes(gcs: GCSBatchStorage) -> None:
    gcs.upload("b1", "a.csv", b"hello")
    assert gcs.download("b1", "a.csv") == b"hello"


def test_gcs_download_missing_raises_object_not_found(
    gcs: GCSBatchStorage, fake_gcs_exceptions: None
) -> None:
    # The GCS NotFound is translated into the storage-agnostic error the route
    # maps to 404; needs the fake google.cloud.exceptions installed.
    with pytest.raises(ObjectNotFoundError):
        gcs.download("b1", "ghost.csv")
