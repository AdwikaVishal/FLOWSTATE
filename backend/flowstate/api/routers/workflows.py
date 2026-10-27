"""Workflow and version endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from flowstate.api.deps import ClockDep, IdsDep, UowDep
from flowstate.api.serializers import (
    version_detail,
    version_summary,
    workflow_detail,
    workflow_summary,
)
from flowstate.domain.enums import Origin
from flowstate.domain.schemas import (
    VersionCreate,
    VersionDetail,
    VersionSummary,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowSummary,
)
from flowstate.errors import NotFoundError
from flowstate.services import workflows as service

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_model=list[WorkflowSummary])
def list_workflows(
    uow: UowDep,
    origin: Annotated[Origin | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[WorkflowSummary]:
    filters: dict[str, object] = {}
    if origin is not None:
        filters["origin"] = origin.value
    rows = uow.workflows.list(
        order_by="created_at", descending=True, limit=limit, offset=offset, **filters
    )
    return [workflow_summary(uow, item) for item in rows]


@router.post("", response_model=WorkflowDetail, status_code=status.HTTP_201_CREATED)
def create_workflow(
    payload: WorkflowCreate, uow: UowDep, clock: ClockDep, ids: IdsDep
) -> WorkflowDetail:
    publish = payload.publish and bool(payload.actions)
    workflow, _version = service.create_workflow(
        uow,
        ids,
        clock,
        name=payload.name,
        description=payload.description,
        owner="local-operator",
        origin=Origin.LIVE,
        actions=[item.model_dump(mode="json") for item in payload.actions],
        invariants=[item.model_dump(mode="json") for item in payload.invariants],
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        redact_paths=payload.redact_paths,
        publish=publish,
    )
    return workflow_detail(uow, workflow)


@router.get("/{workflow_id}", response_model=WorkflowDetail)
def get_workflow(workflow_id: str, uow: UowDep) -> WorkflowDetail:
    return workflow_detail(uow, service.get_workflow(uow, workflow_id))


@router.get("/{workflow_id}/versions", response_model=list[VersionSummary])
def list_versions(workflow_id: str, uow: UowDep) -> list[VersionSummary]:
    service.get_workflow(uow, workflow_id)
    rows = sorted(
        uow.versions.list(workflow_id=workflow_id),
        key=lambda item: item.version,
        reverse=True,
    )
    return [version_summary(uow, item) for item in rows]


@router.post(
    "/{workflow_id}/versions",
    response_model=VersionDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    workflow_id: str, payload: VersionCreate, uow: UowDep, clock: ClockDep, ids: IdsDep
) -> VersionDetail:
    workflow = service.get_workflow(uow, workflow_id)
    version = service.add_version(
        uow,
        ids,
        clock,
        workflow=workflow,
        actions=[item.model_dump(mode="json") for item in payload.actions],
        invariants=[item.model_dump(mode="json") for item in payload.invariants],
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        redact_paths=payload.redact_paths,
    )
    if payload.publish:
        service.publish_version(uow, clock, version)
        workflow.current_version_id = version.id
    return version_detail(uow, version)


@router.get("/{workflow_id}/versions/{version_id}", response_model=VersionDetail)
def get_version(workflow_id: str, version_id: str, uow: UowDep) -> VersionDetail:
    service.get_workflow(uow, workflow_id)
    version = service.get_version(uow, version_id)
    if version.workflow_id != workflow_id:
        raise NotFoundError(
            "That version does not belong to this workflow.", code="VERSION_NOT_FOUND"
        )
    return version_detail(uow, version)


@router.post("/{workflow_id}/versions/{version_id}/publish", response_model=VersionDetail)
def publish_version(
    workflow_id: str, version_id: str, uow: UowDep, clock: ClockDep
) -> VersionDetail:
    workflow = service.get_workflow(uow, workflow_id)
    version = service.get_version(uow, version_id)
    if version.workflow_id != workflow_id:
        raise NotFoundError(
            "That version does not belong to this workflow.", code="VERSION_NOT_FOUND"
        )
    service.publish_version(uow, clock, version)
    workflow.current_version_id = version.id
    return version_detail(uow, version)
