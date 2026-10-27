"""Execution run endpoints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from flowstate.api.deps import (
    ActorDep,
    CancellationDep,
    ClockDep,
    EngineDep,
    IdempotencyDep,
    IdsDep,
    UowDep,
)
from flowstate.api.serializers import node_state_out, run_detail, run_summary
from flowstate.db.models import ExecutionEvent, ExecutionJob, ExecutionRun
from flowstate.domain.enums import (
    TERMINAL_RUN_STATUSES,
    EventType,
    Origin,
    RunStatus,
    RunTrigger,
    RunVerdict,
    VersionStatus,
)
from flowstate.domain.graph import GraphValidationError, build_graph, graph_summary
from flowstate.domain.schema_validation import validate_schema
from flowstate.domain.schemas import (
    ChainVerificationOut,
    EventOut,
    RunCreate,
    RunDetail,
    RunSummary,
    TimelineOut,
)
from flowstate.errors import ConflictError, NotFoundError, ValidationError
from flowstate.services.audit import AuditWriter, verify_chain
from flowstate.services.cancellation import cancel_run
from flowstate.services.evidence import EvidenceStore
from flowstate.services.execution import ExecuteOptions
from flowstate.services.idempotency import lookup as idempotency_lookup
from flowstate.services.idempotency import remember as idempotency_remember
from flowstate.services.idempotency import request_hash
from flowstate.services.redaction import redact
from flowstate.services.timeline import build_timeline

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[RunSummary])
def list_runs(
    uow: UowDep,
    workflow_id: Annotated[str | None, Query()] = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    origin: Annotated[Origin | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RunSummary]:
    filters: dict[str, object] = {}
    if workflow_id:
        filters["workflow_id"] = workflow_id
    if run_status:
        filters["status"] = run_status.value
    if origin:
        filters["origin"] = origin.value
    rows = uow.runs.list(
        order_by="created_at", descending=True, limit=limit, offset=offset, **filters
    )
    return [run_summary(uow, item) for item in rows]


@router.post("", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: RunCreate,
    request: Request,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    engine: EngineDep,
    actor: ActorDep,
    idempotency_key: IdempotencyDep,
) -> RunDetail:
    scope = "POST /runs"
    fingerprint = request_hash(scope, actor.id, payload.model_dump(mode="json"))
    if idempotency_key:
        cached = idempotency_lookup(
            uow, scope=scope, key=idempotency_key, fingerprint=fingerprint, now=clock.now()
        )
        if cached is not None:
            if payload.background:
                cached_run = uow.runs.get(str(cached["id"]))
                if cached_run is not None:
                    return run_detail(uow, cached_run)
            return RunDetail.model_validate(cached)

    workflow = uow.workflows.get(payload.workflow_id)
    if workflow is None:
        raise NotFoundError(
            f"Workflow {payload.workflow_id} was not found.", code="WORKFLOW_NOT_FOUND"
        )
    version_id = payload.version_id or workflow.current_version_id
    if not version_id:
        raise ConflictError("The workflow has no published version.", code="NO_PUBLISHED_VERSION")
    version = uow.versions.get(version_id)
    if version is None:
        raise NotFoundError(f"Version {version_id} was not found.", code="VERSION_NOT_FOUND")
    if version.status != VersionStatus.PUBLISHED:
        raise ConflictError(
            f"Only a PUBLISHED version can execute (this one is {version.status}).",
            code="VERSION_NOT_PUBLISHED",
        )

    actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version.id))
    capture = uow.captures.first(workflow_version_id=version.id)
    try:
        graph = build_graph(actions)
    except GraphValidationError as exc:
        raise ConflictError(
            f"This version's graph is invalid: {exc.message}",
            code=exc.code,
            details=exc.details or None,
        ) from exc
    if payload.background and redact(payload.inputs, version.redact_paths or []) == payload.inputs:
        workers = getattr(request.app.state, "run_workers", None)
        if workers is None:
            raise RuntimeError("The background execution service is unavailable.")
        input_errors = validate_schema(payload.inputs, version.input_schema or {})
        if input_errors:
            raise ValidationError(
                "The run inputs do not satisfy the workflow input schema.",
                code="INPUT_SCHEMA_VALIDATION_FAILED",
                details={"errors": input_errors},
            )
        if not workers.reserve():
            raise ConflictError(
                "The background execution queue is full. Try again after a run completes.",
                code="RUN_QUEUE_FULL",
            )
        try:
            engine._validate_handlers(graph)
            channels = engine._validate_channels(graph)
            run_id = ids.new()
            now = clock.now()
            run = ExecutionRun(
                id=run_id,
                workflow_id=workflow.id,
                workflow_version_id=version.id,
                status=str(RunStatus.PENDING),
                verdict=str(RunVerdict.PENDING),
                trigger=str(RunTrigger.MANUAL),
                origin=str(Origin.LIVE),
                driver="playwright-v1" if "BROWSER" in channels else "simulated-v1",
                seed=payload.seed,
                current_ordinal=0,
                actor=actor.id,
                capture_session_id=capture.id if capture else None,
                inputs=redact(payload.inputs, version.redact_paths or []),
                outputs={},
                created_at=now,
            )
            uow.runs.add(run)
            engine._ensure_node_states(run, graph)
            AuditWriter(uow, clock, ids).append(
                run.id,
                EventType.RUN_CREATED,
                actor=actor.kind,
                actor_id=actor.id,
                payload={
                    "workflow_id": workflow.id,
                    "workflow_slug": workflow.slug,
                    "workflow_name": workflow.name,
                    "version_id": version.id,
                    "version": version.version,
                    "trigger": str(RunTrigger.MANUAL),
                    "origin": str(Origin.LIVE),
                    "driver": run.driver,
                    "channels": sorted(channels),
                    "capture_session_id": capture.id if capture else None,
                    "seed": payload.seed,
                    "scenario_id": None,
                    "graph": graph_summary(graph),
                    "queued": True,
                },
            )
            job = ExecutionJob(
                run_id=run.id,
                status="QUEUED",
                payload={
                    "trigger": str(RunTrigger.MANUAL),
                    "actor_kind": str(actor.kind),
                    "actor_role": str(actor.role),
                    "inputs": payload.inputs,
                },
                created_at=now,
            )
            uow.execution_jobs.add(job)
            detail = run_detail(uow, run)
            if idempotency_key:
                idempotency_remember(
                    uow,
                    ids,
                    clock,
                    scope=scope,
                    key=idempotency_key,
                    fingerprint=fingerprint,
                    response=detail.model_dump(mode="json"),
                    status_code=201,
                )
            uow.commit()
        except Exception:
            workers.release_reservation()
            raise
        workers.submit_reserved(run.id)
        return detail

    run = engine.start_run(
        workflow=workflow,
        version=version,
        actions=actions,
        invariants=invariants,
        options=ExecuteOptions(
            trigger=RunTrigger.MANUAL,
            origin=Origin.LIVE,
            seed=payload.seed,
            actor=actor.id,
            actor_kind=actor.kind,
            capture_session_id=capture.id if capture else None,
            inputs=payload.inputs,
        ),
    )
    detail = run_detail(uow, run)
    if idempotency_key:
        idempotency_remember(
            uow,
            ids,
            clock,
            scope=scope,
            key=idempotency_key,
            fingerprint=fingerprint,
            response=detail.model_dump(mode="json"),
            status_code=201,
        )
    return detail


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, uow: UowDep) -> RunDetail:
    run = uow.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    return run_detail(uow, run)


@router.get("/{run_id}/events", response_model=list[EventOut])
def get_run_events(
    run_id: str, uow: UowDep, limit: Annotated[int, Query(ge=1, le=2000)] = 1000
) -> list[EventOut]:
    if uow.runs.get(run_id) is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    return [EventOut.model_validate(item) for item in uow.events.read(run_id, limit=limit)]


@router.get("/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    request: Request,
    uow: UowDep,
    actor: ActorDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: Annotated[int, Query(ge=-1)] = -1,
    once: Annotated[bool, Query()] = False,
) -> StreamingResponse:
    run = uow.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    try:
        cursor = max(after, int(last_event_id)) if last_event_id is not None else after
    except ValueError as exc:
        raise ConflictError(
            "Last-Event-ID must be the integer sequence of a run event.",
            code="INVALID_EVENT_CURSOR",
        ) from exc
    workflow_id = run.workflow_id

    async def stream():
        nonlocal cursor
        last_heartbeat = asyncio.get_running_loop().time()
        yield ": connected\n\n"
        while not await request.is_disconnected():
            session: Session = request.app.state.db.session_factory()
            try:
                current = session.get(ExecutionRun, run_id)
                if current is None or current.workflow_id != workflow_id:
                    return
                rows = session.scalars(
                    select(ExecutionEvent)
                    .where(ExecutionEvent.run_id == run_id, ExecutionEvent.seq > cursor)
                    .order_by(ExecutionEvent.seq)
                    .limit(251)
                ).all()
                has_more = len(rows) > 250
                envelopes = [
                    {
                        "event_id": event.id,
                        "event_type": event.type,
                        "execution_id": event.run_id,
                        "workflow_id": workflow_id,
                        "timestamp": event.recorded_at.isoformat(),
                        "sequence": event.seq,
                        "payload": event.payload or {},
                        "schema_version": 1,
                    }
                    for event in rows[:250]
                ]
                current_status = current.status
            finally:
                session.close()

            for envelope in envelopes:
                cursor = int(envelope["sequence"])
                yield (
                    f"id: {cursor}\nevent: run-event\ndata: "
                    f"{json.dumps(envelope, separators=(',', ':'), default=str)}\n\n"
                )
            if has_more:
                continue
            if once and current_status not in {RunStatus.PENDING, RunStatus.RUNNING}:
                yield "event: batch-end\ndata: {}\n\n"
                return
            if current_status in TERMINAL_RUN_STATUSES:
                yield f"event: complete\ndata: {json.dumps({'status': str(current_status)})}\n\n"
                return
            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= 15:
                yield ": keep-alive\n\n"
                last_heartbeat = now
            await asyncio.sleep(0.35)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{run_id}/nodes", response_model=list)
def get_run_nodes(run_id: str, uow: UowDep) -> list:
    run = uow.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    actions = list(
        uow.actions.list(workflow_version_id=run.workflow_version_id, order_by="ordinal")
    )
    return node_state_out(uow, run_id, actions)


@router.get("/{run_id}/timeline", response_model=TimelineOut)
def get_run_timeline(run_id: str, uow: UowDep) -> TimelineOut:
    run = uow.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    actions = list(
        uow.actions.list(workflow_version_id=run.workflow_version_id, order_by="ordinal")
    )
    states = node_state_out(uow, run_id, actions)
    payload = build_timeline(
        uow,
        run_id=run_id,
        node_states=[item.model_dump(mode="json") for item in states],
    )
    return TimelineOut.model_validate(payload)


@router.post("/{run_id}/cancel", response_model=RunDetail)
def cancel_execution(
    run_id: str,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    cancellations: CancellationDep,
    actor: ActorDep,
    reason: Annotated[str, Query(max_length=500)] = "",
) -> RunDetail:
    run = cancel_run(
        uow,
        clock,
        ids,
        run_id=run_id,
        actor=actor.id,
        reason=reason,
        registry=cancellations,
    )
    return run_detail(uow, run)


@router.get("/{run_id}/evidence/{evidence_id}")
def download_evidence(run_id: str, evidence_id: str, uow: UowDep) -> FileResponse:
    if uow.runs.get(run_id) is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    record = uow.evidence.get(evidence_id)
    if record is None or record.run_id != run_id:
        raise NotFoundError("Evidence was not found for this run.", code="EVIDENCE_NOT_FOUND")
    if not EvidenceStore.verify(record):
        raise ConflictError(
            "The evidence file is missing or its hash does not match.", code="EVIDENCE_TAMPERED"
        )
    path = Path(record.uri)
    return FileResponse(
        path,
        media_type=record.mime_type,
        filename=path.name,
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.get("/{run_id}/events/verify", response_model=ChainVerificationOut)
def verify_run_events(run_id: str, uow: UowDep) -> ChainVerificationOut:
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
