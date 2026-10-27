"""Blocker detection and the evidence packet.

Two rules shape this module:

1. A blocker is a *classification of persisted state*, never a narrative. Every
   evidence row resolves a real path in the run state or a real audit event, and
   records where it came from and when it was observed.
2. Detection is declarative. Workflows declare ``blockers`` with a ``when``
   condition and an evidence list; the engine evaluates them. No model output is
   involved, so an explanation can never be promoted to evidence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from flowstate.clock import Clock
from flowstate.db.models import Blocker, BlockerEvidence, ExecutionEvent, ExecutionRun
from flowstate.domain.conditions import MISSING, evaluate_condition, resolve_path
from flowstate.domain.enums import (
    BLOCKER_DEFAULT_ROLE,
    ActorRole,
    BlockerKind,
    BlockerStatus,
    DecisionStatus,
    EventType,
)
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services.audit import canonical_json

_MISSING = MISSING


@dataclass(frozen=True)
class BlockerFinding:
    kind: BlockerKind
    code: str
    title: str
    detail: str
    node_key: str
    ordinal: int
    required_role: ActorRole
    reason: str = ""
    policy_citation: str = ""
    question: str = ""
    recommended: str | None = None
    options: tuple[dict[str, Any], ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    resume_conditions: tuple[dict[str, Any], ...] = ()
    input_spec: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def role_satisfies(actor_role: ActorRole | str, required_role: ActorRole | str) -> bool:
    """ADMIN covers every role; otherwise the roles must match exactly."""
    actor = ActorRole(str(actor_role))
    required = ActorRole(str(required_role))
    return actor == ActorRole.ADMIN or actor == required


def _role(value: Any, default: ActorRole) -> ActorRole:
    try:
        return ActorRole(str(value).upper()) if value else default
    except ValueError:
        return default


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def node_input(node: Any) -> dict[str, Any]:
    """Read the declarative input whether given a graph node or a raw action."""
    action = getattr(node, "action", node)
    payload = getattr(action, "input", None)
    if isinstance(payload, dict):
        return payload
    fallback = getattr(node, "input", None)
    return fallback if isinstance(fallback, dict) else {}


def _declared_blockers(action: Any) -> list[dict[str, Any]]:
    payload = node_input(action)
    raw = payload.get("blockers")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _permission_role(action: Any) -> ActorRole | None:
    payload = node_input(action)
    required = payload.get("required_permission")
    if not required:
        return None
    try:
        return ActorRole(str(payload.get("permission_role", "ADMIN")).upper())
    except ValueError:
        return ActorRole.ADMIN


def find_audit_event(
    events: Sequence[ExecutionEvent],
    *,
    event_type: str | Sequence[str] | None = None,
    event_types: Sequence[str] | None = None,
    node_key: str | None = None,
) -> ExecutionEvent | None:
    accepted: set[str] = set()
    if isinstance(event_type, str):
        accepted.add(event_type)
    elif event_type:
        accepted.update(event_type)
    if event_types:
        accepted.update(event_types)
    for event in reversed(events):
        if accepted and event.type not in accepted:
            continue
        if node_key and getattr(event, "node_key", None) != node_key:
            continue
        return event
    return None


def build_evidence(
    specs: Iterable[dict[str, Any]],
    state: dict[str, Any],
    *,
    now: datetime,
    events: Sequence[ExecutionEvent] = (),
) -> list[dict[str, Any]]:
    """Resolve evidence specifications against persisted state and events."""
    items: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        source = str(spec.get("source", ""))
        label = str(spec.get("label") or source or "Record")
        relation = str(spec.get("relation", ""))
        source_kind = str(spec.get("source_kind", "STATE_FIELD")).upper()

        if source_kind == "AUDIT_EVENT":
            event = find_audit_event(
                events,
                event_type=spec.get("event_type"),
                event_types=spec.get("event_types"),
                node_key=spec.get("node_key"),
            )
            if event is None:
                items.append(
                    {
                        "source_kind": "AUDIT_EVENT",
                        "source_path": str(spec.get("event_type") or "event"),
                        "source_ref": spec.get("node_key"),
                        "label": label,
                        "relation": relation,
                        "value": {"found": False},
                        "validation": "MISSING",
                        "detail": "No matching audit event was recorded.",
                        "observed_at": now.isoformat(),
                        "fresh_until": None,
                    }
                )
                continue
            items.append(
                {
                    "source_kind": "AUDIT_EVENT",
                    "source_path": event.type,
                    "source_ref": getattr(event, "node_key", None) or str(event.seq),
                    "label": label,
                    "relation": relation,
                    "value": {
                        "seq": event.seq,
                        "event_type": event.type,
                        "hash": event.hash,
                        "recorded_at": event.recorded_at.isoformat(),
                    },
                    "validation": "INFO",
                    "detail": f"Audit event seq {event.seq} is hash-linked to the run.",
                    "observed_at": event.recorded_at.isoformat(),
                    "fresh_until": None,
                }
            )
            continue

        observed = resolve_path(state, source) if source else _MISSING
        missing = observed is _MISSING or observed is None
        value = None if missing else observed
        detail = f"{source} = {value!r}"
        validation = "INFO"

        if missing:
            validation = "MISSING"
            detail = f"{source} is not present in run state."
        else:
            validate = spec.get("validate")
            if isinstance(validate, dict) and validate:
                result = evaluate_condition(validate, state, now=now)
                validation = "PASS" if result.ok else "FAIL"
                detail = result.detail
            expires_path = spec.get("expires_path")
            if expires_path:
                expires = resolve_path(state, str(expires_path))
                if expires is not _MISSING and expires is not None:
                    expiry_check = evaluate_condition(
                        {"path": str(expires_path), "op": "expired"}, state, now=now
                    )
                    validation = "EXPIRED" if expiry_check.ok else "VALID"
                    detail = expiry_check.detail

        fresh_until = None
        ttl = spec.get("fresh_for_seconds")
        if isinstance(ttl, (int, float)) and ttl > 0:
            fresh_until = (now + timedelta(seconds=float(ttl))).isoformat()

        items.append(
            {
                "source_kind": "STATE_FIELD",
                "source_path": source,
                "source_ref": spec.get("record_ref"),
                "label": label,
                "relation": relation,
                "value": {"value": value},
                "validation": validation,
                "detail": detail,
                "observed_at": now.isoformat(),
                "fresh_until": fresh_until,
            }
        )
    return items


def evidence_hash(items: Sequence[dict[str, Any]]) -> str:
    """Stable hash of the facts a human is being asked to rely on."""
    material = [
        {
            "source_kind": item.get("source_kind"),
            "source_path": item.get("source_path"),
            "source_ref": item.get("source_ref"),
            "value": item.get("value"),
            "validation": item.get("validation"),
            "fresh_until": item.get("fresh_until"),
        }
        for item in items
    ]
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _authorization_evidence(node: Any, state: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    evidence: list[dict[str, Any]] = []
    candidates = (
        ("invoice.status", "Invoice status at the blocked node"),
        ("page.state.status", "Page invoice status at the blocked node"),
        ("page.state.invoice_id", "Page invoice identifier at the blocked node"),
        ("page.page", "Current page at the blocked node"),
    )
    for source, label in candidates:
        observed = resolve_path(state, source)
        if observed is MISSING or observed is None:
            continue
        evidence.append({"source": source, "label": label, "relation": "current state"})
        break
    evidence.append(
        {
            "source_kind": "AUDIT_EVENT",
            "event_type": [EventType.NODE_STARTED, EventType.ACTION_STARTED],
            "node_key": getattr(node, "key", ""),
            "label": "Step start event",
            "relation": "blocked node",
        }
    )
    return tuple(evidence)


def detect_blockers(
    node: Any,
    state: dict[str, Any],
    run: ExecutionRun,
    *,
    now: datetime,
    actor_role: ActorRole = ActorRole.OPERATOR,
    events: Sequence[ExecutionEvent] = (),
    checkpoint_available: bool = True,
    context: str = "execution",
) -> list[BlockerFinding]:
    """Return every blocker that currently applies to ``node``.

    Order is deterministic: declaration order, then the implicit authorization
    blocker. The caller asks about the first one only, because later blockers may
    become moot once the first is resolved.
    """
    findings: list[BlockerFinding] = []
    action_input = node_input(node)
    raw_template = action_input.get("decision")
    template: dict[str, Any] = raw_template if isinstance(raw_template, dict) else {}

    for index, spec in enumerate(_declared_blockers(node)):
        when = spec.get("when")
        if isinstance(when, dict) and when and not evaluate_condition(when, state, now=now).ok:
            continue
        try:
            kind = BlockerKind(str(spec.get("kind", "")).upper())
        except ValueError:
            continue

        declared_role = _role(spec.get("required_role"), BLOCKER_DEFAULT_ROLE[kind])
        findings.append(
            BlockerFinding(
                kind=kind,
                code=str(spec.get("code") or f"{kind.value}_BLOCKER"),
                title=str(spec.get("title") or kind.value.replace("_", " ").title()),
                detail=str(spec.get("detail") or ""),
                node_key=getattr(node, "key", "") or "",
                ordinal=int(getattr(node, "ordinal", 0) or 0),
                required_role=declared_role,
                reason=str(spec.get("reason") or template.get("reason") or ""),
                policy_citation=str(
                    spec.get("policy_citation") or template.get("policy_citation") or ""
                ),
                question=str(spec.get("question") or template.get("question") or ""),
                recommended=spec.get("recommended") or template.get("recommended"),
                options=_as_tuple(spec.get("options") or template.get("options")),
                evidence=_as_tuple(spec.get("evidence")),
                resume_conditions=_as_tuple(spec.get("resume_conditions")),
                input_spec=_as_tuple(spec.get("inputs")),
                metadata={"declaration_index": index},
            )
        )

    permission_role = _permission_role(node)
    if permission_role is not None and not role_satisfies(actor_role, permission_role):
        findings.append(
            BlockerFinding(
                kind=BlockerKind.PERMISSION_REQUIRED,
                code="PERMISSION_REQUIRED",
                title="This step needs a permission the run does not hold",
                detail=(
                    f"The node requires {action_input.get('required_permission')!r}, "
                    f"which the {ActorRole(str(actor_role))} role does not grant."
                ),
                node_key=getattr(node, "key", "") or "",
                ordinal=int(getattr(node, "ordinal", 0) or 0),
                required_role=permission_role,
                reason="The executing identity lacks the permission this node declares.",
                policy_citation="ActionPolicy: missing permission cannot be inferred.",
                options=_as_tuple(template.get("options")),
                evidence=_as_tuple(
                    [
                        {
                            "source": "session.operator",
                            "label": "Executing identity",
                            "relation": "acting identity",
                            "record_ref": run.actor,
                        }
                    ]
                ),
            )
        )

    if getattr(node, "requires_authorization", False):
        findings.append(
            BlockerFinding(
                kind=BlockerKind.HUMAN_APPROVAL_REQUIRED,
                code="HUMAN_APPROVAL_REQUIRED",
                title=str(
                    template.get("title") or f"Authorize: {getattr(node, 'label', 'action')}"
                ),
                detail=str(
                    template.get("reason")
                    or "This step changes financial state and cannot be undone by the engine."
                ),
                node_key=getattr(node, "key", "") or "",
                ordinal=int(getattr(node, "ordinal", 0) or 0),
                required_role=_role(
                    template.get("required_role"),
                    BLOCKER_DEFAULT_ROLE[BlockerKind.HUMAN_APPROVAL_REQUIRED],
                ),
                reason=str(template.get("reason") or ""),
                policy_citation=str(
                    template.get("policy_citation")
                    or "ActionPolicy: IRREVERSIBLE actions require human authorization."
                ),
                question=str(
                    template.get("question")
                    or f"Authorize '{getattr(node, 'label', 'action')}' to continue this run?"
                ),
                recommended=template.get("recommended"),
                options=_as_tuple(template.get("options")),
                evidence=_as_tuple(_authorization_evidence(node, state)),
                resume_conditions=_as_tuple(template.get("resume_conditions")),
            )
        )

    if context == "recovery" and not checkpoint_available:
        findings.append(
            BlockerFinding(
                kind=BlockerKind.RECOVERY_PREREQUISITE_MISSING,
                code="RECOVERY_PREREQUISITE_MISSING",
                title="No usable checkpoint exists to recover this run",
                detail=(
                    "Recovery requires a checkpoint whose state hash matches. None is "
                    "available, so a human must choose a strategy."
                ),
                node_key=getattr(node, "key", "") or "",
                ordinal=int(getattr(node, "ordinal", 0) or 0),
                required_role=BLOCKER_DEFAULT_ROLE[BlockerKind.RECOVERY_PREREQUISITE_MISSING],
                reason="Automated recovery cannot proceed without a valid restore point.",
                policy_citation=(
                    "RecoveryPolicy: state cannot be restored without a verified checkpoint."
                ),
                options=_as_tuple(
                    [
                        {
                            "value": "replay",
                            "label": "Replay from the start",
                            "consequence": "Re-executes the workflow in the sandbox",
                        },
                        {
                            "value": "reject",
                            "label": "Abandon the run",
                            "consequence": "Marks the run failed with this explanation",
                        },
                    ]
                ),
                evidence=_as_tuple(
                    [
                        {
                            "source_path_kind": "checkpoint",
                            "label": "Latest checkpoint",
                            "relation": "recovery prerequisite",
                        }
                    ]
                ),
            )
        )

    findings.sort(key=lambda item: (item.ordinal, item.metadata.get("declaration_index", 0)))
    return findings


def persist_blocker(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    *,
    run: ExecutionRun,
    finding: BlockerFinding,
    state: dict[str, Any],
    events: Sequence[ExecutionEvent] = (),
) -> tuple[Blocker, list[BlockerEvidence]]:
    now = clock.now()
    items = build_evidence(finding.evidence, state, now=now, events=events)
    digest = evidence_hash(items)
    blocker = Blocker(
        id=ids.new(),
        run_id=run.id,
        node_key=finding.node_key,
        action_ordinal=finding.ordinal,
        kind=str(finding.kind),
        code=finding.code,
        title=finding.title,
        detail=finding.detail,
        status=str(BlockerStatus.OPEN),
        required_role=str(finding.required_role),
        resume_conditions=list(finding.resume_conditions),
        input_spec=list(finding.input_spec),
        evidence_spec=list(finding.evidence),
        evidence_hash=digest,
        origin=run.origin,
        created_at=now,
    )
    uow.blockers.add(blocker)

    rows: list[BlockerEvidence] = []
    for item in items:
        row = BlockerEvidence(
            id=ids.new(),
            blocker_id=blocker.id,
            run_id=run.id,
            source_kind=str(item["source_kind"]),
            source_path=str(item["source_path"]),
            source_ref=item.get("source_ref"),
            label=str(item["label"]),
            relation=str(item["relation"]),
            value_json=item.get("value") or {},
            validation=str(item["validation"]),
            detail=str(item["detail"]),
            observed_at=now,
            fresh_until=(
                datetime.fromisoformat(item["fresh_until"]) if item.get("fresh_until") else None
            ),
        )
        uow.blocker_evidence.add(row)
        rows.append(row)

    return blocker, rows


def current_evidence(
    uow: UnitOfWork,
    blocker: Blocker,
    state: dict[str, Any],
    *,
    now: datetime,
    events: Sequence[ExecutionEvent] = (),
) -> tuple[list[dict[str, Any]], str]:
    """Recompute the evidence packet for freshness comparisons."""
    items = build_evidence(blocker.evidence_spec or [], state, now=now, events=events)
    return items, evidence_hash(items)


def resolve_blockers(
    uow: UnitOfWork,
    *,
    run_id: str,
    now: datetime,
    node_key: str | None = None,
    decision_id: str | None = None,
) -> list[Blocker]:
    rows = uow.blockers.list(run_id=run_id)
    resolved: list[Blocker] = []
    for blocker in rows:
        if blocker.status != BlockerStatus.OPEN:
            continue
        if node_key and blocker.node_key and blocker.node_key != node_key:
            continue
        if decision_id and blocker.decision_id not in {None, decision_id}:
            continue
        blocker.status = str(BlockerStatus.RESOLVED)
        blocker.resolved_at = now
        resolved.append(blocker)
    return resolved


def open_blockers(uow: UnitOfWork, run_id: str) -> list[Blocker]:
    return [item for item in uow.blockers.list(run_id=run_id) if item.status == BlockerStatus.OPEN]


def decision_status_for(blocker: Blocker) -> DecisionStatus:
    return DecisionStatus.NOTIFIED
