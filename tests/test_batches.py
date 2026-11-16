"""Tests for the /batches CRUD endpoints (via the in-memory repository)."""

from __future__ import annotations

from fastapi.testclient import TestClient

USER = {"X-User-Id": "user-1"}
OTHER = {"X-User-Id": "user-2"}


def _create(client: TestClient, name: str, headers=USER):
    return client.post("/batches", json={"name": name}, headers=headers)


def test_create_returns_201_and_full_batch(batch_client: TestClient):
    resp = _create(batch_client, "Spring run")
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Spring run"
    assert body["user_id"] == "user-1"
    assert body["status"] == "created"
    assert body["error_message"] is None
    assert body["id"]  # server-assigned uuid
    assert body["created"]


def test_create_requires_user_header(batch_client: TestClient):
    # Missing X-User-Id -> 422 (FastAPI validates the required header).
    resp = batch_client.post("/batches", json={"name": "x"})
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
    assert batch_client.get(f"/batches/{created['id']}", headers=USER).status_code == 404


def test_delete_missing_is_404(batch_client: TestClient):
    resp = batch_client.delete("/batches/no-such-id", headers=USER)
    assert resp.status_code == 404


def test_delete_other_users_batch_is_404(batch_client: TestClient):
    created = _create(batch_client, "mine", USER).json()
    resp = batch_client.delete(f"/batches/{created['id']}", headers=OTHER)
    assert resp.status_code == 404
