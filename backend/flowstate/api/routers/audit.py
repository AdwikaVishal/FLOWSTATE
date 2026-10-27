"""Audit chain endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from flowstate.api.deps import ClockDep, UowDep
from flowstate.domain.schemas import AuditOverview, AuditRunStatus, ChainVerificationOut
from flowstate.errors import NotFoundError
from flowstate.services.audit import verify_chain

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditOverview)
def audit_overview(
    uow: UowDep,
    clock: ClockDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> AuditOverview:
    runs = uow.runs.list(order_by="created_at", descending=True, limit=limit)
    results: list[AuditRunStatus] = []
    for run in runs:
        events = list(uow.events.read(run.id))
        chain = verify_chain(events)
        workflow = uow.workflows.get(run.workflow_id)
        results.append(
            AuditRunStatus(
                run_id=run.id,
                workflow_name=workflow.name if workflow else "Unknown workflow",
                events=chain.event_count,
                valid=chain.valid,
                head_hash=chain.head_hash,
                broken_at_seq=chain.broken_at_seq,
            )
        )
    valid = sum(1 for item in results if item.valid)
    return AuditOverview(
        checked=len(results),
        valid=valid,
        broken=len(results) - valid,
        checked_at=clock.now(),
        runs=results,
    )


@router.get("/verify/{run_id}", response_model=ChainVerificationOut)
def verify_run(run_id: str, uow: UowDep) -> ChainVerificationOut:
    if uow.runs.get(run_id) is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    result = verify_chain(list(uow.events.read(run_id)))
    return ChainVerificationOut(
        run_id=run_id,
        valid=result.valid,
        event_count=result.event_count,
        head_hash=result.head_hash,
        broken_at_seq=result.broken_at_seq,
        reason=result.reason,
    )
