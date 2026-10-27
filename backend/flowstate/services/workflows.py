"""Workflow and version lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from flowstate.clock import Clock
from flowstate.db.models import ActionDefinition, Invariant, Workflow, WorkflowVersion
from flowstate.domain.enums import Origin, VersionStatus
from flowstate.domain.graph import GraphValidationError, build_graph
from flowstate.domain.policy import DEFAULT_POLICY
from flowstate.errors import ConflictError, NotFoundError, ValidationError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services.audit import canonical_json


def content_hash(actions: Sequence[ActionDefinition], invariants: Sequence[Invariant]) -> str:
    material = {
        "actions": [
            {
                "ordinal": item.ordinal,
                "node_key": item.node_key,
                "node_type": item.node_type,
                "kind": item.kind,
                "label": item.label,
                "target": item.target,
                "input": item.input,
                "preconditions": item.preconditions,
                "postconditions": item.postconditions,
                "depends_on": item.depends_on,
                "channel": item.channel,
                "side_effect_class": item.side_effect_class,
                "requires_authorization": item.requires_authorization,
                "retry_policy": item.retry_policy,
                "failure_handler": item.failure_handler,
                "input_schema": item.input_schema,
                "output_schema": item.output_schema,
                "redact_paths": item.redact_paths,
            }
            for item in sorted(actions, key=lambda row: row.ordinal)
        ],
        "invariants": [
            {
                "name": item.name,
                "kind": item.kind,
                "expression": item.expression,
                "severity": item.severity,
            }
            for item in invariants
        ],
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def validate_actions(actions: Sequence[ActionDefinition]) -> None:
    """Validate policy and graph structure. Raises ValidationError on failure."""
    if not actions:
        raise ValidationError("A version must contain at least one node.", code="EMPTY_VERSION")
    for action in actions:
        result = DEFAULT_POLICY.may_execute(action)
        if result.decision == "DENY":
            raise ValidationError(
                f"Node {action.ordinal} ({action.label}) is blocked by policy: {result.detail}",
                code=result.code,
                details={"ordinal": action.ordinal},
            )
    try:
        build_graph(actions)
    except GraphValidationError as exc:
        raise ValidationError(exc.message, code=exc.code, details=exc.details or None) from exc


def slugify(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    slug = "-".join(part for part in cleaned.split("-") if part)
    return slug[:100] or "workflow"


def create_workflow(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    *,
    name: str,
    description: str,
    owner: str,
    origin: Origin = Origin.LIVE,
    seed_batch: str | None = None,
    actions: Sequence[dict[str, Any]] | None = None,
    invariants: Sequence[dict[str, Any]] | None = None,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    redact_paths: Sequence[str] | None = None,
    publish: bool = True,
) -> tuple[Workflow, WorkflowVersion]:
    slug = slugify(name)
    if uow.workflows.first(slug=slug) is not None:
        raise ConflictError(f"A workflow with slug {slug!r} already exists.", code="SLUG_TAKEN")

    now = clock.now()
    workflow = Workflow(
        id=ids.new(),
        slug=slug,
        name=name,
        description=description,
        owner=owner,
        origin=str(origin),
        seed_batch=seed_batch,
        created_at=now,
    )
    uow.workflows.add(workflow)

    version = WorkflowVersion(
        id=ids.new(),
        workflow_id=workflow.id,
        version=1,
        status=str(VersionStatus.DRAFT),
        label=f"{name} v1",
        origin=str(origin),
        input_schema=dict(input_schema or {}),
        output_schema=dict(output_schema or {}),
        redact_paths=list(redact_paths or []),
        created_at=now,
    )
    uow.versions.add(version)

    action_rows = _add_actions(uow, ids, version.id, actions or [])
    invariant_rows = _add_invariants(uow, ids, version.id, invariants or [])
    version.content_hash = content_hash(action_rows, invariant_rows)

    if publish:
        publish_version(uow, clock, version, action_rows, invariant_rows)
        workflow.current_version_id = version.id

    uow.flush()
    return workflow, version


def add_version(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    *,
    workflow: Workflow,
    actions: Sequence[dict[str, Any]],
    invariants: Sequence[dict[str, Any]] | None = None,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    redact_paths: Sequence[str] | None = None,
) -> WorkflowVersion:
    existing = uow.versions.list(workflow_id=workflow.id)
    next_number = max((item.version for item in existing), default=0) + 1
    parent = max(existing, key=lambda item: item.version) if existing else None
    version = WorkflowVersion(
        id=ids.new(),
        workflow_id=workflow.id,
        version=next_number,
        status=str(VersionStatus.DRAFT),
        label=f"{workflow.name} v{next_number}",
        origin=workflow.origin,
        parent_version_id=parent.id if parent else None,
        input_schema=dict(input_schema or {}),
        output_schema=dict(output_schema or {}),
        redact_paths=list(redact_paths or []),
        created_at=clock.now(),
    )
    uow.versions.add(version)
    action_rows = _add_actions(uow, ids, version.id, actions)
    invariant_rows = _add_invariants(uow, ids, version.id, invariants or [])
    version.content_hash = content_hash(action_rows, invariant_rows)
    uow.flush()
    return version


def publish_version(
    uow: UnitOfWork,
    clock: Clock,
    version: WorkflowVersion,
    actions: Sequence[ActionDefinition] | None = None,
    invariants: Sequence[Invariant] | None = None,
) -> WorkflowVersion:
    if version.status != VersionStatus.DRAFT:
        raise ConflictError(
            f"Only a DRAFT version can be published (version is {version.status}).",
            code="VERSION_NOT_DRAFT",
        )
    action_rows = list(actions or uow.actions.list(workflow_version_id=version.id))
    invariant_rows = list(invariants or uow.invariants.list(workflow_version_id=version.id))
    validate_actions(action_rows)

    version.content_hash = content_hash(action_rows, invariant_rows)
    version.status = str(VersionStatus.PUBLISHED)
    version.published_at = clock.now()

    workflow = uow.workflows.get(version.workflow_id)
    if workflow is not None:
        workflow.current_version_id = version.id
    uow.flush()
    return version


def _add_actions(
    uow: UnitOfWork, ids: IdFactory, version_id: str, rows: Sequence[dict[str, Any]]
) -> list[ActionDefinition]:
    created: list[ActionDefinition] = []
    for index, row in enumerate(rows):
        ordinal = int(row.get("ordinal", index))
        retry_policy = dict(row.get("retry_policy") or {})
        failure_handler = dict(row.get("failure_handler") or {})
        action = ActionDefinition(
            id=ids.new(),
            workflow_version_id=version_id,
            ordinal=ordinal,
            kind=str(row.get("kind", "CLICK")),
            label=str(row.get("label", f"Node {index + 1}")),
            target=dict(row.get("target") or {}),
            input=dict(row.get("input") or {}),
            preconditions=list(row.get("preconditions") or []),
            postconditions=list(row.get("postconditions") or []),
            side_effect_class=str(row.get("side_effect_class", "READ_ONLY")),
            requires_authorization=bool(row.get("requires_authorization", False)),
            timeout_ms=int(row.get("timeout_ms", 5000)),
            node_key=str(row.get("node_key") or f"n{ordinal}"),
            node_type=str(row.get("node_type", "ACTION")),
            depends_on=[str(item) for item in (row.get("depends_on") or [])],
            channel=str(row.get("channel", "SIMULATED")),
            retry_policy=retry_policy,
            failure_handler=failure_handler,
            input_schema=dict(row.get("input_schema") or {}),
            output_schema=dict(row.get("output_schema") or {}),
            redact_paths=[str(item) for item in (row.get("redact_paths") or [])],
        )
        uow.actions.add(action)
        created.append(action)
    return created


def _add_invariants(
    uow: UnitOfWork, ids: IdFactory, version_id: str, rows: Sequence[dict[str, Any]]
) -> list[Invariant]:
    created: list[Invariant] = []
    for row in rows:
        invariant = Invariant(
            id=ids.new(),
            workflow_version_id=version_id,
            name=str(row.get("name", "Invariant")),
            description=str(row.get("description", "")),
            kind=str(row.get("kind", "TERMINAL_STATE")),
            expression=dict(row.get("expression") or {}),
            severity=str(row.get("severity", "MAJOR")),
        )
        uow.invariants.add(invariant)
        created.append(invariant)
    return created


def get_workflow(uow: UnitOfWork, workflow_id: str) -> Workflow:
    workflow = uow.workflows.get(workflow_id)
    if workflow is None:
        raise NotFoundError(f"Workflow {workflow_id} was not found.", code="WORKFLOW_NOT_FOUND")
    return workflow


def get_version(uow: UnitOfWork, version_id: str) -> WorkflowVersion:
    version = uow.versions.get(version_id)
    if version is None:
        raise NotFoundError(f"Version {version_id} was not found.", code="VERSION_NOT_FOUND")
    return version
