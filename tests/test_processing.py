"""Tests for the processing pipeline.

The GNPS task imports ``GNPSInput``, ``GNPSProcessor`` and ``ANNOTATION_COLUMNS``
lazily from ``mf_engine.processors.gnps``. The ``fake_gnps`` fixture installs a
minimal stand-in for that module (and a fake processor that echoes a fixed
annotation table), so the pipeline runs end to end without the real engine.
"""

from __future__ import annotations

import csv
import io
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from mf_service.processing import (
    ANNOTATIONS_OUTPUT,
    GNPSAnnotationTask,
    run_pipeline,
)
from mf_service.storage import InMemoryBatchStorage

# The fake processor's fixed output: the columns it declares and the rows it
# returns (one row, with an extra key the writer must drop).
_FAKE_COLUMNS = ("feature_id", "compound", "score")
_FAKE_ANNOTATIONS = [
    {"feature_id": "1", "compound": "caffeine", "score": "0.9", "extra": "x"},
]


class _FakeResult:
    def __init__(self, annotations: list[dict[str, Any]]) -> None:
        self.annotations = annotations


class _FakeGNPSInput:
    def __init__(
        self,
        annotations: list[dict[str, Any]],
        feature_table: list[dict[str, Any]],
        library: list[dict[str, Any]],
    ) -> None:
        self.annotations = annotations
        self.feature_table = feature_table
        self.library = library


class _FakeGNPSProcessor:
    """Echoes a fixed annotation table and records the input it received."""

    def __init__(self) -> None:
        self.received: _FakeGNPSInput | None = None

    def process(self, gnps_input: _FakeGNPSInput) -> _FakeResult:
        self.received = gnps_input
        return _FakeResult(_FAKE_ANNOTATIONS)


@pytest.fixture
def fake_gnps() -> Iterator[None]:
    """Install a fake ``mf_engine.processors.gnps`` module for the test."""
    module = types.ModuleType("mf_engine.processors.gnps")
    module.GNPSInput = _FakeGNPSInput  # type: ignore[attr-defined]
    module.GNPSProcessor = _FakeGNPSProcessor  # type: ignore[attr-defined]
    module.ANNOTATION_COLUMNS = _FAKE_COLUMNS  # type: ignore[attr-defined]
    names = ("mf_engine", "mf_engine.processors", "mf_engine.processors.gnps")
    saved = {name: sys.modules.get(name) for name in names}
    mf_engine = sys.modules.get("mf_engine") or types.ModuleType("mf_engine")
    processors = sys.modules.get("mf_engine.processors") or types.ModuleType(
        "mf_engine.processors"
    )
    processors.gnps = module  # type: ignore[attr-defined]
    sys.modules["mf_engine"] = mf_engine
    sys.modules["mf_engine.processors"] = processors
    sys.modules["mf_engine.processors.gnps"] = module
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _seed_inputs(storage: InMemoryBatchStorage, batch_id: str) -> None:
    """Upload the three input files the GNPS task reads."""
    storage.upload(
        batch_id, "mzmine_annotations.csv", b"feature_id\n1", content_type="text/csv"
    )
    storage.upload(
        batch_id,
        "gnps_feature_table_reformated.csv",
        b"feature_id\n1",
        content_type="text/csv",
    )
    storage.upload(
        batch_id,
        "gnps_merged_results.tsv",
        b"feature_id\tx\n1\ty",  # TSV, read with a tab delimiter
        content_type="text/tab-separated-values",
    )


def test_run_pipeline_produces_annotations_csv(fake_gnps: None) -> None:
    storage = InMemoryBatchStorage()
    _seed_inputs(storage, "b1")
    processor = _FakeGNPSProcessor()

    outputs = run_pipeline("b1", storage, processor)

    # The pipeline returns the produced files (it does not upload them).
    assert set(outputs) == {ANNOTATIONS_OUTPUT}
    rows = list(csv.DictReader(io.StringIO(outputs[ANNOTATIONS_OUTPUT].decode())))
    assert [r["compound"] for r in rows] == ["caffeine"]
    # The output columns are exactly ANNOTATION_COLUMNS — the extra input key is
    # dropped, so the schema stays stable.
    assert list(rows[0].keys()) == list(_FAKE_COLUMNS)


def test_run_pipeline_reads_library_as_tsv(fake_gnps: None) -> None:
    # The library input is tab-separated; confirm the task parsed it with a tab
    # delimiter (one record with the expected columns), not as CSV.
    storage = InMemoryBatchStorage()
    _seed_inputs(storage, "b1")
    processor = _FakeGNPSProcessor()
    run_pipeline("b1", storage, processor)
    assert processor.received is not None
    assert processor.received.library == [{"feature_id": "1", "x": "y"}]


def test_gnps_task_is_a_pipeline_task(fake_gnps: None) -> None:
    # The default task reads/writes only through the context; a direct run over
    # a seeded context records the output on it.
    from mf_service.processing import PipelineContext

    storage = InMemoryBatchStorage()
    _seed_inputs(storage, "b1")
    ctx = PipelineContext(
        batch_id="b1", storage=storage, processor=_FakeGNPSProcessor()
    )
    GNPSAnnotationTask().run(ctx)
    assert ANNOTATIONS_OUTPUT in ctx.outputs
