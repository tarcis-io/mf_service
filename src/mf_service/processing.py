"""Batch processing — the engine pipeline that turns inputs into annotations.

A batch that has reached ``created`` (all input files uploaded) is processed by
a **pipeline**: an ordered sequence of :class:`PipelineTask` steps that share a
mutable :class:`PipelineContext`. Today there is a single task
(:class:`GNPSAnnotationTask`), which runs :mod:`mf_engine`'s ``GNPSProcessor``
over the batch's input files and writes the per-feature annotation table to
storage as :data:`ANNOTATIONS_OUTPUT`. More tasks (scoring, summaries, group
comparisons, ...) slot into :data:`DEFAULT_PIPELINE` without touching the job or
the routes.

The pipeline is **storage-driven**: it reads the batch's input files from the
:class:`~mf_service.storage.StorageGateway`, runs the I/O-free engine over the
parsed records, and writes the result back through the same gateway. The engine
is imported lazily so importing this module stays cheap.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from mf_service.storage import StorageGateway

if TYPE_CHECKING:  # type-checker only; no runtime engine import
    from mf_engine.processors.gnps import GNPSProcessor

# The processing output, written to storage under ``<batch_id>/<name>`` so it
# lists and downloads through the same endpoints as the inputs.
ANNOTATIONS_OUTPUT = "annotations.csv"

# Input filenames the GNPS task reads, mapped to the GNPSInput field each feeds.
# (The two other uploaded files are not inputs to this processor.)
_GNPS_ANNOTATIONS_FILE = "mzmine_annotations.csv"
_GNPS_FEATURE_TABLE_FILE = "gnps_feature_table_reformated.csv"
_GNPS_LIBRARY_FILE = "gnps_merged_results.tsv"


def _read_table(data: bytes, *, delimiter: str = ",") -> list[dict[str, Any]]:
    """Parse CSV/TSV bytes into records (one dict per row)."""
    text = data.decode("utf-8-sig")  # tolerate a BOM from exported tables
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [dict(row) for row in reader]


def _write_csv(records: list[dict[str, Any]], columns: list[str]) -> bytes:
    """Serialise records to CSV bytes with a fixed column order.

    ``columns`` fixes the header and order so the output schema is stable even
    when a record omits a key (written blank) or carries an extra one (ignored).
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue().encode("utf-8")


@dataclass
class PipelineContext:
    """State threaded through a pipeline run.

    ``batch_id`` scopes every storage key to ``<batch_id>/<name>``. ``processor``
    is the engine processor the GNPS task needs. ``outputs`` collects the files a
    task produced (name -> bytes); the job uploads them once the pipeline
    finishes so a failure part-way writes nothing.
    """

    batch_id: str
    storage: StorageGateway
    processor: GNPSProcessor
    outputs: dict[str, bytes] = field(default_factory=dict)


class PipelineTask(Protocol):
    """One step of the processing pipeline."""

    def run(self, ctx: PipelineContext) -> None:
        """Do the step's work, reading from and recording onto ``ctx``."""


class GNPSAnnotationTask:
    """Run the GNPS processor over a batch's inputs into an annotation table.

    Reads the three input tables the processor needs from storage, runs the
    I/O-free ``GNPSProcessor``, and records the result as
    :data:`ANNOTATIONS_OUTPUT` on the context (the job uploads it).
    """

    def run(self, ctx: PipelineContext) -> None:
        from mf_engine.processors.gnps import ANNOTATION_COLUMNS, GNPSInput

        prefix = f"{ctx.batch_id}/"
        gnps_input = GNPSInput(
            annotations=_read_table(
                ctx.storage.download(prefix + _GNPS_ANNOTATIONS_FILE)
            ),
            feature_table=_read_table(
                ctx.storage.download(prefix + _GNPS_FEATURE_TABLE_FILE)
            ),
            library=_read_table(
                ctx.storage.download(prefix + _GNPS_LIBRARY_FILE),
                delimiter="\t",
            ),
        )
        result = ctx.processor.process(gnps_input)
        ctx.outputs[ANNOTATIONS_OUTPUT] = _write_csv(
            result.annotations, list(ANNOTATION_COLUMNS)
        )


# The pipeline, in run order. Appending a task here extends processing without
# changing the job or the routes.
DEFAULT_PIPELINE: tuple[PipelineTask, ...] = (GNPSAnnotationTask(),)


def run_pipeline(
    batch_id: str,
    storage: StorageGateway,
    processor: GNPSProcessor,
    *,
    pipeline: tuple[PipelineTask, ...] = DEFAULT_PIPELINE,
) -> dict[str, bytes]:
    """Run every task over ``batch_id`` and return the files produced.

    Tasks run in order against one shared context; each may read inputs and
    record outputs. The collected outputs are returned (not uploaded here) so
    the caller controls when they are written — nothing is stored if a task
    raises part-way through.
    """
    ctx = PipelineContext(batch_id=batch_id, storage=storage, processor=processor)
    for task in pipeline:
        task.run(ctx)
    return ctx.outputs
