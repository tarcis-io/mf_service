"""Tests for the /batches CRUD routes.

These exercise the full HTTP surface against an isolated in-memory repository
and file store (the ``batch_client`` fixture), including user scoping via the
X-User-Id header, the multipart create + background upload, and the 404/409/413/
422 mappings. TestClient runs background tasks before returning, so a create
call's upload has already finished (and flipped the status) once it returns.
"""

from __future__ import annotations

import dataclasses
import inspect
import io
import zipfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import mf_service.api.v1.batches as batches_module
from conftest import USER, auth
from mf_service.domain import REQUIRED_BATCH_FILES, Batch, BatchStatus
from mf_service.repository import InMemoryBatchRepository
from mf_service.schemas import BatchResponse
from mf_service.storage import InMemoryBatchStorage

BATCH_FIELDS = {
    "id",
    "user_id",
    "name",
    "created",
    "updated",
    "status",
    "error_message",
}


def test_response_model_fields_match_the_domain_entity() -> None:
    # BatchResponse is populated from a Batch via from_attributes, so the two
    # must carry the same fields: a field added to one but not the other would
    # silently vanish from the API. This guards that pairing (and BATCH_FIELDS).
    domain_fields = {f.name for f in dataclasses.fields(Batch)}
    assert set(BatchResponse.model_fields) == domain_fields == BATCH_FIELDS


def _files(**overrides: bytes) -> dict[str, tuple[str, bytes, str]]:
    """Build the multipart ``files=`` mapping with all required parts.

    Each part defaults to a tiny unique CSV/TSV payload; pass ``overrides`` keyed
    by filename to replace a part's bytes (e.g. an oversize body), or drop one by
    passing it as ``None`` via ``_files_without``.
    """
    out: dict[str, tuple[str, bytes, str]] = {}
    for filename in REQUIRED_BATCH_FILES:
        ctype = "text/tab-separated-values" if filename.endswith(".tsv") else "text/csv"
        data = overrides.get(filename, f"col\n{filename}".encode())
        out[filename] = (filename, data, ctype)
    return out


