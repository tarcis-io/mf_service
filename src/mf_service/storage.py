"""Object-storage seam — the :class:`StorageGateway` contract and backends.

Batch input files are uploaded to object storage (Google Cloud Storage in
production) under the key ``<batch_id>/<filename>``. The upload path depends on
this interface, never on a concrete backend, so the background task is unaware
of GCS and tests can swap in :class:`InMemoryStorageGateway`.

``google-cloud-storage`` is imported lazily (inside the provider that builds the
GCS gateway, not at module import) so importing the service stays cheap and only
pulls the client in when an upload actually runs — mirroring the Firestore
seam's lazy-import discipline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # type-checker only; no runtime GCS import
    from google.cloud.storage import Client


@runtime_checkable
class StorageGateway(Protocol):
    """Contract for uploading a batch's files to object storage."""

    def upload(self, path: str, data: bytes, *, content_type: str) -> None:
        """Store ``data`` at ``path`` (an object key), overwriting any existing
        object. ``content_type`` is the object's MIME type.
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


class InMemoryStorageGateway:
    """A dict-backed :class:`StorageGateway` for tests and local runs.

    Records each upload under its object key so tests can assert what was
    written — the paths and payloads — without a live bucket.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    def upload(self, path: str, data: bytes, *, content_type: str) -> None:
        self.objects[path] = data
        self.content_types[path] = content_type


def build_gcs_client(project: str | None = None) -> Any:
    """Build a Google Cloud Storage client (lazy import).

    Imported here, not at module scope, so importing this module and starting
    the app never pull in ``google-cloud-storage`` until an upload runs. With no
    project the client auto-detects it from the ambient GCP credentials, as on
    Cloud Run.
    """
    from google.cloud import storage

    return storage.Client(project=project) if project else storage.Client()
