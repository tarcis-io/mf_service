"""Tests for the engine-integration dependency seam."""

from __future__ import annotations

from mf_engine.processors.gnps import GNPSProcessor

from mf_service.deps import get_gnps_processor


def test_provider_returns_gnps_processor():
    assert isinstance(get_gnps_processor(), GNPSProcessor)


def test_provider_is_cached():
    # One processor per process: the provider is lru_cached so routes share a
    # single stateless instance rather than rebuilding it per request.
    assert get_gnps_processor() is get_gnps_processor()
