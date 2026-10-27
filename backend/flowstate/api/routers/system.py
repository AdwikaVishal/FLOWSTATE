"""System information and the Overview dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Request

from flowstate.api.deps import ClockDep, SettingsDep, UowDep
from flowstate.api.serializers import run_summary
from flowstate.domain.schemas import OverviewResponse, SystemInfoResponse
from flowstate.services import system

router = APIRouter(prefix="/system", tags=["system"])


@router.get("", response_model=SystemInfoResponse)
def info(request: Request, settings: SettingsDep, clock: ClockDep) -> SystemInfoResponse:
    state = request.app.state
    payload = system.system_info(
        settings,
        clock,
        started_at=state.started_at,
        db_url=state.db.url,
        schema_version=state.schema_version,
        db_healthy=state.db.healthy(),
    )
    return SystemInfoResponse.model_validate(payload)


@router.get("/overview", response_model=OverviewResponse)
def overview(
    request: Request, uow: UowDep, settings: SettingsDep, clock: ClockDep
) -> OverviewResponse:
    state = request.app.state
    payload = system.overview(
        uow,
        clock,
        settings,
        started_at=state.started_at,
        db_healthy=state.db.healthy(),
    )
    payload["recent_runs"] = [
        run_summary(uow, item)
        for item in uow.runs.list(order_by="created_at", descending=True, limit=8)
    ]
    return OverviewResponse.model_validate(payload)
