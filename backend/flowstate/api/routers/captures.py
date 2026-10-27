"""Workflow learning endpoints: record, review, validate, save and replay."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from flowstate.api.deps import (
    ActorDep,
    ClockDep,
    EngineDep,
    EvidenceDep,
    IdsDep,
    SettingsDep,
    UowDep,
)
from flowstate.api.serializers import (
    capture_summary,
    run_detail,
    run_summary,
    version_detail,
    workflow_detail,
)
from flowstate.domain.schemas import (
    CaptureActionPatch,
    CaptureCreate,
    CaptureEventBatch,
    CaptureReplayRequest,
    CaptureSessionOut,
    CaptureWorkflowCreate,
    CaptureWorkflowResponse,
    RunDetail,
    RunSummary,
)
from flowstate.repositories import UnitOfWork
from flowstate.services import learning

router = APIRouter(prefix="/captures", tags=["learning"])


def _actions(uow: UnitOfWork, session_id: str):
    rows = list(uow.captured_actions.list(session_id=session_id))
    rows.sort(key=lambda item: item.ordinal)
    return rows


@router.get("", response_model=list[CaptureSessionOut])
def list_captures(
    uow: UowDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CaptureSessionOut]:
    return [capture_summary(uow, item) for item in learning.list_sessions(uow, limit=limit)]


@router.post("", response_model=CaptureSessionOut, status_code=status.HTTP_201_CREATED)
def start_capture(
    payload: CaptureCreate,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> CaptureSessionOut:
    session = learning.create_session(
        uow,
        clock,
        ids,
        settings,
        label=payload.label,
        demo_url=payload.demo_url,
        actor=actor.id,
    )
    return capture_summary(uow, session)


@router.get("/{session_id}", response_model=CaptureSessionOut)
def get_capture(session_id: str, uow: UowDep) -> CaptureSessionOut:
    return capture_summary(uow, learning.get_session(uow, session_id))


@router.post("/{session_id}/pause", response_model=CaptureSessionOut)
def pause_capture(session_id: str, uow: UowDep) -> CaptureSessionOut:
    session = learning.get_session(uow, session_id)
    learning.pause_session(session)
    return capture_summary(uow, session)


@router.post("/{session_id}/resume", response_model=CaptureSessionOut)
def resume_capture(session_id: str, uow: UowDep) -> CaptureSessionOut:
    session = learning.get_session(uow, session_id)
    learning.resume_session(session)
    return capture_summary(uow, session)


@router.post("/{session_id}/stop", response_model=CaptureSessionOut)
def stop_capture(
    session_id: str,
    uow: UowDep,
    clock: ClockDep,
) -> CaptureSessionOut:
    session = learning.get_session(uow, session_id)
    learning.stop_session(uow, clock, session)
    return capture_summary(uow, session)


@router.post("/{session_id}/events", response_model=CaptureSessionOut)
def record_events(
    session_id: str,
    payload: CaptureEventBatch,
    uow: UowDep,
    settings: SettingsDep,
    ids: IdsDep,
) -> CaptureSessionOut:
    session = learning.get_session(uow, session_id)
    learning.add_events(uow, ids, session, payload.events, settings=settings)
    return capture_summary(uow, session)


@router.patch(
    "/{session_id}/actions/{action_id}",
    response_model=CaptureSessionOut,
)
def edit_action(
    session_id: str,
    action_id: str,
    payload: CaptureActionPatch,
    uow: UowDep,
    clock: ClockDep,
) -> CaptureSessionOut:
    session = learning.get_session(uow, session_id)
    action = learning.get_action(uow, session_id, action_id)
    learning.update_action(uow, clock, session, action, payload.model_dump(exclude_unset=True))
    return capture_summary(uow, session)


@router.post("/{session_id}/validate", response_model=CaptureSessionOut)
def validate_capture(
    session_id: str,
    request: Request,
    uow: UowDep,
    clock: ClockDep,
    evidence: EvidenceDep,
) -> CaptureSessionOut:
    session = learning.get_session(uow, session_id)
    learning.validate_session(
        uow,
        clock,
        session,
        _actions(uow, session.id),
        browser_factory=getattr(request.app.state, "browser_factory", None),
        evidence=evidence,
    )
    return capture_summary(uow, session)


@router.post(
    "/{session_id}/workflow",
    response_model=CaptureWorkflowResponse,
    status_code=status.HTTP_201_CREATED,
)
def save_capture_workflow(
    session_id: str,
    payload: CaptureWorkflowCreate,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    settings: SettingsDep,
) -> CaptureWorkflowResponse:
    session = learning.get_session(uow, session_id)
    workflow, version = learning.save_workflow(
        uow,
        clock,
        ids,
        session,
        _actions(uow, session.id),
        name=payload.name,
        description=payload.description,
        publish=payload.publish,
        browser_timeout_ms=settings.browser_timeout_ms,
    )
    return CaptureWorkflowResponse(
        session=capture_summary(uow, session),
        workflow=workflow_detail(uow, workflow),
        version=version_detail(uow, version),
    )


@router.post("/{session_id}/replay", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
def replay_capture(
    session_id: str,
    payload: CaptureReplayRequest,
    uow: UowDep,
    clock: ClockDep,
    engine: EngineDep,
    actor: ActorDep,
) -> RunDetail:
    session = learning.get_session(uow, session_id)
    run = learning.replay(
        uow,
        engine,
        clock,
        session,
        inputs=payload.inputs,
        variant=payload.variant,
        actor=actor.id,
    )
    return run_detail(uow, run)


@router.get("/{session_id}/runs", response_model=list[RunSummary])
def capture_runs(
    session_id: str,
    uow: UowDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunSummary]:
    learning.get_session(uow, session_id)
    rows = uow.runs.list(
        capture_session_id=session_id,
        order_by="created_at",
        descending=True,
        limit=limit,
    )
    return [run_summary(uow, item) for item in rows]
