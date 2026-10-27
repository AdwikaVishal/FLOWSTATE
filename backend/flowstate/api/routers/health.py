"""Health and readiness."""

from __future__ import annotations

from fastapi import APIRouter, Request

from flowstate import __version__
from flowstate.api.deps import ClockDep, SettingsDep
from flowstate.domain.schemas import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
def health(settings: SettingsDep, clock: ClockDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        time=clock.now(),
    )


@router.get("/health/ready", response_model=ReadyResponse, summary="Readiness")
def readiness(
    request: Request,
) -> ReadyResponse:
    state = request.app.state
    healthy = state.db.healthy()
    checks = {
        "database": "pass" if healthy else "fail",
        "evidence_store": "pass" if state.evidence_root.exists() else "warn",
        "schema": "pass" if state.schema_version >= 1 else "fail",
    }
    return ReadyResponse(
        status="ready" if healthy else "degraded",
        database=healthy,
        schema_version=state.schema_version,
        checks=checks,
    )
