"""Tests for the /batches CRUD endpoints (via the in-memory repository)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mf_service.domain import BatchStatus
from mf_service.repository import InMemoryBatchRepository
from mf_service.storage import InMemoryStorageGateway
from mf_service.uploads import REQUIRED_FILE_NAMES, REQUIRED_FILES

USER = {"X-User-Id": "user-1"}
OTHER = {"X-User-Id": "user-2"}

# The form-field names the endpoint declares (the keys of the field->filename
# map the route is built from).
REQUIRED_FILES_FIELDS = list(REQUIRED_FILE_NAMES)


def _files(omit: str | None = None) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Multipart ``files`` for the five required uploads, optionally dropping
    one (to exercise the missing-field 422).
    """
    return [
        (field, (f"{field}.csv", f"data for {field}".encode(), "text/csv"))
        for field in REQUIRED_FILES_FIELDS
        if field != omit
    ]


def _create(client: TestClient, name: str, headers=USER, *, omit=None):
    return client.post(
        "/batches",
        data={"name": name},
        files=_files(omit=omit),
        headers=headers,
    )


def test_create_returns_201_and_full_batch(batch_client: TestClient):
    resp = _create(batch_client, "Spring run")
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Spring run"
    assert body["user_id"] == "user-1"
    # The response is built before the upload task runs, so it shows the born
    # state 'creating'.
    assert body["status"] == "creating"
    assert body["error_message"] is None
    assert body["id"]  # server-assigned uuid
    assert body["created"]


def test_create_uploads_all_files_and_marks_created(
    batch_client: TestClient,
    batch_repo: InMemoryBatchRepository,
    storage_gateway: InMemoryStorageGateway,
):
    # The background task runs before TestClient returns, so by now the files
    # are in storage (keyed <batch_id>/<filename>) and the batch is 'created'.
    body = _create(batch_client, "run").json()
    batch_id = body["id"]

    expected_keys = {f"{batch_id}/{name}" for name in REQUIRED_FILES}
    assert set(storage_gateway.objects) == expected_keys
    assert batch_repo.get("user-1", batch_id).status is BatchStatus.CREATED


def test_create_sets_tsv_content_type(
    batch_client: TestClient, storage_gateway: InMemoryStorageGateway
):
    body = _create(batch_client, "run").json()
    tsv_key = f"{body['id']}/gnps_merged_results.tsv"
    assert storage_gateway.content_types[tsv_key] == "text/tab-separated-values"


def test_create_requires_user_header(batch_client: TestClient):
    # Missing X-User-Id -> 422 (FastAPI validates the required header).
    resp = batch_client.post("/batches", data={"name": "x"}, files=_files())
    assert resp.status_code == 422


@pytest.mark.parametrize("missing", REQUIRED_FILES_FIELDS)
def test_create_requires_every_file(batch_client: TestClient, missing: str):
    # Dropping any one of the five required files is a 422 before any upload.
    resp = _create(batch_client, "x", omit=missing)
    assert resp.status_code == 422


def test_create_rejects_blank_name(batch_client: TestClient):
    resp = _create(batch_client, "   ")  # trimmed to empty -> fails min_length
    assert resp.status_code == 422


def test_create_duplicate_name_returns_409(batch_client: TestClient):
    _create(batch_client, "dup")
    resp = _create(batch_client, "dup")
    assert resp.status_code == 409
    assert "already in use" in resp.json()["detail"]


def test_duplicate_name_allowed_across_users(batch_client: TestClient):
    assert _create(batch_client, "shared", USER).status_code == 201
    assert _create(batch_client, "shared", OTHER).status_code == 201


def test_list_returns_only_callers_batches(batch_client: TestClient):
    _create(batch_client, "a", USER)
    _create(batch_client, "b", USER)
    _create(batch_client, "c", OTHER)

    resp = batch_client.get("/batches", headers=USER)
    assert resp.status_code == 200
    names = {b["name"] for b in resp.json()}
    assert names == {"a", "b"}


def test_get_returns_batch(batch_client: TestClient):
    created = _create(batch_client, "findme").json()
    resp = batch_client.get(f"/batches/{created['id']}", headers=USER)
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_other_users_batch_is_404(batch_client: TestClient):
    created = _create(batch_client, "mine", USER).json()
    resp = batch_client.get(f"/batches/{created['id']}", headers=OTHER)
    assert resp.status_code == 404


def test_get_missing_is_404(batch_client: TestClient):
    resp = batch_client.get("/batches/no-such-id", headers=USER)
    assert resp.status_code == 404


def test_update_renames(batch_client: TestClient):
    created = _create(batch_client, "before").json()
    resp = batch_client.patch(
        f"/batches/{created['id']}", json={"name": "after"}, headers=USER
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "after"


def test_update_rejects_blank_name(batch_client: TestClient):
    # BatchUpdateRequest strips whitespace then enforces min_length, so an
    # all-blank rename trims to empty and fails validation — mirroring create.
    created = _create(batch_client, "before").json()
    resp = batch_client.patch(
        f"/batches/{created['id']}", json={"name": "   "}, headers=USER
    )
    assert resp.status_code == 422


def test_update_duplicate_name_returns_409(batch_client: TestClient):
    _create(batch_client, "taken")
    other = _create(batch_client, "free").json()
    resp = batch_client.patch(
        f"/batches/{other['id']}", json={"name": "taken"}, headers=USER
    )
    assert resp.status_code == 409


def test_update_missing_is_404(batch_client: TestClient):
    resp = batch_client.patch(
        "/batches/no-such-id", json={"name": "x"}, headers=USER
    )
    assert resp.status_code == 404


def test_update_other_users_batch_is_404(batch_client: TestClient):
    created = _create(batch_client, "mine", USER).json()
    resp = batch_client.patch(
        f"/batches/{created['id']}", json={"name": "hijack"}, headers=OTHER
    )
    assert resp.status_code == 404


def test_delete_removes_batch(batch_client: TestClient):
    created = _create(batch_client, "temp").json()
    resp = batch_client.delete(f"/batches/{created['id']}", headers=USER)
    assert resp.status_code == 204
    gone = batch_client.get(f"/batches/{created['id']}", headers=USER)
    assert gone.status_code == 404


def test_delete_missing_is_404(batch_client: TestClient):
    resp = batch_client.delete("/batches/no-such-id", headers=USER)
    assert resp.status_code == 404


def test_delete_other_users_batch_is_404(batch_client: TestClient):
    created = _create(batch_client, "mine", USER).json()
    resp = batch_client.delete(f"/batches/{created['id']}", headers=OTHER)
    assert resp.status_code == 404
