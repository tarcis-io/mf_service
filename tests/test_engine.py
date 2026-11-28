"""Tests for the mf_engine version seam."""

from __future__ import annotations

import mf_engine

from mf_service.core.engine import engine_version


def test_engine_version_matches_mf_engine() -> None:
    # The seam returns mf_engine's own __version__ — the engine's single source
    # of truth — rather than anything the service defines itself.
    assert engine_version() == mf_engine.__version__
