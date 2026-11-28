"""Batch processing — the engine pipeline run by ``POST /process``.

Defines the processing pipeline (an ordered sequence of tasks over a shared
context) and the task that runs :mod:`mf_engine`'s GNPS processor. The route and
its background job depend on :func:`run_pipeline` and :data:`DEFAULT_PIPELINE`,
so new tasks extend processing without touching the HTTP layer.
"""

from __future__ import annotations

from mf_service.processing.pipeline import (
    ANNOTATIONS_OUTPUT,
    DEFAULT_PIPELINE,
    GNPSAnnotationTask,
    PipelineContext,
    PipelineTask,
    run_pipeline,
)

__all__ = [
    "ANNOTATIONS_OUTPUT",
    "DEFAULT_PIPELINE",
    "GNPSAnnotationTask",
    "PipelineContext",
    "PipelineTask",
    "run_pipeline",
]
