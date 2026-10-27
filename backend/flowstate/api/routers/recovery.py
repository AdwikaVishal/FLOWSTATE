"""Recovery endpoints: plan, attempt, verify and manual intervention."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from flowstate.api.deps import ActorDep, ClockDep, EngineDep, IdsDep, UowDep
from flowstate.domain.enums import RecoveryStrategy
from flowstate.domain.schemas import (
    RecoverRequest,
    RecoveryNote,
    RecoveryOut,
    RecoveryPlanOut,
)
from flowstate.errors import NotFoundError
from flowstate.services import recovery as service

router = APIRouter(prefix="/recoveries", tags=["recovery"])
run_router = APIRouter(prefix="/runs", tags=["recovery"])


@router.get("", response_model=list[RecoveryOut])
def list_recoveries(
    uow: UowDep,
    run_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[RecoveryOut]:
    filters = {"run_id": run_id} if run_id else {}
    rows = uow.recoveries.list(
        order_by="started_at",
        descending=True,
        limit=limit,
        **filters,  # type: ignore[arg-type]
    )
    return [RecoveryOut.model_validate(item) for item in rows]


@router.get("/{attempt_id}", response_model=RecoveryOut)
def get_recovery(attempt_id: str, uow: UowDep) -> RecoveryOut:
    return RecoveryOut.model_validate(service.get_attempt(uow, attempt_id))


@router.post("/{attempt_id}/note", response_model=RecoveryOut)
def note_recovery(
    attempt_id: str,
    payload: RecoveryNote,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    actor: ActorDep,
) -> RecoveryOut:
    attempt = service.record_manual_note(
        uow,
        clock,
        ids,
        attempt_id=attempt_id,
        note=payload.note,
        actor=actor.id,
    )
    return RecoveryOut.model_validate(attempt)


@run_router.get("/{run_id}/recovery-plan", response_model=RecoveryPlanOut)
def recovery_plan(run_id: str, uow: UowDep) -> RecoveryPlanOut:
    run = uow.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    return RecoveryPlanOut.model_validate(service.plan_for_run(uow, run))


@run_router.get("/{run_id}/recoveries", response_model=list[RecoveryOut])
def list_run_recoveries(run_id: str, uow: UowDep) -> list[RecoveryOut]:
    if uow.runs.get(run_id) is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    rows = sorted(
        uow.recoveries.list(run_id=run_id),
        key=lambda item: item.started_at,
        reverse=True,
    )
    return [RecoveryOut.model_validate(item) for item in rows]


@run_router.post("/{run_id}/recover", response_model=RecoveryOut)
def recover_run(
    run_id: str,
    payload: RecoverRequest,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    engine: EngineDep,
    actor: ActorDep,
) -> RecoveryOut:
    strategy = RecoveryStrategy(payload.strategy) if payload.strategy else None
    attempt = service.recover(
        uow,
        clock,
        ids,
        engine,
        engine.evidence,
        run_id=run_id,
        actor=actor.id,
        strategy=strategy,
        actor_kind=actor.kind,
        allow_irreversible=payload.allow_irreversible,
    )
    return RecoveryOut.model_validate(attempt)