def _create(
    client: TestClient, name: str = "b1", user: str = USER, **override: bytes
) -> dict:
    resp = client.post(
        "/batches", data={"name": name}, files=_files(**override), headers=auth(user)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- create ------------------------------------------------------------------


def test_create_returns_201_with_full_batch(batch_client: TestClient) -> None:
    resp = batch_client.post(
        "/batches", data={"name": "b1"}, files=_files(), headers=auth()
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == BATCH_FIELDS


def test_create_returns_creating_then_uploads_in_background(
    batch_client: TestClient,
    batch_repo: InMemoryBatchRepository,
    batch_storage: InMemoryBatchStorage,
) -> None:
    # The response is serialised before the background upload runs, so it shows
    # the initial 'creating'. Once the call returns (TestClient has run the task)
    # the batch is 'created' and every required file is in storage under its id.
    body = _create(batch_client, "b1")
    assert body["status"] == BatchStatus.CREATING.value

    stored_batch = batch_repo.get(USER, body["id"])
    assert stored_batch.status is BatchStatus.CREATED
    assert set(batch_storage.files_for(body["id"])) == set(REQUIRED_BATCH_FILES)


def test_create_assigns_server_managed_fields(batch_client: TestClient) -> None:
    body = _create(batch_client, "b1")
    assert body["name"] == "b1"
    assert body["user_id"] == USER
    assert body["id"]  # server-generated UUID
    assert body["status"] == BatchStatus.CREATING.value
    assert body["error_message"] is None
    # A fresh batch has updated == created.
    assert body["updated"] == body["created"]


def test_create_duplicate_name_is_409(batch_client: TestClient) -> None:
    _create(batch_client, "dup")
    resp = batch_client.post(
        "/batches", data={"name": "dup"}, files=_files(), headers=auth()
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]  # a non-empty message (copy not pinned here)


def test_create_same_name_different_users_both_succeed(
    batch_client: TestClient,
) -> None:
    _create(batch_client, "same", user="user-a")
    resp = batch_client.post(
        "/batches", data={"name": "same"}, files=_files(), headers=auth("user-b")
    )
    assert resp.status_code == 201


def test_create_without_user_header_is_422(batch_client: TestClient) -> None:
    resp = batch_client.post("/batches", data={"name": "b1"}, files=_files())
    assert resp.status_code == 422


def test_create_with_blank_user_header_is_422(batch_client: TestClient) -> None:
    resp = batch_client.post(
        "/batches",
        data={"name": "b1"},
        files=_files(),
        headers={"X-User-Id": "   "},
    )
    assert resp.status_code == 422


def test_create_with_whitespace_name_is_422(batch_client: TestClient) -> None:
    # Whitespace is stripped first, so an all-blank name trims to empty -> 422.
    resp = batch_client.post(
        "/batches", data={"name": "   "}, files=_files(), headers=auth()
    )
    assert resp.status_code == 422


def test_create_without_name_is_422(batch_client: TestClient) -> None:
    resp = batch_client.post("/batches", files=_files(), headers=auth())
    assert resp.status_code == 422


def test_create_name_at_max_length_is_allowed(batch_client: TestClient) -> None:
    resp = batch_client.post(
        "/batches", data={"name": "x" * 255}, files=_files(), headers=auth()
    )
    assert resp.status_code == 201


def test_create_name_too_long_is_422(batch_client: TestClient) -> None:
    resp = batch_client.post(
        "/batches", data={"name": "x" * 256}, files=_files(), headers=auth()
    )
    assert resp.status_code == 422


def test_create_missing_a_required_file_is_422(batch_client: TestClient) -> None:
    # Drop one required part; FastAPI's required-parameter check rejects it.
    files = _files()
    del files["mzmine_metadata.csv"]
    resp = batch_client.post(
        "/batches", data={"name": "b1"}, files=files, headers=auth()
    )
    assert resp.status_code == 422


def test_create_with_empty_name_is_422(batch_client: TestClient) -> None:
    resp = batch_client.post(
        "/batches", data={"name": ""}, files=_files(), headers=auth()
    )
    assert resp.status_code == 422


def test_create_file_over_limit_is_413(batch_client: TestClient) -> None:
    # One file just over the configured 10 MiB default → 413 Payload Too Large.
    oversize = b"x" * (10 * 1024 * 1024 + 1)
    resp = batch_client.post(
        "/batches",
        data={"name": "b1"},
        files=_files(**{"gnps_merged_results.tsv": oversize}),
        headers=auth(),
    )
    assert resp.status_code == 413


def test_create_upload_failure_sets_error_status_and_message(
    batch_client: TestClient,
    batch_repo: InMemoryBatchRepository,
    batch_storage: InMemoryBatchStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If an upload raises in the background job, the batch is moved to 'error'
    # with the exception message (and 'updated' is stamped). The create response
    # itself is still 201 'creating' — the failure happens after it is sent.
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("bucket unavailable")

    monkeypatch.setattr(batch_storage, "upload", boom)

    body = _create(batch_client, "b1")
    assert body["status"] == BatchStatus.CREATING.value

    stored = batch_repo.get(USER, body["id"])
    assert stored.status is BatchStatus.ERROR
    assert stored.error_message == "bucket unavailable"
    assert stored.updated >= stored.created


def test_create_response_field_types(batch_client: TestClient) -> None:
    # The wire format: timestamps are ISO strings, status is a string, and an
    # unset error_message is null — guards against a field silently changing type.
    body = _create(batch_client, "b1")
    assert isinstance(body["id"], str)
    assert isinstance(body["created"], str)
    assert isinstance(body["updated"], str)
    datetime.fromisoformat(body["created"])  # parseable ISO-8601
    datetime.fromisoformat(body["updated"])
    assert body["status"] == "creating"
    assert body["error_message"] is None


def test_create_and_batch_routes_are_sync() -> None:
    # The routes are plain `def` (run in FastAPI's threadpool), not `async def`:
    # a real backend/engine call here is blocking and must not stall the loop.
    for fn in (
        batches_module.create_batch,
        batches_module.list_batches,
        batches_module.get_batch,
        batches_module.update_batch,
        batches_module.delete_batch,
    ):
        assert not inspect.iscoroutinefunction(fn)


# --- list --------------------------------------------------------------------


def test_list_empty(batch_client: TestClient) -> None:
    resp = batch_client.get("/batches", headers=auth())
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_returns_newest_first(batch_client: TestClient) -> None:
    _create(batch_client, "first")
    _create(batch_client, "second")
    _create(batch_client, "third")
    names = [b["name"] for b in batch_client.get("/batches", headers=auth()).json()]
    assert names == ["third", "second", "first"]


def test_list_is_scoped_to_the_caller(batch_client: TestClient) -> None:
    _create(batch_client, "mine", user="user-a")
    _create(batch_client, "theirs", user="user-b")
    a = batch_client.get("/batches", headers=auth("user-a")).json()
    assert [b["name"] for b in a] == ["mine"]


def test_list_without_user_header_is_422(batch_client: TestClient) -> None:
    assert batch_client.get("/batches").status_code == 422


# --- get ---------------------------------------------------------------------


def test_get_returns_the_batch(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    resp = batch_client.get(f"/batches/{created['id']}", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_missing_is_404(batch_client: TestClient) -> None:
    resp = batch_client.get("/batches/missing", headers=auth())
    assert resp.status_code == 404
    assert resp.json()["detail"]  # a non-empty message (copy not pinned here)


def test_get_other_users_batch_is_404(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1", user="user-a")
    resp = batch_client.get(f"/batches/{created['id']}", headers=auth("user-b"))
    assert resp.status_code == 404


def test_get_without_user_header_is_422(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    assert batch_client.get(f"/batches/{created['id']}").status_code == 422


# --- list files --------------------------------------------------------------


def test_list_files_returns_all_uploaded(batch_client: TestClient) -> None:
    # After create, the background upload has stored all required files; the
    # listing returns one entry per file, with metadata.
    created = _create(batch_client, "b1")
    resp = batch_client.get(f"/batches/{created['id']}/files", headers=auth())
    assert resp.status_code == 200
    body = resp.json()
    assert {f["filename"] for f in body} == set(REQUIRED_BATCH_FILES)
    one = body[0]
    assert set(one) == {"filename", "size", "content_type", "updated"}
    assert isinstance(one["size"], int)


def test_list_files_missing_batch_is_404(batch_client: TestClient) -> None:
    resp = batch_client.get("/batches/missing/files", headers=auth())
    assert resp.status_code == 404


def test_list_files_other_users_batch_is_404(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1", user="user-a")
    resp = batch_client.get(
        f"/batches/{created['id']}/files", headers=auth("user-b")
    )
    assert resp.status_code == 404


def test_list_files_without_user_header_is_422(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    assert batch_client.get(f"/batches/{created['id']}/files").status_code == 422


# --- download a file ---------------------------------------------------------


def test_download_file_returns_bytes_and_disposition(
    batch_client: TestClient,
) -> None:
    created = _create(batch_client, "b1")
    resp = batch_client.get(
        f"/batches/{created['id']}/files/mzmine_metadata.csv", headers=auth()
    )
    assert resp.status_code == 200
    # The body is the exact bytes _files() uploaded for that part.
    assert resp.content == b"col\nmzmine_metadata.csv"
    assert resp.headers["content-type"] == "application/octet-stream"
    disp = resp.headers["content-disposition"]
    assert "attachment" in disp
    assert "mzmine_metadata.csv" in disp


def test_download_missing_file_is_404(batch_client: TestClient) -> None:
    # Batch exists (and is owned), but the named file was never uploaded.
    created = _create(batch_client, "b1")
    resp = batch_client.get(
        f"/batches/{created['id']}/files/nope.csv", headers=auth()
    )
    assert resp.status_code == 404


def test_download_missing_batch_is_404(batch_client: TestClient) -> None:
    resp = batch_client.get(
        "/batches/missing/files/mzmine_metadata.csv", headers=auth()
    )
    assert resp.status_code == 404


def test_download_other_users_batch_is_404(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1", user="user-a")
    resp = batch_client.get(
        f"/batches/{created['id']}/files/mzmine_metadata.csv",
        headers=auth("user-b"),
    )
    assert resp.status_code == 404


def test_download_without_user_header_is_422(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    resp = batch_client.get(
        f"/batches/{created['id']}/files/mzmine_metadata.csv"
    )
    assert resp.status_code == 422


# --- download archive (all files as a zip) -----------------------------------


def test_archive_returns_zip_of_all_files(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    resp = batch_client.get(
        f"/batches/{created['id']}/files/archive", headers=auth()
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert ".zip" in resp.headers["content-disposition"]
    # The body is a valid zip containing exactly the required files, each with
    # the bytes _files() uploaded.
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    assert set(archive.namelist()) == set(REQUIRED_BATCH_FILES)
    assert archive.read("mzmine_metadata.csv") == b"col\nmzmine_metadata.csv"


def test_archive_of_batch_with_no_files_is_empty_zip(
    batch_client: TestClient, batch_repo: InMemoryBatchRepository
) -> None:
    # A batch that exists but has no stored files yields a valid, empty zip
    # (not a 404). Create the batch directly so nothing is uploaded.
    batch = batch_repo.create(Batch(user_id=USER, name="empty"))
    resp = batch_client.get(
        f"/batches/{batch.id}/files/archive", headers=auth()
    )
    assert resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    assert archive.namelist() == []


def test_archive_does_not_shadow_single_file_download(
    batch_client: TestClient,
) -> None:
    # Guards route ordering: '/files/archive' is the zip route, while
    # '/files/<name>' still downloads that one file (archive is not treated as a
    # filename, and a real filename is not treated as the archive route).
    created = _create(batch_client, "b1")
    one = batch_client.get(
        f"/batches/{created['id']}/files/mzmine_metadata.csv", headers=auth()
    )
    assert one.headers["content-type"] == "application/octet-stream"
    archive = batch_client.get(
        f"/batches/{created['id']}/files/archive", headers=auth()
    )
    assert archive.headers["content-type"] == "application/zip"


def test_archive_missing_batch_is_404(batch_client: TestClient) -> None:
    resp = batch_client.get("/batches/missing/files/archive", headers=auth())
    assert resp.status_code == 404


def test_archive_other_users_batch_is_404(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1", user="user-a")
    resp = batch_client.get(
        f"/batches/{created['id']}/files/archive", headers=auth("user-b")
    )
    assert resp.status_code == 404


def test_archive_without_user_header_is_422(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    assert (
        batch_client.get(f"/batches/{created['id']}/files/archive").status_code
        == 422
    )


# --- patch (rename) ----------------------------------------------------------


def test_patch_renames_the_batch(batch_client: TestClient) -> None:
    created = _create(batch_client, "old")
    resp = batch_client.patch(
        f"/batches/{created['id']}", json={"name": "new"}, headers=auth()
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "new"


def test_patch_does_not_change_status_or_updated(
    batch_client: TestClient,
) -> None:
    created = _create(batch_client, "old")
    # Read the batch's actual state just before renaming: the background upload
    # has already run, so status is now 'created' and updated has advanced.
    # Baseline against that, not the create response's stale 'creating'.
    before = batch_client.get(
        f"/batches/{created['id']}", headers=auth()
    ).json()
    renamed = batch_client.patch(
        f"/batches/{created['id']}", json={"name": "new"}, headers=auth()
    ).json()
    # Rename is not a status change: status and updated are untouched by it.
    assert renamed["status"] == before["status"]
    assert renamed["updated"] == before["updated"]


def test_patch_missing_is_404(batch_client: TestClient) -> None:
    resp = batch_client.patch(
        "/batches/missing", json={"name": "new"}, headers=auth()
    )
    assert resp.status_code == 404


def test_patch_duplicate_name_is_409(batch_client: TestClient) -> None:
    _create(batch_client, "taken")
    other = _create(batch_client, "free")
    resp = batch_client.patch(
        f"/batches/{other['id']}", json={"name": "taken"}, headers=auth()
    )
    assert resp.status_code == 409


def test_patch_other_users_batch_is_404(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1", user="user-a")
    resp = batch_client.patch(
        f"/batches/{created['id']}", json={"name": "new"}, headers=auth("user-b")
    )
    assert resp.status_code == 404


def test_patch_empty_name_is_422(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    resp = batch_client.patch(
        f"/batches/{created['id']}", json={"name": ""}, headers=auth()
    )
    assert resp.status_code == 422


def test_patch_name_too_long_is_422(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    resp = batch_client.patch(
        f"/batches/{created['id']}", json={"name": "x" * 256}, headers=auth()
    )
    assert resp.status_code == 422


def test_patch_without_user_header_is_422(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    resp = batch_client.patch(f"/batches/{created['id']}", json={"name": "new"})
    assert resp.status_code == 422


def test_patch_with_blank_user_header_is_422(batch_client: TestClient) -> None:
    # A blank/whitespace-only X-User-Id is rejected (trimmed to empty), same as
    # create — the header dependency is shared across all batch routes.
    created = _create(batch_client, "b1")
    resp = batch_client.patch(
        f"/batches/{created['id']}",
        json={"name": "new"},
        headers={"X-User-Id": "   "},
    )
    assert resp.status_code == 422


def test_patch_to_same_name_succeeds(batch_client: TestClient) -> None:
    # Renaming a batch to its own current name is a no-op, not a 409 — a batch
    # doesn't collide with itself.
    created = _create(batch_client, "keep")
    resp = batch_client.patch(
        f"/batches/{created['id']}", json={"name": "keep"}, headers=auth()
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "keep"


def test_patch_with_unknown_field_is_422(batch_client: TestClient) -> None:
    # extra="forbid" on the update body too (e.g. an attempt to PATCH a
    # server-managed field like status is rejected rather than ignored).
    created = _create(batch_client, "b1")
    resp = batch_client.patch(
        f"/batches/{created['id']}",
        json={"name": "new", "status": "processed"},
        headers=auth(),
    )
    assert resp.status_code == 422


# --- delete ------------------------------------------------------------------


def test_delete_returns_204_and_removes(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    resp = batch_client.delete(f"/batches/{created['id']}", headers=auth())
    assert resp.status_code == 204
    # Gone afterwards.
    assert (
        batch_client.get(f"/batches/{created['id']}", headers=auth()).status_code
        == 404
    )


def test_delete_also_removes_the_stored_files(
    batch_client: TestClient, batch_storage: InMemoryBatchStorage
) -> None:
    # Deleting a batch cascades to its storage folder: the uploaded files are
    # gone afterwards.
    created = _create(batch_client, "b1")
    assert batch_storage.files_for(created["id"])  # uploaded by the create job
    batch_client.delete(f"/batches/{created['id']}", headers=auth())
    assert batch_storage.files_for(created["id"]) == {}


def test_delete_missing_is_404(batch_client: TestClient) -> None:
    resp = batch_client.delete("/batches/missing", headers=auth())
    assert resp.status_code == 404


def test_delete_other_users_batch_leaves_files_intact(
    batch_client: TestClient, batch_storage: InMemoryBatchStorage
) -> None:
    created = _create(batch_client, "b1", user="user-a")
    resp = batch_client.delete(f"/batches/{created['id']}", headers=auth("user-b"))
    assert resp.status_code == 404
    # A 404 must not touch storage — the owner's files are untouched.
    assert set(batch_storage.files_for(created["id"])) == set(REQUIRED_BATCH_FILES)


def test_delete_without_user_header_is_422(batch_client: TestClient) -> None:
    created = _create(batch_client, "b1")
    assert batch_client.delete(f"/batches/{created['id']}").status_code == 422


# --- precedence --------------------------------------------------------------


def test_missing_header_wins_over_missing_batch_is_422(
    batch_client: TestClient,
) -> None:
    # The X-User-Id dependency resolves before the route body, so a request with
    # neither a header nor an existing batch is 422 (auth-ish), not 404.
    resp = batch_client.get("/batches/does-not-exist")
    assert resp.status_code == 422


# --- round trip over the shared repository ----------------------------------


def test_created_batch_is_visible_through_the_repository(
    batch_client: TestClient, batch_repo: InMemoryBatchRepository
) -> None:
    # The route and the fixture share one repository, so a created batch is
    # retrievable directly — proving the route actually persisted it. By the time
    # _create returns, the background upload has finished and flipped it to
    # 'created' (TestClient runs background tasks before returning).
    created = _create(batch_client, "b1")
    stored = batch_repo.get(USER, created["id"])
    assert stored.name == "b1"
    assert stored.status is BatchStatus.CREATED


def test_error_state_is_serialised_in_the_response(
    batch_client: TestClient, batch_repo: InMemoryBatchRepository
) -> None:
    # Drive the batch into the error state through the shared repo (as a worker
    # would), then confirm GET surfaces status=error and the error_message.
    created = _create(batch_client, "b1")
    batch_repo.update_status(
        created["id"], BatchStatus.ERROR, error_message="boom"
    )
    body = batch_client.get(f"/batches/{created['id']}", headers=auth()).json()
    assert body["status"] == "error"
    assert body["error_message"] == "boom"
