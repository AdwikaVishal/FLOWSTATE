"""Decision Inbox and blocker endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from flowstate.api.deps import (
    ActorDep,
    ClockDep,
    EngineDep,
    HumanDep,
    IdsDep,
    SettingsDep,
    UowDep,
)
from flowstate.api.serializers import blocker_out, decision_out
from flowstate.domain.enums import BlockerKind, BlockerStatus, DecisionStatus, Origin
from flowstate.domain.schemas import (
    BlockerOut,
    DecisionApprove,
    DecisionDefer,
    DecisionDeny,
    DecisionHistoryOut,
    DecisionOut,
    DecisionProvideInfo,
    DecisionReject,
)
from flowstate.errors import NotFoundError
from flowstate.services import decisions as service

router = APIRouter(prefix="/decisions", tags=["decisions"])
blocker_router = APIRouter(prefix="/blockers", tags=["blockers"])
run_blocker_router = APIRouter(prefix="/runs", tags=["blockers"])


@router.get("", response_model=list[DecisionOut])
def list_decisions(
    uow: UowDep,
    actor: ActorDep,
    decision_status: Annotated[
        DecisionStatus | None,
        Query(alias="status", description="Filter by decision status"),
    ] = None,
    kind: Annotated[BlockerKind | None, Query(description="Filter by blocker cause")] = None,
    origin: Annotated[Origin | None, Query()] = None,
    run_id: Annotated[str | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DecisionOut]:
    filters: dict[str, object] = {}
    if decision_status:
        filters["status"] = decision_status.value
    if kind:
        filters["kind"] = kind.value
    if origin:
        filters["origin"] = origin.value
    if run_id:
        filters["run_id"] = run_id

    rows = list(
        uow.decisions.list(
            order_by="requested_at",
            descending=True,
            limit=limit,
            offset=offset,
            **filters,  # type: ignore[arg-type]
        )
    )
    if open_only:
        rows = [item for item in rows if item.status in service.OPEN_STATUSES]
    return [decision_out(uow, item, actor_role=actor.role) for item in rows]


@router.get("/{decision_id}", response_model=DecisionOut)
def get_decision(decision_id: str, uow: UowDep, actor: ActorDep) -> DecisionOut:
    return decision_out(uow, service.get_decision(uow, decision_id), actor_role=actor.role)


@router.get("/{decision_id}/history", response_model=list[DecisionHistoryOut])
def get_history(decision_id: str, uow: UowDep, actor: ActorDep) -> list[DecisionHistoryOut]:
    service.get_decision(uow, decision_id)
    return [
        DecisionHistoryOut.model_validate(item) for item in service.history_rows(uow, decision_id)
    ]


@router.post("/{decision_id}/approve", response_model=DecisionOut)
def approve_decision(
    decision_id: str,
    payload: DecisionApprove,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    settings: SettingsDep,
    engine: EngineDep,
    actor: HumanDep,
) -> DecisionOut:
    decision = service.approve(
        uow,
        clock,
        ids,
        settings,
        engine,
        decision_id=decision_id,
        actor_id=actor.id,
        actor_role=actor.role,
        actor_kind=actor.kind,
        note=payload.note,
    )
    return decision_out(uow, decision, actor_role=actor.role)


@router.post("/{decision_id}/reject", response_model=DecisionOut)
def reject_decision(
    decision_id: str,
    payload: DecisionReject,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    actor: HumanDep,
) -> DecisionOut:
    decision = service.reject(
        uow,
        clock,
        ids,
        decision_id=decision_id,
        actor_id=actor.id,
        actor_role=actor.role,
        reason=payload.reason or None,
        actor_kind=actor.kind,
    )
    return decision_out(uow, decision, actor_role=actor.role)


@router.post("/{decision_id}/deny", response_model=DecisionOut, include_in_schema=False)
def deny_decision(
    decision_id: str,
    payload: DecisionDeny,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    actor: HumanDep,
) -> DecisionOut:
    decision = service.reject(
        uow,
        clock,
        ids,
        decision_id=decision_id,
        actor_id=actor.id,
        actor_role=actor.role,
        reason=payload.reason or None,
        actor_kind=actor.kind,
    )
    return decision_out(uow, decision, actor_role=actor.role)


@router.post("/{decision_id}/provide-info", response_model=DecisionOut)
def provide_info(
    decision_id: str,
    payload: DecisionProvideInfo,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    engine: EngineDep,
    actor: HumanDep,
) -> DecisionOut:
    decision = service.provide_info(
        uow,
        clock,
        ids,
        engine,
        decision_id=decision_id,
        actor_id=actor.id,
        values=payload.values,
        actor_role=actor.role,
        note=payload.note,
        actor_kind=actor.kind,
    )
    return decision_out(uow, decision, actor_role=actor.role)


@router.post("/{decision_id}/defer", response_model=DecisionOut)
def defer_decision(
    decision_id: str,
    payload: DecisionDefer,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    actor: HumanDep,
) -> DecisionOut:
    decision = service.defer(
        uow,
        clock,
        ids,
        decision_id=decision_id,
        actor_id=actor.id,
        reason=payload.reason,
        until=payload.until,
        actor_role=actor.role,
        actor_kind=actor.kind,
    )
    return decision_out(uow, decision, actor_role=actor.role)


@blocker_router.get("", response_model=list[BlockerOut])
def list_blockers(
    uow: UowDep,
    blocker_status: Annotated[BlockerStatus | None, Query(alias="status")] = None,
    kind: Annotated[BlockerKind | None, Query()] = None,
    run_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[BlockerOut]:
    filters: dict[str, Any] = {}
    if blocker_status:
        filters["status"] = blocker_status.value
    if kind:
        filters["kind"] = kind.value
    if run_id:
        filters["run_id"] = run_id
    rows = uow.blockers.list(order_by="created_at", descending=True, limit=limit, **filters)
    return [blocker_out(uow, item) for item in rows]


@run_blocker_router.get("/{run_id}/blockers", response_model=list[BlockerOut])
def run_blockers(run_id: str, uow: UowDep) -> list[BlockerOut]:
    if uow.runs.get(run_id) is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    rows = uow.blockers.list(run_id=run_id)
    rows.sort(key=lambda item: item.created_at)
    return [blocker_out(uow, item) for item in rows]
