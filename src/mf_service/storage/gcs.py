"""Google Cloud Storage-backed :class:`~mf_service.storage.BatchStorage`.

One folder per batch: objects are written at ``<batch id>/<filename>`` within
the configured bucket. ``google-cloud-storage`` is only used through the passed
client, so importing this module never requires the library (the client is
built in :mod:`mf_service.core.deps`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mf_service.storage.batch import ObjectNotFoundError, StoredFile

if TYPE_CHECKING:  # type-checker only; no runtime Storage import
    from google.cloud.storage import Client


def _object_path(batch_id: str, filename: str) -> str:
    """Return the object path for a batch file: ``<batch id>/<filename>``."""
    return f"{batch_id}/{filename}"


def _batch_prefix(batch_id: str) -> str:
    """Return the folder prefix for a batch: ``<batch id>/``."""
    return f"{batch_id}/"


class GCSBatchStorage:
    """A :class:`~mf_service.storage.BatchStorage` backed by a GCS bucket."""

    def __init__(self, client: Client, *, bucket: str) -> None:
        self._client = client
        self._bucket_name = bucket

    def upload(
        self,
        batch_id: str,
        filename: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(_object_path(batch_id, filename))
        blob.upload_from_string(data, content_type=content_type)

    def list_files(self, batch_id: str) -> list[StoredFile]:
        # List the batch's folder and strip the prefix so callers see bare
        # filenames, not <batch id>/<filename>.
        prefix = _batch_prefix(batch_id)
        blobs = self._client.list_blobs(self._bucket_name, prefix=prefix)
        return [
            StoredFile(
                filename=blob.name[len(prefix):],
                size=blob.size or 0,
                content_type=blob.content_type or "application/octet-stream",
                updated=blob.updated,
            )
            for blob in blobs
        ]

    def download(self, batch_id: str, filename: str) -> bytes:
        from google.cloud.exceptions import NotFound

        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(_object_path(batch_id, filename))
        try:
            data: bytes = blob.download_as_bytes()
        except NotFound as exc:
            raise ObjectNotFoundError(
                _object_path(batch_id, filename)
            ) from exc
        return data

    def delete_batch(self, batch_id: str) -> None:
        # List the batch's folder and delete each object. Listing by prefix is
        # how GCS models a "folder"; an empty result (nothing uploaded) deletes
        # nothing, so this is safe for a batch that never finished uploading.
        bucket = self._client.bucket(self._bucket_name)
        for blob in self._client.list_blobs(
            bucket, prefix=_batch_prefix(batch_id)
        ):
            blob.delete()


__all__ = ["GCSBatchStorage"]
