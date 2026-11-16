"""Object-storage seam — the :class:`StorageGateway` contract and backends.

Batch input files live in object storage (Google Cloud Storage in production)
under the key ``<batch_id>/<filename>``. The gateway uploads them, lists them,
and downloads them; callers depend on this interface, never on a concrete
backend, so the routes and the background task are unaware of GCS and tests can
swap in :class:`InMemoryStorageGateway`.

``google-cloud-storage`` is imported lazily (inside the provider that builds the
GCS gateway, not at module import) so importing the service stays cheap and only
pulls the client in when an upload actually runs — mirroring the Firestore
seam's lazy-import discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # type-checker only; no runtime GCS import
    from google.cloud.storage import Client


class ObjectNotFoundError(Exception):
    """Raised when an object key does not exist in storage."""


@dataclass
class StoredObject:
    """Metadata for one stored object (storage-agnostic).

    ``name`` is the object key relative to its prefix (the bare filename for a
    batch's files, not the full ``<batch_id>/<filename>`` key).
    """

    name: str
    size: int
    content_type: str
    updated: datetime | None


@runtime_checkable
class StorageGateway(Protocol):
    """Contract for a batch's files in object storage."""

    def upload(self, path: str, data: bytes, *, content_type: str) -> None:
        """Store ``data`` at ``path`` (an object key), overwriting any existing
        object. ``content_type`` is the object's MIME type.
        """

    def list(self, prefix: str) -> list[StoredObject]:
        """Return metadata for every object whose key starts with ``prefix``.

        ``prefix`` is expected to be ``<batch_id>/``; each returned
        :class:`StoredObject` carries the filename with the prefix stripped.
        """

    def download(self, path: str) -> bytes:
        """Return the bytes stored at ``path`` (an object key).

        Raises :class:`ObjectNotFoundError` if no object exists at ``path``.
        """


class GCSStorageGateway:
    """A :class:`StorageGateway` backed by a Google Cloud Storage bucket."""

    def __init__(self, client: Client, *, bucket: str) -> None:
        self._client = client
        self._bucket_name = bucket

    def upload(self, path: str, data: bytes, *, content_type: str) -> None:
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(path)
        blob.upload_from_string(data, content_type=content_type)

    def list(self, prefix: str) -> list[StoredObject]:
        # list_blobs returns every object under the prefix; strip the prefix
        # from each key so callers see bare filenames, not <batch_id>/<file>.
        blobs = self._client.list_blobs(self._bucket_name, prefix=prefix)
        return [
            StoredObject(
                name=blob.name[len(prefix):],
                size=blob.size or 0,
                content_type=blob.content_type or "application/octet-stream",
                updated=blob.updated,
            )
            for blob in blobs
        ]

    def download(self, path: str) -> bytes:
        from google.cloud.exceptions import NotFound

        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(path)
        try:
            data: bytes = blob.download_as_bytes()
        except NotFound as exc:
            raise ObjectNotFoundError(path) from exc
        return data


class InMemoryStorageGateway:
    """A dict-backed :class:`StorageGateway` for tests and local runs.

    Records each upload under its object key so tests can assert what was
    written — the paths and payloads — without a live bucket.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.updated: dict[str, datetime] = {}

    def upload(self, path: str, data: bytes, *, content_type: str) -> None:
        self.objects[path] = data
        self.content_types[path] = content_type
        self.updated[path] = datetime.now(timezone.utc)

    def list(self, prefix: str) -> list[StoredObject]:
        return [
            StoredObject(
                name=path[len(prefix):],
                size=len(data),
                content_type=self.content_types[path],
                updated=self.updated.get(path),
            )
            for path, data in self.objects.items()
            if path.startswith(prefix)
        ]

    def download(self, path: str) -> bytes:
        try:
            return self.objects[path]
        except KeyError as exc:
            raise ObjectNotFoundError(path) from exc


def build_gcs_client(project: str | None = None) -> Any:
    """Build a Google Cloud Storage client (lazy import).

    Imported here, not at module scope, so importing this module and starting
    the app never pull in ``google-cloud-storage`` until an upload runs. With no
    project the client auto-detects it from the ambient GCP credentials, as on
    Cloud Run.
    """
    from google.cloud import storage

    return storage.Client(project=project) if project else storage.Client()
