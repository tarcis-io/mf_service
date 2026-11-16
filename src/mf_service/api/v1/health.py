"""The ``/health`` route — a liveness probe.

Returns a static ``{"status": "ok"}`` so load balancers, orchestrators
(Kubernetes liveness/readiness probes), and uptime checks can confirm the
service is up without touching any dependency.
"""

from __future__ import annotations

from fastapi import APIRouter

from mf_service.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
def health() -> HealthResponse:
    """Report that the service is live."""
    # `status` defaults to "ok" on the model; no field to set here.
    return HealthResponse()
