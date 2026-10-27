"""Workflow learning: controlled capture, review, generation and replay.

The learning module never invents intent. It stores what the user did, ranks
locators, flags actions it cannot interpret confidently, and refuses to publish
while an ambiguity or unresolved selector remains. Replay is a normal engine run
through the ``playwright-v1`` driver, so it inherits postconditions, timeouts,
the audit chain and the ``UNKNOWN`` reconciliation rule.
"""

from __future__ import annotations

import contextlib
import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

from flowstate.capture.browser import BrowserError, BrowserSession
from flowstate.capture.recording import (
    RawCaptureEvent,
    action_from_event,
    build_input_schema,
    suggested_inputs,
    workflow_invariants,
    workflow_nodes,
)
from flowstate.clock import Clock
from flowstate.config import Settings
from flowstate.db.models import ActionDefinition, CapturedAction, CaptureSession, Workflow
from flowstate.domain.enums import (
    ActorKind,
    CaptureStatus,
    EvidenceKind,
    Origin,
    RunTrigger,
    VersionStatus,
)
from flowstate.domain.policy import DEFAULT_POLICY
from flowstate.domain.schema_validation import validate_schema
from flowstate.errors import ConflictError, NotFoundError, ValidationError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services import workflows as workflow_service
from flowstate.services.evidence import EvidenceStore
from flowstate.services.execution import ExecuteOptions, ExecutionEngine

_MUTABLE_STATUSES = frozenset({CaptureStatus.REVIEW, CaptureStatus.VALIDATED, CaptureStatus.SAVED})
_EVENT_STATUSES = frozenset({CaptureStatus.RECORDING})


@dataclass
class ActionView:
    """A detached action row safe to hand to the engine (no ORM mutation)."""

    ordinal: int
    node_key: str
    node_type: str
    kind: str
    label: str
    target: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    postconditions: list[dict[str, Any]] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    channel: str = "SIMULATED"
    side_effect_class: str = "READ_ONLY"
    requires_authorization: bool = False
    timeout_ms: int = 5000
    retry_policy: dict[str, Any] = field(default_factory=dict)
    failure_handler: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    redact_paths: list[str] = field(default_factory=list)


