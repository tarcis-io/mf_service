"""Tests for the POST /process route (start processing a batch).

/process is not user-scoped. These exercise batch selection (explicit id vs the
oldest 'created'), the 404/409 guards, and the background pipeline that flips the
batch to 'processed' (or 'error') and uploads the annotations output.

The route's background job runs the real pipeline, which imports
``mf_engine.processors.gnps``. We install a fake for that module and override
``get_gnps_processor`` with a fake processor, so no real engine is needed.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import USER
from mf_service import create_app
from mf_service.api.v1.process import _run_pipeline_then_finish
from mf_service.core.deps import (
    get_batch_repository,
    get_batch_storage,
    get_gnps_processor,
)
from mf_service.domain import Batch, BatchStatus
from mf_service.repository import BatchNotFoundError, InMemoryBatchRepository
from mf_service.storage import InMemoryBatchStorage

_ANNOTATION_COLUMNS = ("feature_id", "compound")
_ANNOTATIONS = [{"feature_id": "1", "compound": "caffeine"}]


class _FakeResult:
    def __init__(self, annotations: list[dict[str, Any]]) -> None:
        self.annotations = annotations


class _FakeGNPSInput:
    def __init__(self, annotations: Any, feature_table: Any, library: Any) -> None:
        self.annotations = annotations
        self.feature_table = feature_table
        self.library = library


class _FakeGNPSProcessor:
    def process(self, gnps_input: _FakeGNPSInput) -> _FakeResult:
        return _FakeResult(_ANNOTATIONS)


class _BoomProcessor:
    """A processor that fails, to drive the pipeline into the error path."""

    def process(self, gnps_input: _FakeGNPSInput) -> _FakeResult:
        raise RuntimeError("engine exploded")


@pytest.fixture
def fake_gnps_module() -> Iterator[None]:
    """Install a fake ``mf_engine.processors.gnps`` (for the pipeline's import)."""
    module = types.ModuleType("mf_engine.processors.gnps")
    module.GNPSInput = _FakeGNPSInput  # type: ignore[attr-defined]
    module.GNPSProcessor = _FakeGNPSProcessor  # type: ignore[attr-defined]
    module.ANNOTATION_COLUMNS = _ANNOTATION_COLUMNS  # type: ignore[attr-defined]
    names = ("mf_engine", "mf_engine.processors", "mf_engine.processors.gnps")
    saved = {name: sys.modules.get(name) for name in names}
    eng = sys.modules.get("mf_engine") or types.ModuleType("mf_engine")
    procs = sys.modules.get("mf_engine.processors") or types.ModuleType(
        "mf_engine.processors"
    )
    procs.gnps = module  # type: ignore[attr-defined]
    sys.modules["mf_engine"] = eng
    sys.modules["mf_engine.processors"] = procs
    sys.modules["mf_engine.processors.gnps"] = module
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _make_client(
    repo: InMemoryBatchRepository,
    storage: InMemoryBatchStorage,
    processor: Any,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_batch_repository] = lambda: repo
    app.dependency_overrides[get_batch_storage] = lambda: storage
    app.dependency_overrides[get_gnps_processor] = lambda: processor
    return TestClient(app)


def _seed_inputs(storage: InMemoryBatchStorage, batch_id: str) -> None:
    storage.upload(batch_id, "mzmine_annotations.csv", b"feature_id\n1")
    storage.upload(batch_id, "gnps_feature_table_reformated.csv", b"feature_id\n1")
    storage.upload(batch_id, "gnps_merged_results.tsv", b"feature_id\n1")


def _created_batch(
    repo: InMemoryBatchRepository, name: str = "b1", user: str = USER
) -> Batch:
    batch = repo.create(Batch(user_id=user, name=name))
    return repo.update_status(batch.id, BatchStatus.CREATED)


# --- selection ---------------------------------------------------------------


def test_process_explicit_id_returns_processing_then_processed(
    fake_gnps_module: None,
) -> None:
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    batch = _created_batch(repo)
    _seed_inputs(storage, batch.id)
    client = _make_client(repo, storage, _FakeGNPSProcessor())

    resp = client.post("/process", json={"batch_id": batch.id})
    assert resp.status_code == 200
    # The response is the batch at claim time: 'processing'.
    assert resp.json()["status"] == BatchStatus.PROCESSING.value
    # The background job has finished by the time the call returns: 'processed',
    # and the annotations output is in storage.
    stored = repo.get(USER, batch.id)
    assert stored.status is BatchStatus.PROCESSED
    assert "annotations.csv" in storage.files_for(batch.id)


def test_process_no_body_picks_oldest_created(fake_gnps_module: None) -> None:
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older = repo.create(Batch(user_id="u1", name="older", created=base))
    newer = repo.create(
        Batch(user_id="u1", name="newer", created=base + timedelta(hours=1))
    )
    repo.update_status(older.id, BatchStatus.CREATED)
    repo.update_status(newer.id, BatchStatus.CREATED)
    _seed_inputs(storage, older.id)
    client = _make_client(repo, storage, _FakeGNPSProcessor())

    # No body at all → oldest created batch is selected.
    resp = client.post("/process")
    assert resp.status_code == 200
    assert resp.json()["id"] == older.id


def test_process_empty_queue_is_404(fake_gnps_module: None) -> None:
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    repo.create(Batch(user_id="u1", name="creating"))  # not 'created'
    client = _make_client(repo, storage, _FakeGNPSProcessor())
    assert client.post("/process").status_code == 404


def test_process_missing_id_is_404(fake_gnps_module: None) -> None:
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    client = _make_client(repo, storage, _FakeGNPSProcessor())
    resp = client.post("/process", json={"batch_id": "nope"})
    assert resp.status_code == 404


def test_process_non_created_status_is_409(fake_gnps_module: None) -> None:
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    creating = repo.create(Batch(user_id="u1", name="fresh"))  # status creating
    client = _make_client(repo, storage, _FakeGNPSProcessor())
    resp = client.post("/process", json={"batch_id": creating.id})
    assert resp.status_code == 409


def test_process_is_not_user_scoped(fake_gnps_module: None) -> None:
    # No X-User-Id is sent, and the batch belongs to some other user.
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    batch = _created_batch(repo, user="someone-else")
    _seed_inputs(storage, batch.id)
    client = _make_client(repo, storage, _FakeGNPSProcessor())
    resp = client.post("/process", json={"batch_id": batch.id})
    assert resp.status_code == 200


# --- background pipeline failure ---------------------------------------------


def test_process_pipeline_error_sets_error_status_and_message(
    fake_gnps_module: None,
) -> None:
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    batch = _created_batch(repo)
    _seed_inputs(storage, batch.id)
    client = _make_client(repo, storage, _BoomProcessor())

    resp = client.post("/process", json={"batch_id": batch.id})
    # The response is still 200 'processing' — the failure happens afterwards.
    assert resp.status_code == 200
    assert resp.json()["status"] == BatchStatus.PROCESSING.value

    stored = repo.get(USER, batch.id)
    assert stored.status is BatchStatus.ERROR
    assert "engine exploded" in (stored.error_message or "")
    # No annotations output was written (the task raised before producing it).
    assert "annotations.csv" not in storage.files_for(batch.id)


# --- background job when the batch is deleted mid-run ------------------------
#
# The /process response returns before the background task runs, so a batch can
# be deleted in between. Then the task's status writes raise BatchNotFoundError,
# which the job must swallow as a no-op. TestClient runs background tasks inline
# (no window to delete between), so these drive the job function directly with a
# repository that has no such batch.


def test_background_job_success_path_is_noop_if_batch_gone(
    fake_gnps_module: None,
) -> None:
    # Pipeline succeeds, but update_status(PROCESSED) on a missing batch raises
    # BatchNotFoundError — swallowed, nothing marked (covers the success-path
    # except).
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    _seed_inputs(storage, "ghost")  # inputs exist so the pipeline can run
    _run_pipeline_then_finish(repo, storage, _FakeGNPSProcessor(), "ghost")
    # The output was produced and uploaded, but no batch record exists to mark.
    assert "annotations.csv" in storage.files_for("ghost")
    with pytest.raises(BatchNotFoundError):  # no such batch was ever marked
        repo.get(USER, "ghost")


# --- POST /process/timeouts (sweep stuck in-progress batches) ----------------
#
# These use the shared batch_client/batch_repo fixtures (no engine needed — the
# sweep only flips statuses). Stuck batches are seeded by pinning the clock to an
# old time while driving them to an in-progress status.


def _stuck(
    batch_repo: InMemoryBatchRepository,
    monkeypatch: pytest.MonkeyPatch,
    status: BatchStatus,
    when: datetime,
    name: str,
) -> Batch:
    batch = batch_repo.create(Batch(user_id=USER, name=name))
    monkeypatch.setattr("mf_service.domain.batch._now", lambda: when)
    stuck = batch_repo.update_status(batch.id, status)
    monkeypatch.undo()  # restore the real clock for the rest of the test
    return stuck


def test_timeouts_default_threshold_sweeps_old_batches(
    batch_client: TestClient,
    batch_repo: InMemoryBatchRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stamped two hours ago — older than the default 1h threshold.
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    c = _stuck(batch_repo, monkeypatch, BatchStatus.CREATING, long_ago, "c")
    p = _stuck(batch_repo, monkeypatch, BatchStatus.PROCESSING, long_ago, "p")

    resp = batch_client.post("/process/timeouts")
    assert resp.status_code == 200
    body = resp.json()
    assert {b["id"] for b in body} == {c.id, p.id}
    assert all(b["status"] == "error" for b in body)
    assert all(b["error_message"] for b in body)


def test_timeouts_respects_explicit_older_than_seconds(
    batch_client: TestClient,
    batch_repo: InMemoryBatchRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stamped 30 min ago: under the default 1h, but over an explicit 60s cutoff.
    half_hour_ago = datetime.now(timezone.utc) - timedelta(minutes=30)
    _stuck(batch_repo, monkeypatch, BatchStatus.PROCESSING, half_hour_ago, "p")

    # Default threshold leaves it (not yet an hour old).
    assert batch_client.post("/process/timeouts").json() == []
    # An explicit small threshold sweeps it.
    body = batch_client.post("/process/timeouts?older_than_seconds=60").json()
    assert len(body) == 1 and body[0]["status"] == "error"


def test_timeouts_empty_when_nothing_stuck(batch_client: TestClient) -> None:
    assert batch_client.post("/process/timeouts").json() == []


def test_timeouts_rejects_non_positive_threshold(
    batch_client: TestClient,
) -> None:
    # older_than_seconds must be >= 1 (Query ge=1): 0 or negative is a 422.
    for value in (0, -5):
        resp = batch_client.post(f"/process/timeouts?older_than_seconds={value}")
        assert resp.status_code == 422


def test_timeouts_needs_no_user_header(
    batch_client: TestClient,
    batch_repo: InMemoryBatchRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No X-User-Id is sent (the sweep is global) and it still succeeds.
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    _stuck(batch_repo, monkeypatch, BatchStatus.PROCESSING, long_ago, "p")
    resp = batch_client.post("/process/timeouts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_background_job_failure_path_is_noop_if_batch_gone(
    fake_gnps_module: None,
) -> None:
    # The task raises AND the batch is gone, so update_status(ERROR) also raises
    # BatchNotFoundError — swallowed (covers the failure-path inner except).
    repo, storage = InMemoryBatchRepository(), InMemoryBatchStorage()
    _seed_inputs(storage, "ghost")
    # Must not raise even though both the pipeline and the error write fail.
    _run_pipeline_then_finish(repo, storage, _BoomProcessor(), "ghost")
