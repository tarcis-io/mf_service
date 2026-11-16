"""Tests for the processing pipeline (CSV helpers, pipeline, GNPS task)."""

from __future__ import annotations

import sys
import types

import pytest

from mf_service.processing import (
    ANNOTATIONS_OUTPUT,
    GNPSAnnotationTask,
    PipelineContext,
    _read_table,
    _write_csv,
    run_pipeline,
)
from mf_service.storage import InMemoryStorageGateway

# --------------------------------------------------------------------------
# CSV helpers
# --------------------------------------------------------------------------


def test_read_table_parses_csv():
    rows = _read_table(b"a,b\n1,2\n3,4\n")
    assert rows == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_read_table_parses_tsv_and_strips_bom():
    rows = _read_table(b"\xef\xbb\xbfx\ty\n1\t2\n", delimiter="\t")
    assert rows == [{"x": "1", "y": "2"}]  # BOM stripped from the header


def test_write_csv_fixes_header_order_and_handles_missing_extra():
    data = _write_csv(
        [{"a": "1", "b": "2", "c": "ignored"}, {"a": "3"}],
        columns=["a", "b"],
    )
    assert data.decode() == "a,b\r\n1,2\r\n3,\r\n"  # c dropped; missing b blank


def test_csv_roundtrips():
    records = [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]
    assert _read_table(_write_csv(records, ["a", "b"])) == records


# --------------------------------------------------------------------------
# run_pipeline mechanics (with a trivial fake task)
# --------------------------------------------------------------------------


class _RecordingTask:
    def __init__(self) -> None:
        self.seen: str | None = None

    def run(self, ctx: PipelineContext) -> None:
        self.seen = ctx.batch_id
        ctx.outputs["out.csv"] = b"done"


def test_run_pipeline_runs_tasks_and_collects_outputs():
    storage = InMemoryStorageGateway()
    task = _RecordingTask()
    outputs = run_pipeline("b1", storage, processor=None, pipeline=(task,))
    assert task.seen == "b1"
    assert outputs == {"out.csv": b"done"}


def test_run_pipeline_does_not_upload_outputs_itself():
    # The pipeline returns outputs; uploading is the caller's job (so a later
    # task failing leaves nothing written).
    storage = InMemoryStorageGateway()
    run_pipeline("b1", storage, processor=None, pipeline=(_RecordingTask(),))
    assert storage.objects == {}


# --------------------------------------------------------------------------
# GNPSAnnotationTask against a fake mf_engine
# --------------------------------------------------------------------------


@pytest.fixture
def _fake_engine(monkeypatch):
    """Install a fake ``mf_engine.processors.gnps`` with the symbols the task
    uses: ``GNPSInput``, ``ANNOTATION_COLUMNS``, and a processor whose
    ``process`` echoes the three input tables' row counts into one record.
    """
    mod = types.ModuleType("mf_engine.processors.gnps")

    class GNPSInput:
        def __init__(self, annotations, feature_table, library):
            self.annotations = annotations
            self.feature_table = feature_table
            self.library = library

    class GNPSResult:
        def __init__(self, annotations):
            self.annotations = annotations

    class FakeProcessor:
        def process(self, data: GNPSInput) -> GNPSResult:
            return GNPSResult(
                annotations=[
                    {
                        "Feature ID": "1",
                        "n_annotations": len(data.annotations),
                        "n_feature_table": len(data.feature_table),
                        "n_library": len(data.library),
                    }
                ]
            )

    mod.GNPSInput = GNPSInput
    mod.GNPSResult = GNPSResult
    mod.ANNOTATION_COLUMNS = [
        "Feature ID",
        "n_annotations",
        "n_feature_table",
        "n_library",
    ]
    monkeypatch.setitem(sys.modules, "mf_engine.processors.gnps", mod)
    return FakeProcessor()


def test_gnps_task_reads_inputs_runs_and_records_output(_fake_engine):
    storage = InMemoryStorageGateway()
    # Two annotation rows, one feature-table row, one (tab-separated) library row.
    storage.upload(
        "b1/mzmine_annotations.csv", b"id,score\n1,9\n2,3\n", content_type="text/csv"
    )
    storage.upload(
        "b1/gnps_feature_table_reformated.csv",
        b"row ID,row m/z\n1,100.0\n",
        content_type="text/csv",
    )
    storage.upload(
        "b1/gnps_merged_results.tsv",
        b"#Scan#\tMQScore\n1\t0.9\n",
        content_type="text/tab-separated-values",
    )

    ctx = PipelineContext(batch_id="b1", storage=storage, processor=_fake_engine)
    GNPSAnnotationTask().run(ctx)

    assert ANNOTATIONS_OUTPUT in ctx.outputs
    out = _read_table(ctx.outputs[ANNOTATIONS_OUTPUT])
    assert out == [
        {
            "Feature ID": "1",
            "n_annotations": "2",  # the library was read as TSV (1 row)
            "n_feature_table": "1",
            "n_library": "1",
        }
    ]
