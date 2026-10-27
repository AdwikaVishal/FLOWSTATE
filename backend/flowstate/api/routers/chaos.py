"""Chaos Lab endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from flowstate.api.deps import (
    ActorDep,
    ClockDep,
    EngineDep,
    IdempotencyDep,
    IdsDep,
    UowDep,
)
from flowstate.api.serializers import run_detail, scenario_out
from flowstate.domain.enums import Origin
from flowstate.domain.schemas import (
    ChaosReportOut,
    RunDetail,
    ScenarioCreate,
    ScenarioOut,
)
from flowstate.errors import NotFoundError
from flowstate.services import chaos
from flowstate.services.idempotency import lookup as idempotency_lookup
from flowstate.services.idempotency import remember as idempotency_remember
from flowstate.services.idempotency import request_hash

router = APIRouter(prefix="/scenarios", tags=["chaos"])


@router.get("", response_model=list[ScenarioOut])
def list_scenarios(
    uow: UowDep,
    workflow_id: Annotated[str | None, Query()] = None,
    origin: Annotated[Origin | None, Query()] = None,
) -> list[ScenarioOut]:
    filters: dict[str, object] = {}
    if workflow_id:
        filters["workflow_id"] = workflow_id
    if origin:
        filters["origin"] = origin.value
    rows = uow.scenarios.list(
        order_by="created_at",
        descending=True,
        **filters,  # type: ignore[arg-type]
    )
    return [scenario_out(uow, item) for item in rows]


@router.post("", response_model=ScenarioOut, status_code=status.HTTP_201_CREATED)
def create_scenario(
    payload: ScenarioCreate, uow: UowDep, clock: ClockDep, ids: IdsDep
) -> ScenarioOut:
    workflow = uow.workflows.get(payload.workflow_id)
    if workflow is None:
        raise NotFoundError(
            f"Workflow {payload.workflow_id} was not found.", code="WORKFLOW_NOT_FOUND"
        )
    scenario = chaos.create_scenario(
        uow,
        ids,
        clock,
        workflow=workflow,
        name=payload.name,
        description=payload.description,
        target_ordinal=payload.target_ordinal,
        fault_type=payload.fault_type,
        parameters=payload.parameters,
        input_fixtures=payload.input_fixtures,
        seed=payload.seed,
        origin=Origin.LIVE,
    )
    return scenario_out(uow, scenario)


@router.get("/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: str, uow: UowDep) -> ScenarioOut:
    return scenario_out(uow, chaos.get_scenario(uow, scenario_id))


@router.post(
    "/{scenario_id}/evaluate",
    response_model=ChaosReportOut,
    status_code=status.HTTP_201_CREATED,
)
def evaluate_scenario(
    scenario_id: str,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    engine: EngineDep,
    actor: ActorDep,
    idempotency_key: IdempotencyDep,
) -> ChaosReportOut:
    scope = f"POST /scenarios/{scenario_id}/evaluate"
    fingerprint = request_hash(scope, actor.id, {"scenario_id": scenario_id})
    if idempotency_key:
        cached = idempotency_lookup(
            uow, scope=scope, key=idempotency_key, fingerprint=fingerprint, now=clock.now()
        )
        if cached is not None:
            return ChaosReportOut.model_validate(cached)
    report = chaos.evaluate_scenario(
        uow, clock, ids, engine, scenario_id=scenario_id, actor=actor.id
    )
    response = ChaosReportOut.model_validate(report)
    if idempotency_key:
        idempotency_remember(
            uow,
            ids,
            clock,
            scope=scope,
            key=idempotency_key,
            fingerprint=fingerprint,
            response=response.model_dump(mode="json"),
            status_code=201,
        )
    return response


@router.get("/{scenario_id}/report", response_model=ChaosReportOut)
def get_report(scenario_id: str, uow: UowDep, clock: ClockDep) -> ChaosReportOut:
    return ChaosReportOut.model_validate(chaos.report_scenario(uow, clock, scenario_id))


@router.post("/{scenario_id}/run", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
def run_scenario(
    scenario_id: str,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    engine: EngineDep,
    actor: ActorDep,
) -> RunDetail:
    run_id = chaos.run_scenario(uow, clock, ids, engine, scenario_id=scenario_id, actor=actor.id)
    run = uow.runs.get(run_id)
    if run is None:  # pragma: no cover - defensive
        raise NotFoundError("The chaos run could not be loaded.", code="RUN_NOT_FOUND")
    return run_detail(uow, run)