def _sanitize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drop query strings so a capture URL can never carry a token."""
    clean = copy.deepcopy(snapshot or {})
    raw = str(clean.get("url") or "")
    if raw:
        parsed = urlparse(raw)
        clean["url"] = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return clean


def _action_dict(action: CapturedAction) -> dict[str, Any]:
    return {
        "ordinal": action.ordinal,
        "kind": action.kind,
        "label": action.label,
        "selector": list(action.selector or []),
        "accessible_name": action.accessible_name,
        "input_param": action.input_param,
        "input_value": action.input_value,
        "page_before": dict(action.page_before or {}),
        "page_after": dict(action.page_after or {}),
        "expected_result": action.expected_result,
        "observed_result": action.observed_result,
        "preconditions": list(action.preconditions or []),
        "postconditions": list(action.postconditions or []),
        "failure_conditions": list(action.failure_conditions or []),
        "redacted_fields": list(action.redacted_fields or []),
        "ambiguous": action.ambiguous,
        "ambiguity_reason": action.ambiguity_reason,
        "resolution": dict(action.resolution or {}),
    }


def _resolve_demo_url(settings: Settings, requested: str | None) -> str:
    url = (requested or settings.demo_app_url).rstrip("/") + "/"
    parsed = urlparse(url)
    if "/demo-app" not in parsed.path:
        raise ValidationError(
            "Only the controlled /demo-app site can be recorded.",
            code="CAPTURE_ORIGIN_NOT_ALLOWED",
        )
    decision = DEFAULT_POLICY.resolve_origin(url)
    if decision.decision != "ALLOW":
        raise ValidationError(decision.detail, code=decision.code)
    return url


def get_session(uow: UnitOfWork, session_id: str) -> CaptureSession:
    session = uow.captures.get(session_id)
    if session is None:
        raise NotFoundError(
            f"Capture session {session_id} was not found.", code="CAPTURE_NOT_FOUND"
        )
    return session


def get_action(uow: UnitOfWork, session_id: str, action_id: str) -> CapturedAction:
    action = uow.captured_actions.get(action_id)
    if action is None or action.session_id != session_id:
        raise NotFoundError(
            f"Captured action {action_id} was not found in this session.",
            code="CAPTURED_ACTION_NOT_FOUND",
        )
    return action


def list_sessions(uow: UnitOfWork, *, limit: int = 50) -> Sequence[CaptureSession]:
    return uow.captures.list(order_by="created_at", descending=True, limit=limit)


def create_session(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    settings: Settings,
    *,
    label: str,
    demo_url: str | None,
    actor: str,
) -> CaptureSession:
    if not settings.capture_enabled:
        raise ConflictError("Workflow capture is disabled.", code="CAPTURE_DISABLED")
    url = _resolve_demo_url(settings, demo_url)
    now = clock.now()
    session = CaptureSession(
        id=ids.new(),
        label=label or "Invoice lookup capture",
        status=str(CaptureStatus.RECORDING),
        demo_url=url,
        variant="default",
        origin=str(Origin.LIVE),
        created_by=actor,
        event_count=0,
        validation={},
        created_at=now,
        started_at=now,
    )
    uow.captures.add(session)
    return session


def _require_status(session: CaptureSession, allowed: frozenset[CaptureStatus], code: str) -> None:
    if session.status not in allowed:
        raise ConflictError(
            f"This capture is {session.status}; the operation needs "
            + " or ".join(item.value for item in allowed)
            + ".",
            code=code,
        )


def pause_session(session: CaptureSession) -> CaptureSession:
    _require_status(session, _EVENT_STATUSES, "CAPTURE_NOT_RECORDING")
    session.status = str(CaptureStatus.PAUSED)
    return session


def resume_session(session: CaptureSession) -> CaptureSession:
    if session.status != CaptureStatus.PAUSED:
        raise ConflictError("Only a paused capture can resume.", code="CAPTURE_NOT_PAUSED")
    session.status = str(CaptureStatus.RECORDING)
    return session


def stop_session(uow: UnitOfWork, clock: Clock, session: CaptureSession) -> CaptureSession:
    if session.status not in {CaptureStatus.RECORDING, CaptureStatus.PAUSED}:
        raise ConflictError(
            "This capture has already been stopped.", code="CAPTURE_ALREADY_STOPPED"
        )
    if session.event_count == 0:
        raise ConflictError(
            "No interactions were recorded. Record at least one action before stopping.",
            code="CAPTURE_EMPTY",
        )
    session.status = str(CaptureStatus.REVIEW)
    session.ended_at = clock.now()
    return session


def add_events(
    uow: UnitOfWork,
    ids: IdFactory,
    session: CaptureSession,
    payloads: list[dict[str, Any]],
    *,
    settings: Settings,
) -> list[CapturedAction]:
    _require_status(session, _EVENT_STATUSES, "CAPTURE_NOT_RECORDING")
    if not payloads:
        return []
    if session.event_count + len(payloads) > settings.capture_max_events:
        raise ValidationError(
            f"A capture is limited to {settings.capture_max_events} actions.",
            code="CAPTURE_EVENT_LIMIT",
            details={"limit": settings.capture_max_events},
        )

    events: list[RawCaptureEvent] = []
    for index, payload in enumerate(payloads):
        try:
            events.append(RawCaptureEvent.model_validate(payload))
        except Exception as exc:
            raise ValidationError(
                f"Capture event {index} is not valid: {exc}",
                code="CAPTURE_EVENT_INVALID",
                details={"index": index},
            ) from exc
    events.sort(key=lambda item: (item.client_sequence, item.timestamp_offset_ms))

    existing = uow.captured_actions.list(session_id=session.id)
    next_ordinal = max((item.ordinal for item in existing), default=-1) + 1
    created: list[CapturedAction] = []
    for offset, event in enumerate(events):
        data = action_from_event(
            event,
            ordinal=next_ordinal + offset,
            demo_origin=session.demo_url.split("/demo-app/")[0],
        )
        action = CapturedAction(
            id=ids.new(),
            session_id=session.id,
            ordinal=int(data["ordinal"]),
            kind=str(data["kind"]),
            label=str(data["label"]),
            selector=list(data["selector"]),
            accessible_name=str(data["accessible_name"]),
            input_value=data["input_value"],
            input_param=data["input_param"],
            page_before=_sanitize_snapshot(data["page_before"]),
            page_after=_sanitize_snapshot(data["page_after"]),
            expected_result=str(data["expected_result"]),
            observed_result=str(data["observed_result"]),
            preconditions=list(data["preconditions"]),
            postconditions=list(data["postconditions"]),
            failure_conditions=list(data["failure_conditions"]),
            redacted_fields=list(data["redacted_fields"]),
            ambiguous=bool(data["ambiguous"]),
            ambiguity_reason=str(data["ambiguity_reason"]),
            status=str(data["status"]),
            resolution={},
            created_at=session.started_at or session.created_at,
        )
        uow.captured_actions.add(action)
        created.append(action)
    session.event_count = len(existing) + len(created)
    return created


def update_action(
    uow: UnitOfWork,
    clock: Clock,
    session: CaptureSession,
    action: CapturedAction,
    patch: dict[str, Any],
) -> CapturedAction:
    _require_status(session, _MUTABLE_STATUSES, "CAPTURE_NOT_REVIEWABLE")
    if patch.get("label") is not None:
        action.label = str(patch["label"]).strip()
    if patch.get("kind") is not None:
        kind = str(patch["kind"]).upper()
        if kind not in {"CLICK", "TYPE", "SELECT", "SUBMIT", "NAVIGATE"}:
            raise ValidationError(f"Unsupported action kind {kind!r}.", code="CAPTURE_KIND_INVALID")
        action.kind = kind
    if patch.get("input_param") is not None:
        parameter = str(patch["input_param"]).strip()
        if parameter and not parameter.replace("_", "").isalnum():
            raise ValidationError(
                "Input parameter names may contain letters, digits and underscores only.",
                code="CAPTURE_PARAM_INVALID",
            )
        action.input_param = parameter or None
    if patch.get("input_value") is not None:
        action.input_value = str(patch["input_value"])
    if patch.get("expected_result") is not None:
        action.expected_result = str(patch["expected_result"])
    if patch.get("selector") is not None:
        selectors = list(patch["selector"])
        for candidate in selectors:
            if not isinstance(candidate, dict) or not candidate.get("strategy"):
                raise ValidationError(
                    "Every selector needs a strategy.", code="CAPTURE_SELECTOR_INVALID"
                )
        action.selector = selectors

    accepted = bool(patch.get("accept_ambiguity")) or bool(action.resolution.get("accepted"))
    note = patch.get("resolution_note")
    if note is not None:
        action.resolution = {"accepted": accepted, "note": str(note)}
    elif patch.get("accept_ambiguity"):
        action.resolution = {
            "accepted": True,
            "note": str(action.resolution.get("note") or "Reviewed and accepted."),
        }

    still_ambiguous = bool(action.ambiguity_reason) and not action.resolution.get("accepted")
    action.ambiguous = still_ambiguous
    action.status = (
        "AMBIGUOUS"
        if still_ambiguous
        else ("RESOLVED" if action.resolution.get("accepted") else "CAPTURED")
    )
    session.status = str(CaptureStatus.REVIEW)
    session.validation = {}
    session.validated_at = None
    return action


def _preview_nodes(session: CaptureSession, actions: Sequence[CapturedAction]) -> list[ActionView]:
    nodes = workflow_nodes([_action_dict(item) for item in actions], session.demo_url)
    return [
        ActionView(
            ordinal=int(node["ordinal"]),
            node_key=str(node.get("node_key") or ""),
            node_type=str(node.get("node_type") or "ACTION"),
            kind=str(node.get("kind") or "CLICK"),
            label=str(node.get("label") or ""),
            target=dict(node.get("target") or {}),
            input=dict(node.get("input") or {}),
            preconditions=list(node.get("preconditions") or []),
            postconditions=list(node.get("postconditions") or []),
            depends_on=list(node.get("depends_on") or []),
            channel=str(node.get("channel") or "SIMULATED"),
            side_effect_class=str(node.get("side_effect_class") or "READ_ONLY"),
            requires_authorization=bool(node.get("requires_authorization")),
            timeout_ms=int(node.get("timeout_ms") or 5000),
            retry_policy=dict(node.get("retry_policy") or {}),
            failure_handler=dict(node.get("failure_handler") or {}),
            input_schema=dict(node.get("input_schema") or {}),
            output_schema=dict(node.get("output_schema") or {}),
            redact_paths=list(node.get("redact_paths") or []),
        )
        for node in nodes
    ]


def validate_session(
    uow: UnitOfWork,
    clock: Clock,
    session: CaptureSession,
    actions: Sequence[CapturedAction],
    *,
    browser_factory: Any,
    evidence: EvidenceStore | None = None,
) -> dict[str, Any]:
    _require_status(
        session,
        frozenset({CaptureStatus.REVIEW, CaptureStatus.VALIDATED, CaptureStatus.SAVED}),
        "CAPTURE_NOT_REVIEWABLE",
    )
    if browser_factory is None:
        raise ValidationError(
            "Playwright is not available, so selectors cannot be validated.",
            code="BROWSER_DRIVER_UNAVAILABLE",
        )
    if not actions:
        raise ValidationError("There is nothing to validate.", code="CAPTURE_EMPTY")

    preview = _preview_nodes(session, actions)
    defaults = suggested_inputs([_action_dict(item) for item in actions])
    browser: BrowserSession = browser_factory()
    try:
        report = browser.validate(preview, session.demo_url, defaults)
    except BrowserError as exc:
        raise ValidationError(str(exc), code=exc.code) from exc
    finally:
        with contextlib.suppress(Exception):
            browser.close()

    by_ordinal = {item.ordinal: item for item in actions}
    ok = True
    for entry in report:
        node_key = str(entry.get("node_key") or "")
        key = node_key.replace("captured_", "")
        if node_key.startswith("captured_") and key.isdigit():
            action = by_ordinal.get(int(key) - 1)
            if action is not None:
                action.status = "VALIDATED" if entry.get("ok") else "UNRESOLVED"
        if not entry.get("ok"):
            ok = False

    validation = {
        "ok": ok,
        "checked_at": clock.now().isoformat(),
        "demo_url": session.demo_url,
        "actions": report,
        "checked": len(report),
        "passed": sum(1 for item in report if item.get("ok")),
    }
    session.validation = validation
    session.validated_at = clock.now()
    if session.status != CaptureStatus.SAVED:
        session.status = str(CaptureStatus.VALIDATED if ok else CaptureStatus.REVIEW)
    if evidence is not None:
        record = evidence.write_json(
            run_id=session.id,
            kind=EvidenceKind.SELECTOR_REPORT,
            payload=validation,
        )
        uow.evidence.add(record)
        validation["evidence_id"] = record.id
        session.validation = validation
    return validation


def _generated_name(session: CaptureSession, requested: str | None) -> str:
    if requested:
        return requested
    return f"Learned: {session.label}"[:200]


def save_workflow(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    session: CaptureSession,
    actions: Sequence[CapturedAction],
    *,
    name: str | None,
    description: str,
    publish: bool,
    browser_timeout_ms: int = 8000,
) -> tuple[Workflow, Any]:
    _require_status(session, _MUTABLE_STATUSES, "CAPTURE_NOT_REVIEWABLE")
    if not actions:
        raise ValidationError("There is nothing to save.", code="CAPTURE_EMPTY")
    unresolved = [
        item.ordinal
        for item in actions
        if item.ambiguous and not (item.resolution or {}).get("accepted")
    ]
    if unresolved:
        raise ValidationError(
            "Resolve the ambiguous actions before saving the workflow.",
            code="CAPTURE_AMBIGUITIES_UNRESOLVED",
            details={"ordinals": unresolved},
        )
    if publish and not (session.validation or {}).get("ok"):
        raise ValidationError(
            "Validate the recorded selectors against the demo app before publishing.",
            code="CAPTURE_NOT_VALIDATED",
        )

    action_dicts = [_action_dict(item) for item in actions]
    nodes = workflow_nodes(action_dicts, session.demo_url, browser_timeout_ms=browser_timeout_ms)
    invariants = workflow_invariants(action_dicts)
    input_schema = build_input_schema(action_dicts)

    if session.workflow_id:
        workflow = uow.workflows.get(session.workflow_id)
        if workflow is None:
            raise NotFoundError("The linked workflow no longer exists.", code="WORKFLOW_NOT_FOUND")
        version = workflow_service.add_version(
            uow,
            ids,
            clock,
            workflow=workflow,
            actions=nodes,
            invariants=invariants,
            input_schema=input_schema,
            output_schema={"type": "object"},
            redact_paths=[],
        )
    else:
        workflow_name = _generated_name(session, name)
        try:
            workflow, version = workflow_service.create_workflow(
                uow,
                ids,
                clock,
                name=workflow_name,
                description=(
                    description
                    or "Learned from a controlled demo recording. "
                    "Every step is a typed browser action."
                ),
                owner=session.created_by,
                origin=Origin.LIVE,
                actions=nodes,
                invariants=invariants,
                input_schema=input_schema,
                output_schema={"type": "object"},
                redact_paths=[],
                publish=False,
            )
        except ConflictError:
            workflow, version = workflow_service.create_workflow(
                uow,
                ids,
                clock,
                name=f"{workflow_name} ({session.id[:8]})",
                description=description,
                owner=session.created_by,
                origin=Origin.LIVE,
                actions=nodes,
                invariants=invariants,
                input_schema=input_schema,
                output_schema={"type": "object"},
                redact_paths=[],
                publish=False,
            )
    if publish:
        workflow_service.publish_version(uow, clock, version)
        workflow.current_version_id = version.id
    session.workflow_id = workflow.id
    session.workflow_version_id = version.id
    session.status = str(CaptureStatus.SAVED)
    session.ended_at = session.ended_at or clock.now()
    return workflow, version


def _action_views(actions: Sequence[ActionDefinition], variant: str) -> list[ActionView]:
    views: list[ActionView] = []
    for row in actions:
        target = copy.deepcopy(dict(row.target or {}))
        if variant == "changed" and target.get("url"):
            url = str(target["url"])
            joiner = "&" if "?" in url else "?"
            if "variant=" not in url:
                target["url"] = f"{url}{joiner}variant=changed"
        views.append(
            ActionView(
                ordinal=row.ordinal,
                node_key=row.node_key,
                node_type=row.node_type,
                kind=row.kind,
                label=row.label,
                target=target,
                input=copy.deepcopy(dict(row.input or {})),
                preconditions=list(row.preconditions or []),
                postconditions=list(row.postconditions or []),
                depends_on=list(row.depends_on or []),
                channel=row.channel,
                side_effect_class=row.side_effect_class,
                requires_authorization=row.requires_authorization,
                timeout_ms=row.timeout_ms,
                retry_policy=copy.deepcopy(dict(row.retry_policy or {})),
                failure_handler=copy.deepcopy(dict(row.failure_handler or {})),
                input_schema=copy.deepcopy(dict(row.input_schema or {})),
                output_schema=copy.deepcopy(dict(row.output_schema or {})),
                redact_paths=list(row.redact_paths or []),
            )
        )
    return views


def replay(
    uow: UnitOfWork,
    engine: ExecutionEngine,
    clock: Clock,
    session: CaptureSession,
    *,
    inputs: dict[str, Any],
    variant: str,
    actor: str,
) -> Any:
    if session.status != CaptureStatus.SAVED:
        raise ConflictError(
            "Save the reviewed changes before replaying. Replay always uses the "
            "saved version, never an unsaved edit.",
            code="CAPTURE_NOT_SAVED",
        )
    if not session.workflow_id or not session.workflow_version_id:
        raise ConflictError(
            "Save and publish the learned workflow before replaying it.",
            code="CAPTURE_WORKFLOW_NOT_SAVED",
        )
    workflow = uow.workflows.get(session.workflow_id)
    version = uow.versions.get(session.workflow_version_id)
    if workflow is None or version is None:
        raise NotFoundError(
            "The learned workflow or version no longer exists.", code="WORKFLOW_NOT_FOUND"
        )
    if version.status != VersionStatus.PUBLISHED:
        raise ConflictError(
            "The learned workflow version must be published before replay.",
            code="VERSION_NOT_PUBLISHED",
        )
    if variant not in {"default", "changed"}:
        raise ValidationError("Unknown demo variant.", code="CAPTURE_VARIANT_INVALID")

    errors = validate_schema(inputs, version.input_schema or {})
    if errors:
        raise ValidationError(
            "The replay inputs do not satisfy the workflow input schema.",
            code="INPUT_SCHEMA_VALIDATION_FAILED",
            details={"errors": errors},
        )

    actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version.id))
    views = _action_views(actions, variant)
    return engine.start_run(
        workflow=workflow,
        version=version,
        actions=views,
        invariants=invariants,
        options=ExecuteOptions(
            trigger=RunTrigger.MANUAL,
            origin=Origin.LIVE,
            actor=actor,
            actor_kind=ActorKind.HUMAN,
            capture_session_id=session.id,
            inputs=inputs,
        ),
    )
