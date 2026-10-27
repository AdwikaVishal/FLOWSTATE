"""Deterministic graph execution engine.

The engine is the only component that drives actions and the only writer of run
status. It executes a validated typed DAG in deterministic ``(ordinal, key)``
order, records every transition in the hash-linked audit chain, and keeps a
queryable per-node projection.

Design rules enforced here:

* A tool response is never success on its own; postconditions decide.
* An ambiguous outcome (a timeout after the request may have been applied) is
  ``UNKNOWN`` and is reconciled with a read-only probe before anything else.
* Only READ_ONLY/IDEMPOTENT nodes are retried automatically. An explicit retry
  policy cannot override that.
* Every attempt has a classified error, a duration and an idempotency key when
  the target supports one.
* Cancellation is checked at safe node boundaries.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from flowstate.capture.browser import BrowserError, BrowserSession, BrowserUnavailable
from flowstate.clock import Clock
from flowstate.config import Settings
from flowstate.db.models import (
    ActionDefinition,
    Checkpoint,
    ExecutionEvent,
    ExecutionNodeState,
    ExecutionRun,
    FaultScenario,
    HumanDecision,
    Invariant,
    InvariantVerdictRecord,
    Workflow,
    WorkflowVersion,
)
from flowstate.domain.enums import (
    NODE_SUCCESS_STATUSES,
    NODE_TERMINAL_STATUSES,
    ActorKind,
    ActorRole,
    CheckpointKind,
    DecisionStatus,
    ErrorClass,
    EventType,
    EvidenceKind,
    InvariantVerdict,
    NodeStatus,
    NodeType,
    Origin,
    RunStatus,
    RunTrigger,
    RunVerdict,
)
from flowstate.domain.graph import (
    GraphNode,
    WorkflowGraph,
    active_failure_handler,
    build_graph,
    graph_summary,
    normalize_retry_policy,
)
from flowstate.domain.invariants import InvariantResult, aggregate, evaluate_invariant
from flowstate.domain.policy import DEFAULT_POLICY, ActionPolicy, PolicyDecision
from flowstate.domain.schema_validation import validate_schema
from flowstate.errors import ConflictError, ValidationError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.sandbox import invoice
from flowstate.services import blockers as blocker_service
from flowstate.services.audit import AuditWriter, canonical_json
from flowstate.services.cancellation import CancellationRegistry
from flowstate.services.evidence import EvidenceStore
from flowstate.services.handlers import (
    DEFAULT_HANDLERS,
    HandlerContext,
    HandlerRegistry,
    HandlerResult,
    evaluate_postconditions,
    evaluate_preconditions,
)
from flowstate.services.redaction import redact, redact_state

logger = logging.getLogger(__name__)

_RETRYABLE_SIDE_EFFECTS = frozenset({"READ_ONLY", "IDEMPOTENT"})
_IDEMPOTENT_SIDE_EFFECTS = frozenset({"IDEMPOTENT"})
_ACTION_CHANNELS = frozenset({"SIMULATED", "BROWSER"})


@dataclass
class ExecuteOptions:
    trigger: RunTrigger = RunTrigger.MANUAL
    origin: Origin = Origin.LIVE
    seed: int = 0
    scenario: FaultScenario | None = None
    actor: str = "local-operator"
    actor_kind: ActorKind = ActorKind.HUMAN
    authorized_ordinals: set[int] = field(default_factory=set)
    actor_role: ActorRole = ActorRole.OPERATOR
    pre_authorization_reason: str | None = None
    seed_batch: str | None = None
    capture_session_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    publish_progress: bool = False


class ExecutionEngine:
    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdFactory,
        settings: Settings,
        evidence: EvidenceStore,
        policy: ActionPolicy | None = None,
        *,
        handlers: HandlerRegistry | None = None,
        cancellations: CancellationRegistry | None = None,
        browser_factory: Callable[[], BrowserSession] | None = None,
    ) -> None:
        self.uow = uow
        self.clock = clock
        self.ids = ids
        self.settings = settings
        self.policy = policy or DEFAULT_POLICY
        self.handlers = handlers or DEFAULT_HANDLERS
        self.cancellations = cancellations
        self.browser_factory = browser_factory
        self.audit = AuditWriter(uow, clock, ids)
        self.evidence = evidence
        self._version: WorkflowVersion | None = None
        self._redact_paths: list[str] = []

    # ------------------------------------------------------------------ public

    def start_run(
        self,
        *,
        workflow: Workflow,
        version: WorkflowVersion,
        actions: Sequence[Any],
        invariants: Sequence[Invariant],
        options: ExecuteOptions | None = None,
        state: dict[str, Any] | None = None,
        start_ordinal: int = 0,
        existing_run: ExecutionRun | None = None,
    ) -> ExecutionRun:
        opts = options or ExecuteOptions()
        self._version = version
        self._redact_paths = list(getattr(version, "redact_paths", None) or [])

        graph = build_graph(actions)
        self._validate_handlers(graph)
        input_errors = validate_schema(opts.inputs, getattr(version, "input_schema", None))
        if input_errors:
            raise ValidationError(
                "The run inputs do not satisfy the workflow input schema.",
                code="INPUT_SCHEMA_VALIDATION_FAILED",
                details={"errors": input_errors},
            )

        channels = self._validate_channels(graph)
        driver_name = invoice.DRIVER_NAME
        browser: BrowserSession | None = None
        if "BROWSER" in channels:
            browser = self._open_browser()
            driver_name = browser.driver_name

        try:
            now = self.clock.now()
            run = existing_run or ExecutionRun(
                id=self.ids.new(),
                workflow_id=workflow.id,
                workflow_version_id=version.id,
                status=str(RunStatus.PENDING),
                verdict=str(RunVerdict.PENDING),
                trigger=str(opts.trigger),
                origin=str(opts.origin),
                driver=driver_name,
                seed=opts.seed,
                current_ordinal=start_ordinal,
                fault_scenario_id=opts.scenario.id if opts.scenario else None,
                actor=opts.actor,
                seed_batch=opts.seed_batch,
                capture_session_id=opts.capture_session_id,
                inputs=redact(opts.inputs, self._redact_paths),
                outputs={},
                created_at=now,
            )
            if existing_run is None:
                self.uow.runs.add(run)
                self.audit.append(
                    run.id,
                    EventType.RUN_CREATED,
                    actor=opts.actor_kind,
                    actor_id=opts.actor,
                    payload=self._safe(
                        {
                            "workflow_id": workflow.id,
                            "workflow_slug": workflow.slug,
                            "workflow_name": workflow.name,
                            "version_id": version.id,
                            "version": version.version,
                            "trigger": str(opts.trigger),
                            "origin": str(opts.origin),
                            "driver": driver_name,
                            "channels": sorted(channels),
                            "capture_session_id": opts.capture_session_id,
                            "seed": opts.seed,
                            "scenario_id": opts.scenario.id if opts.scenario else None,
                            "graph": graph_summary(graph),
                        }
                    ),
                )
            else:
                run.driver = driver_name
            self.audit.append(
                run.id,
                EventType.WORKFLOW_VALIDATED,
                payload=self._safe(
                    {
                        "graph": graph_summary(graph),
                        "handlers": self.handlers.registered,
                        "channels": sorted(channels),
                        "input_schema_valid": True,
                    }
                ),
            )
            self.audit.append(
                run.id,
                EventType.RUN_INPUTS_RECORDED,
                payload=self._safe({"inputs": redact(opts.inputs, self._redact_paths)}),
            )

            run.status = str(RunStatus.RUNNING)
            run.started_at = now
            self.audit.append(
                run.id,
                EventType.RUN_STARTED,
                actor=opts.actor_kind,
                actor_id=opts.actor,
                payload={"start_ordinal": start_ordinal, "nodes": len(graph.nodes)},
            )

            if opts.pre_authorization_reason and opts.authorized_ordinals:
                self.audit.append(
                    run.id,
                    EventType.AUTHORIZATION_PRE_GRANTED,
                    actor=ActorKind.HUMAN,
                    actor_id=opts.actor,
                    payload={
                        "scope": "sandbox-run",
                        "ordinals": sorted(opts.authorized_ordinals),
                        "reason": opts.pre_authorization_reason,
                    },
                )

            if opts.scenario is not None and opts.scenario.status == "READY":
                opts.scenario.status = "INJECTING"

            self._ensure_node_states(run, graph)
            self._execute_graph(
                run=run,
                graph=graph,
                version=version,
                invariants=invariants,
                state=state if state is not None else self._initial_state(channels),
                options=opts,
                resume_ordinal=None,
                browser=browser,
            )
            return run
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    logger.exception("browser session close failed")

    def resume_run(
        self,
        *,
        run: ExecutionRun,
        actions: Sequence[ActionDefinition],
        invariants: Sequence[Invariant],
        state: dict[str, Any],
        start_ordinal: int,
        options: ExecuteOptions,
    ) -> ExecutionRun:
        version = self.uow.versions.get(run.workflow_version_id)
        if version is None:
            raise ConflictError(
                "The workflow version for this run no longer exists.", code="VERSION_NOT_FOUND"
            )
        self._version = version
        self._redact_paths = list(getattr(version, "redact_paths", None) or [])
        graph = build_graph(actions)
        self._validate_handlers(graph)
        channels = self._validate_channels(graph)
        browser: BrowserSession | None = None
        if "BROWSER" in channels:
            if start_ordinal != 0:
                raise ConflictError(
                    "A browser-backed run cannot be resumed mid-page: the live page state is "
                    "not persisted. Start a fresh replay instead.",
                    code="BROWSER_RESUME_UNSUPPORTED",
                )
            # A replay from ordinal zero rebuilds the browser context instead of
            # pretending the old page survived. The engine still owns ordering,
            # authorization, postconditions and verification for every step.
            browser = self._open_browser()
            if channels == {"BROWSER"}:
                state = {**self._initial_state(channels), "inputs": dict(options.inputs)}
            else:
                state = dict(state)
                state["page"] = {}
                state["node_outputs"] = {}
                state.setdefault("inputs", dict(options.inputs))

        try:
            run.status = str(RunStatus.RUNNING)
            run.verdict = str(RunVerdict.PENDING)
            run.ended_at = None
            run.failure_code = None
            run.failure_message = None
            run.failure_ordinal = None
            self._ensure_node_states(run, graph)
            self._prepare_resume(run, graph, start_ordinal)

            self._execute_graph(
                run=run,
                graph=graph,
                version=version,
                invariants=invariants,
                state=state,
                options=options,
                resume_ordinal=start_ordinal,
                browser=browser,
            )
            return run
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    logger.exception("browser session close failed")

    # --------------------------------------------------------------- internals

    def _validate_handlers(self, graph: WorkflowGraph) -> None:
        missing = [
            node.node_type.value
            for node in graph.nodes.values()
            if not self.handlers.supports(node.node_type)
        ]
        if missing:
            raise ValidationError(
                "The workflow uses node types with no registered handler.",
                code="HANDLER_NOT_REGISTERED",
                details={"node_types": sorted(set(missing))},
            )

    def _validate_channels(self, graph: WorkflowGraph) -> set[str]:
        """Reject unregistered action drivers *before* a run is created."""
        channels: set[str] = set()
        unsupported: list[str] = []
        for node in graph.nodes.values():
            if node.node_type not in {NodeType.ACTION, NodeType.HUMAN_DECISION}:
                continue
            channel = str(getattr(node.action, "channel", "SIMULATED") or "SIMULATED").upper()
            channels.add(channel)
            if channel not in _ACTION_CHANNELS:
                unsupported.append(channel)
        if unsupported:
            raise ValidationError(
                "The workflow uses an action channel with no registered driver.",
                code="DRIVER_NOT_REGISTERED",
                details={"channels": sorted(set(unsupported))},
            )
        if "BROWSER" in channels and self.browser_factory is None:
            raise ValidationError(
                "This workflow needs the Playwright browser driver, but it is not configured. "
                "Install playwright and Chromium, then retry.",
                code="BROWSER_DRIVER_UNAVAILABLE",
            )
        return channels

    def _open_browser(self) -> BrowserSession:
        assert self.browser_factory is not None
        try:
            session = self.browser_factory()
            session.start()
            return session
        except BrowserUnavailable as exc:
            raise ValidationError(str(exc), code=exc.code) from exc
        except BrowserError as exc:
            raise ValidationError(str(exc), code=exc.code) from exc

    @staticmethod
    def _initial_state(channels: set[str]) -> dict[str, Any]:
        if channels == {"BROWSER"}:
            return {"page": {}, "node_outputs": {}}
        return invoice.initial_state()

    def _ensure_node_states(
        self, run: ExecutionRun, graph: WorkflowGraph
    ) -> dict[str, ExecutionNodeState]:
        existing = {item.node_key: item for item in self.uow.node_states.list(run_id=run.id)}
        for node in graph.execution_nodes:
            if node.key in existing:
                continue
            max_attempts = 1
            if node.node_type == NodeType.ACTION:
                max_attempts = normalize_retry_policy(node.retry_policy)["max_attempts"]
            state = ExecutionNodeState(
                id=self.ids.new(),
                run_id=run.id,
                node_key=node.key,
                node_type=node.node_type.value,
                ordinal=node.ordinal,
                status=str(NodeStatus.PENDING),
                attempt=0,
                max_attempts=max_attempts,
                output={},
            )
            self.uow.node_states.add(state)
            existing[node.key] = state
        return existing

    def _prepare_resume(self, run: ExecutionRun, graph: WorkflowGraph, start_ordinal: int) -> None:
        states = {item.node_key: item for item in self.uow.node_states.list(run_id=run.id)}
        if start_ordinal == 0:
            for node in graph.execution_nodes:
                state = states[node.key]
                state.status = str(NodeStatus.PENDING)
                state.error_class = None
                state.error_code = None
                state.error_message = None
                state.checkpoint_id = None
                state.attempt = 0
                state.started_at = None
                state.ended_at = None
                state.reconciled = False
            run.current_ordinal = 0
            return
        for node in graph.execution_nodes:
            state = states[node.key]
            if node.ordinal < start_ordinal:
                if state.status not in {item.value for item in NODE_SUCCESS_STATUSES}:
                    state.status = str(NodeStatus.PASSED)
                    state.error_class = None
                    state.error_code = None
                    state.error_message = None
                    state.ended_at = state.ended_at or self.clock.now()
            elif state.status in {
                NodeStatus.RUNNING.value,
                NodeStatus.BLOCKED.value,
                NodeStatus.FAILED.value,
            }:
                state.status = str(NodeStatus.PENDING)
                state.error_class = None
                state.error_code = None
                state.error_message = None
        run.current_ordinal = max(run.current_ordinal, start_ordinal)

    def _execute_graph(
        self,
        *,
        run: ExecutionRun,
        graph: WorkflowGraph,
        version: WorkflowVersion,
        invariants: Sequence[Invariant],
        state: dict[str, Any],
        options: ExecuteOptions,
        resume_ordinal: int | None,
        browser: BrowserSession | None = None,
    ) -> None:
        state.setdefault("inputs", copy.deepcopy(options.inputs))
        if browser is not None:
            state.setdefault("page", {})
        state.setdefault("node_outputs", {})
        states = self._ensure_node_states(run, graph)
        route_selected = self._route_selections(graph, states)
        active_recovery: set[str] = {
            node.key
            for node in graph.execution_nodes
            if node.node_type == NodeType.RECOVERY_HANDLER
            and states[node.key].status in {NodeStatus.PASSED.value, NodeStatus.RECOVERED.value}
        }
        executed = 0
        while True:
            if self._cancellation_requested(run):
                self._cancel_execution(run, graph, states, options)
                return
            if run.status in {
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.TIMED_OUT,
                RunStatus.AWAITING_DECISION,
                RunStatus.AWAITING_RECOVERY,
            }:
                return

            ready = self._ready_nodes(graph, states, route_selected, active_recovery)
            if not ready:
                if self._skip_unreachable(run, graph, states):
                    continue
                if all(
                    states[key].status in {item.value for item in NODE_TERMINAL_STATUSES}
                    for key in graph.order
                ):
                    break
                self._fail(
                    run, "GRAPH_DEADLOCK", "No runnable node remains and the graph is not complete."
                )
                return

            node = min(ready, key=lambda item: (item.ordinal, item.key))
            executed += 1
            if executed > self.settings.run_action_budget:
                self._time_out(run, "run action budget exhausted")
                return

            result = self._run_node(
                run=run,
                node=node,
                graph=graph,
                state=state,
                options=options,
                states=states,
                route_selected=route_selected,
                active_recovery=active_recovery,
                browser=browser,
            )
            if options.publish_progress:
                self.uow.commit()
            if result == "STOP":
                return

        # Nodes that were never needed (dormant recovery handlers) are skipped.
        for key in graph.order:
            node_state = states[key]
            if node_state.status == NodeStatus.PENDING.value:
                node_state.status = str(NodeStatus.SKIPPED)
                node_state.ended_at = self.clock.now()
                node_state.error_class = ErrorClass.DEPENDENCY_FAILED.value
                node_state.error_code = "NOT_REACHED"
                node_state.error_message = "Node was not required by the selected execution path."
                self.audit.append(
                    run.id,
                    EventType.NODE_SKIPPED,
                    action_ordinal=node_state.ordinal,
                    node_key=key,
                    payload={"reason": "not_reached", "node_type": node_state.node_type},
                )

        self._verify(run, invariants, state, options, version)

    def _ready_nodes(
        self,
        graph: WorkflowGraph,
        states: dict[str, ExecutionNodeState],
        route_selected: dict[str, str],
        active_recovery: set[str],
    ) -> list[GraphNode]:
        ready: list[GraphNode] = []
        for key in graph.order:
            node = graph.nodes[key]
            node_state = states[key]
            if node_state.status in {item.value for item in NODE_TERMINAL_STATUSES}:
                continue
            if node.node_type == NodeType.RECOVERY_HANDLER:
                if key not in active_recovery:
                    continue
                # A recovery handler is deliberately allowed to run even when the
                # node it compensates for has failed.
                ready.append(node)
                continue
            if node.route_from:
                source = node.route_from["node_key"]
                selected = route_selected.get(source)
                if selected is not None and selected != key:
                    continue
                if selected is None:
                    continue
            if all(
                states[dep].status in {item.value for item in NODE_SUCCESS_STATUSES}
                for dep in node.depends_on
            ):
                ready.append(node)
        return ready

    def _skip_unreachable(
        self, run: ExecutionRun, graph: WorkflowGraph, states: dict[str, ExecutionNodeState]
    ) -> bool:
        progress = False
        for key in graph.order:
            node = graph.nodes[key]
            node_state = states[key]
            if node_state.status in {item.value for item in NODE_TERMINAL_STATUSES}:
                continue
            if node.node_type == NodeType.RECOVERY_HANDLER:
                continue
            if node.route_from:
                source_state = states.get(node.route_from["node_key"])
                if source_state and source_state.status == NodeStatus.PASSED.value:
                    selected = (source_state.output or {}).get("outcome")
                    target = graph.route_targets.get(node.route_from["node_key"], {}).get(
                        str(selected)
                    )
                    if target and target != key:
                        self._skip_node(
                            run,
                            graph.nodes[key],
                            node_state,
                            code="ROUTE_NOT_SELECTED",
                            message=f"Branch selected {target!r}, not this node.",
                        )
                        progress = True
                        continue
            failing = [
                dep
                for dep in node.depends_on
                if states[dep].status
                in {NodeStatus.FAILED.value, NodeStatus.BLOCKED.value, NodeStatus.CANCELLED.value}
            ]
            if failing:
                self._skip_node(
                    run,
                    graph.nodes[key],
                    node_state,
                    code="DEPENDENCY_FAILED",
                    message=f"Dependency {failing[0]!r} did not pass.",
                )
                progress = True
        return progress

    def _route_selections(
        self, graph: WorkflowGraph, states: dict[str, ExecutionNodeState]
    ) -> dict[str, str]:
        selected: dict[str, str] = {}
        for key, targets in graph.route_targets.items():
            state = states.get(key)
            if state is None or state.status != NodeStatus.PASSED.value:
                continue
            outcome = str((state.output or {}).get("outcome", ""))
            target = targets.get(outcome)
            if target:
                selected[key] = target
        return selected

    def _run_node(
        self,
        *,
        run: ExecutionRun,
        node: GraphNode,
        graph: WorkflowGraph,
        state: dict[str, Any],
        options: ExecuteOptions,
        states: dict[str, ExecutionNodeState],
        route_selected: dict[str, str],
        active_recovery: set[str],
        browser: BrowserSession | None = None,
    ) -> str:
        node_state = states[node.key]
        started = self.clock.now()
        node_state.started_at = started
        node_state.status = str(NodeStatus.RUNNING)
        node_state.error_class = None
        node_state.error_code = None
        node_state.error_message = None

        self._emit_node_started(run, node, node_state)

        if node.node_type in {NodeType.ACTION, NodeType.HUMAN_DECISION}:
            failed = evaluate_preconditions(node, state)
            if failed:
                return self._finish_failure(
                    run,
                    node,
                    node_state,
                    state,
                    options,
                    graph=graph,
                    active_recovery=active_recovery,
                    error_class=ErrorClass.PRECONDITION,
                    code="PRECONDITION_FAILED",
                    message=failed[0],
                    retryable=False,
                )

        authorized = True
        if node.node_type == NodeType.ACTION and node.ordinal not in options.authorized_ordinals:
            findings = blocker_service.detect_blockers(
                node,
                state,
                run,
                now=self.clock.now(),
                actor_role=options.actor_role,
                events=list(self.uow.events.read(run.id)),
            )
            if findings:
                self._request_decision(run, node, state, options)
                node_state.status = str(NodeStatus.BLOCKED)
                node_state.error_class = None
                node_state.error_code = findings[0].code
                node_state.error_message = findings[0].title
                return "STOP"

        if node.node_type in {NodeType.ACTION, NodeType.HUMAN_DECISION}:
            policy_result = self.policy.may_execute(node.action)
            if policy_result.decision == PolicyDecision.DENY:
                return self._finish_failure(
                    run,
                    node,
                    node_state,
                    state,
                    options,
                    graph=graph,
                    active_recovery=active_recovery,
                    error_class=ErrorClass.POLICY,
                    code=policy_result.code,
                    message=policy_result.detail,
                    retryable=False,
                )
            needs_auth = (
                node.requires_authorization or policy_result.decision == PolicyDecision.ESCALATE
            )
            if needs_auth and node.ordinal not in options.authorized_ordinals:
                self._request_decision(run, node, state, options)
                node_state.status = str(NodeStatus.BLOCKED)
                node_state.error_class = None
                node_state.error_code = "AWAITING_DECISION"
                node_state.error_message = "Waiting for a human authorization."
                return "STOP"

        if (
            node.node_type == NodeType.HUMAN_DECISION
            and node.ordinal not in options.authorized_ordinals
        ):
            self._request_decision(run, node, state, options)
            node_state.status = str(NodeStatus.BLOCKED)
            node_state.error_code = "AWAITING_DECISION"
            node_state.error_message = "Waiting for a human decision."
            return "STOP"

        if node.node_type == NodeType.CHECKPOINT:
            checkpoint = self._save_checkpoint(
                run, state, node.ordinal, CheckpointKind.SAFE_POINT, node.key
            )
            node_state.checkpoint_id = checkpoint.id

        policy = normalize_retry_policy(node.retry_policy)
        # Attempts accumulate across resumes so the budget is enforced against
        # the full history of this node, not just the current engine invocation.
        attempt = max(int(node_state.attempt or 0), 0)
        fault = self._fault_for(run, node, options.scenario)
        fault_announced = False

        while True:
            if self._cancellation_requested(run):
                self._cancel_execution(run, graph, states, options)
                return "STOP"
            attempt += 1
            node_state.attempt = attempt
            if attempt > 1:
                self._emit_node_started(run, node, node_state, retry=True)

            if fault is not None and not fault_announced:
                self.audit.append(
                    run.id,
                    EventType.FAULT_INJECTED,
                    action_ordinal=node.ordinal,
                    node_key=node.key,
                    payload={
                        "fault_type": fault["type"],
                        "scenario_id": options.scenario.id if options.scenario else None,
                        "parameters": fault.get("parameters", {}),
                    },
                )
                if options.scenario is not None and options.scenario.status == "INJECTING":
                    options.scenario.status = "OBSERVING"
                fault_announced = True

            state_before = invoice.snapshot(state)
            idempotency_key = self._idempotency_key(run, node)
            node_state.idempotency_key = idempotency_key
            context = HandlerContext(
                node=node,
                state=state,
                attempt=attempt,
                fault=fault,
                state_before=state_before,
                run_id=run.id,
                idempotency_key=idempotency_key,
                browser=browser,
            )
            result = self.handlers.get(node.node_type).execute(context)
            evidence_ids = self._save_screenshot(run, result)

            elapsed_ms = max(int((self.clock.now() - started).total_seconds() * 1000), 0)
            if result.outcome == "PASSED" and elapsed_ms > node.timeout_ms:
                result = HandlerResult(
                    "UNKNOWN",
                    detail=f"handler exceeded {node.timeout_ms} ms timeout",
                    error_class=ErrorClass.TIMEOUT,
                    error_code="ACTION_TIMED_OUT",
                )

            if result.outcome == "UNKNOWN":
                self.audit.append(
                    run.id,
                    EventType.ACTION_UNKNOWN,
                    action_ordinal=node.ordinal,
                    node_key=node.key,
                    payload=self._safe(
                        {
                            "detail": result.detail,
                            "error_class": str(result.error_class or ErrorClass.AMBIGUOUS_OUTCOME),
                            "attempt": attempt,
                        }
                    ),
                )
                probed, probe_detail = self._reconcile(node, state, state_before, fault, browser)
                if not probed:
                    self.audit.append(
                        run.id,
                        EventType.ACTION_FAILED,
                        action_ordinal=node.ordinal,
                        node_key=node.key,
                        payload=self._safe({"probe": probe_detail, "effect_present": False}),
                    )
                    node_state.status = str(NodeStatus.BLOCKED)
                    node_state.ended_at = self.clock.now()
                    node_state.duration_ms = elapsed_ms
                    node_state.error_class = ErrorClass.AMBIGUOUS_OUTCOME.value
                    node_state.error_code = "ACTION_OUTCOME_UNKNOWN"
                    node_state.error_message = (
                        "The action may have executed, but a read-only probe could not confirm the "
                        "effect. It was not retried."
                    )
                    self._save_checkpoint(
                        run, state_before, node.ordinal, CheckpointKind.PRE_ACTION, node.key
                    )
                    # The attempted state is captured too, so recovery can inspect
                    # what the attempt actually did instead of re-executing it.
                    self._save_checkpoint(
                        run, state, node.ordinal, CheckpointKind.ATTEMPT_STATE, node.key
                    )
                    self.audit.append(
                        run.id,
                        EventType.NODE_BLOCKED,
                        action_ordinal=node.ordinal,
                        node_key=node.key,
                        payload={
                            "error_class": node_state.error_class,
                            "detail": node_state.error_message,
                        },
                    )
                    run.status = str(RunStatus.AWAITING_RECOVERY)
                    run.verdict = str(RunVerdict.PENDING)
                    run.failure_code = "ACTION_OUTCOME_UNKNOWN"
                    run.failure_message = node_state.error_message
                    run.failure_ordinal = node.ordinal
                    run.current_ordinal = node.ordinal
                    return "STOP"

                self.audit.append(
                    run.id,
                    EventType.ACTION_CONFIRMED,
                    action_ordinal=node.ordinal,
                    node_key=node.key,
                    payload=self._safe({"probe": probe_detail, "effect_present": True}),
                )
                self.audit.append(
                    run.id,
                    EventType.NODE_RECONCILED,
                    action_ordinal=node.ordinal,
                    node_key=node.key,
                    payload={"probe": probe_detail, "retried": False},
                )
                node_state.reconciled = True
                failed = evaluate_postconditions(node, state)
                if failed:
                    return self._finish_failure(
                        run,
                        node,
                        node_state,
                        state,
                        options,
                        graph=graph,
                        active_recovery=active_recovery,
                        error_class=ErrorClass.POSTCONDITION,
                        code="POSTCONDITION_FAILED",
                        message=failed[0],
                        retryable=False,
                    )
                result = HandlerResult(
                    "PASSED",
                    detail="reconciled after ambiguous outcome",
                    output={"reconciled": True, "probe": probe_detail},
                )

            if result.outcome == "BLOCKED":
                node_state.status = str(NodeStatus.BLOCKED)
                node_state.ended_at = self.clock.now()
                node_state.error_code = result.error_code or "BLOCKED"
                node_state.error_message = result.detail
                run.status = str(RunStatus.AWAITING_RECOVERY)
                run.failure_code = node_state.error_code
                run.failure_message = result.detail
                run.failure_ordinal = node.ordinal
                self.audit.append(
                    run.id,
                    EventType.NODE_BLOCKED,
                    action_ordinal=node.ordinal,
                    node_key=node.key,
                    payload={"detail": result.detail, "error_code": node_state.error_code},
                )
                return "STOP"

            if result.outcome == "FAILED":
                retry_allowed = (
                    attempt < policy["max_attempts"]
                    and result.retryable
                    and node.side_effect_class in _RETRYABLE_SIDE_EFFECTS
                    and (
                        not policy["retry_on"]
                        or str(result.error_class or ErrorClass.HANDLER_ERROR) in policy["retry_on"]
                    )
                )
                if retry_allowed:
                    self.audit.append(
                        run.id,
                        EventType.NODE_RETRY_SCHEDULED,
                        action_ordinal=node.ordinal,
                        node_key=node.key,
                        payload={
                            "attempt": attempt,
                            "next_attempt": attempt + 1,
                            "max_attempts": policy["max_attempts"],
                            "backoff_ms": policy["backoff_ms"],
                            "error_class": str(result.error_class or ErrorClass.HANDLER_ERROR),
                            "detail": result.detail,
                            "idempotency_key": idempotency_key,
                        },
                    )
                    continue

                exhausted = result.retryable and policy["max_attempts"] > 1
                return self._finish_failure(
                    run,
                    node,
                    node_state,
                    state,
                    options,
                    graph=graph,
                    active_recovery=active_recovery,
                    error_class=ErrorClass.RETRY_EXHAUSTED
                    if exhausted
                    else (result.error_class or ErrorClass.HANDLER_ERROR),
                    code="RETRY_EXHAUSTED" if exhausted else (result.error_code or "NODE_FAILED"),
                    message=result.detail,
                    retryable=False,
                )

            # PASSED
            output_errors = validate_schema(
                result.output,
                getattr(node.action, "output_schema", None),
                path=f"nodes.{node.key}.output",
            )
            if output_errors:
                return self._finish_failure(
                    run,
                    node,
                    node_state,
                    state,
                    options,
                    graph=graph,
                    active_recovery=active_recovery,
                    error_class=ErrorClass.OUTPUT_SCHEMA,
                    code="OUTPUT_SCHEMA_VALIDATION_FAILED",
                    message="; ".join(output_errors),
                    retryable=False,
                )

            node_state.status = str(NodeStatus.PASSED)
            node_state.ended_at = self.clock.now()
            node_state.duration_ms = elapsed_ms
            output = dict(result.output)
            if evidence_ids:
                output["evidence_ids"] = evidence_ids
            node_state.output = redact(
                output,
                [*self._redact_paths, *(getattr(node.action, "redact_paths", None) or [])],
            )
            run.current_ordinal = node.ordinal + 1
            state.setdefault("node_outputs", {})[node.key] = copy.deepcopy(node_state.output)

            if node.node_type == NodeType.BRANCH and result.route_outcome:
                route_selected[node.key] = graph_target = node.branches.get(
                    result.route_outcome, ""
                )
                if graph_target:
                    self.audit.append(
                        run.id,
                        EventType.NODE_PASSED,
                        action_ordinal=node.ordinal,
                        node_key=node.key,
                        payload=self._safe(
                            {
                                "node_type": node.node_type.value,
                                "outcome": result.route_outcome,
                                "selected": graph_target,
                                "duration_ms": elapsed_ms,
                                "attempt": attempt,
                            }
                        ),
                    )
                else:
                    return self._finish_failure(
                        run,
                        node,
                        node_state,
                        state,
                        options,
                        graph=graph,
                        active_recovery=active_recovery,
                        error_class=ErrorClass.VALIDATION,
                        code="BRANCH_TARGET_MISSING",
                        message=f"Branch outcome {result.route_outcome!r} has no target.",
                        retryable=False,
                    )
            elif node.node_type == NodeType.ACTION:
                self.audit.append(
                    run.id,
                    EventType.ACTION_SUCCEEDED,
                    action_ordinal=node.ordinal,
                    node_key=node.key,
                    payload=self._safe(
                        {
                            "kind": str(getattr(node.action, "kind", "")),
                            "label": node.label,
                            "detail": result.detail,
                            "gated": bool(getattr(node.action, "requires_authorization", False)),
                            "authorized": authorized,
                            "duration_ms": elapsed_ms,
                            "attempt": attempt,
                            "idempotency_key": idempotency_key,
                            "reconciled": node_state.reconciled,
                        }
                    ),
                )
            else:
                self.audit.append(
                    run.id,
                    EventType.NODE_PASSED,
                    action_ordinal=node.ordinal,
                    node_key=node.key,
                    payload=self._safe(
                        {
                            "node_type": node.node_type.value,
                            "label": node.label,
                            "detail": result.detail,
                            "duration_ms": elapsed_ms,
                            "attempt": attempt,
                        }
                    ),
                )

            if node.node_type in {NodeType.ACTION, NodeType.CHECKPOINT}:
                checkpoint = self._save_checkpoint(
                    run, state, node.ordinal, CheckpointKind.POST_ACTION, node.key
                )
                node_state.checkpoint_id = checkpoint.id

            if node.node_type == NodeType.RECOVERY_HANDLER:
                self._mark_recovered(graph=graph, states=states, recovery_key=node.key, run=run)

            return "CONTINUE"

    def _finish_failure(
        self,
        run: ExecutionRun,
        node: GraphNode,
        node_state: ExecutionNodeState,
        state: dict[str, Any],
        options: ExecuteOptions,
        *,
        graph: WorkflowGraph | None,
        active_recovery: set[str],
        error_class: ErrorClass,
        code: str,
        message: str,
        retryable: bool,
    ) -> str:
        now = self.clock.now()
        node_state.ended_at = now
        node_state.duration_ms = max(
            int((now - (node_state.started_at or now)).total_seconds() * 1000), 0
        )
        node_state.error_class = error_class.value
        node_state.error_code = code
        node_state.error_message = message
        self.audit.append(
            run.id,
            EventType.NODE_FAILED,
            action_ordinal=node.ordinal,
            node_key=node.key,
            payload={
                "error_class": error_class.value,
                "code": code,
                "detail": message,
                "retryable": retryable,
                "attempts": node_state.attempt,
            },
        )

        handler = active_failure_handler(node.failure_handler)
        strategy = str(handler.get("strategy", "FAIL")).upper()
        target = str(handler.get("target") or "")

        if strategy == "SKIP":
            node_state.status = str(NodeStatus.SKIPPED)
            self.audit.append(
                run.id,
                EventType.NODE_SKIPPED,
                action_ordinal=node.ordinal,
                node_key=node.key,
                payload={"reason": "failure_handler_skip", "detail": message},
            )
            return "CONTINUE"

        if strategy in {"GOTO", "RECOVERY"} and target and graph is not None:
            active_recovery.add(target)
            node_state.status = str(NodeStatus.FAILED)
            self.audit.append(
                run.id,
                EventType.NODE_BLOCKED,
                action_ordinal=node.ordinal,
                node_key=node.key,
                payload={"strategy": strategy, "recovery_target": target},
            )
            return "CONTINUE"

        node_state.status = str(NodeStatus.FAILED)
        if strategy == "RECOVERY":
            self._save_checkpoint(run, state, node.ordinal, CheckpointKind.PRE_ACTION, node.key)
            run.status = str(RunStatus.AWAITING_RECOVERY)
            run.verdict = str(RunVerdict.PENDING)
            run.failure_code = code
            run.failure_message = message
            run.failure_ordinal = node.ordinal
            run.current_ordinal = node.ordinal
            self.audit.append(
                run.id,
                EventType.NODE_BLOCKED,
                action_ordinal=node.ordinal,
                node_key=node.key,
                payload={"error_class": error_class.value, "detail": "awaiting recovery"},
            )
            return "STOP"

        self._fail(run, code, message, node.ordinal)
        return "STOP"

    def _mark_recovered(
        self,
        *,
        graph: WorkflowGraph | None,
        states: dict[str, ExecutionNodeState],
        recovery_key: str,
        run: ExecutionRun,
    ) -> None:
        for key, state in states.items():
            node = graph.nodes.get(key) if graph else None
            if node is None:
                continue
            handler = active_failure_handler(node.failure_handler)
            target = str(handler.get("target") or "")
            if target == recovery_key and state.status == NodeStatus.FAILED.value:
                state.status = str(NodeStatus.RECOVERED)
                state.error_message = f"Recovered by {recovery_key}."
                self.audit.append(
                    run.id,
                    EventType.NODE_RECOVERED,
                    action_ordinal=state.ordinal,
                    node_key=key,
                    payload={"recovery_node": recovery_key},
                )

    def _skip_node(
        self,
        run: ExecutionRun,
        node: GraphNode,
        node_state: ExecutionNodeState,
        *,
        code: str,
        message: str,
    ) -> None:
        node_state.status = str(NodeStatus.SKIPPED)
        node_state.ended_at = self.clock.now()
        node_state.error_class = ErrorClass.DEPENDENCY_FAILED.value
        node_state.error_code = code
        node_state.error_message = message
        self.audit.append(
            run.id,
            EventType.NODE_SKIPPED,
            action_ordinal=node.ordinal,
            node_key=node.key,
            payload={"reason": code, "detail": message, "node_type": node.node_type.value},
        )

    def _reconcile(
        self,
        node: GraphNode,
        state: dict[str, Any],
        state_before: dict[str, Any],
        fault: dict[str, Any] | None,
        browser: BrowserSession | None = None,
    ) -> tuple[bool, str]:
        action_input = dict(getattr(node.action, "input", None) or {})
        if str(getattr(node.action, "channel", "")) == "BROWSER":
            if browser is None:
                return False, "no browser session is available for a read-only probe"
            ok, detail, snapshot = browser.probe(
                target=dict(getattr(node.action, "target", None) or {}),
                action_input=action_input,
                inputs=dict(state.get("inputs") or {}),
            )
            if snapshot:
                state["page"] = snapshot
            return ok, detail
        return invoice.probe(action_input, state, state_before, fault=fault)

    def _save_screenshot(self, run: ExecutionRun, result: HandlerResult) -> list[str]:
        if not result.screenshot:
            return []
        try:
            record = self.evidence.write_bytes(
                owner_id=run.id,
                kind=EvidenceKind.SCREENSHOT,
                content=result.screenshot,
                mime_type="image/png",
                extension="png",
            )
            self.uow.evidence.add(record)
            return [record.id]
        except Exception:
            logger.exception("screenshot evidence could not be stored")
            return []

    def _emit_node_started(
        self,
        run: ExecutionRun,
        node: GraphNode,
        node_state: ExecutionNodeState,
        *,
        retry: bool = False,
    ) -> None:
        event_type = (
            EventType.ACTION_STARTED
            if node.node_type == NodeType.ACTION
            else EventType.NODE_STARTED
        )
        self.audit.append(
            run.id,
            event_type,
            action_ordinal=node.ordinal,
            node_key=node.key,
            payload=self._safe(
                {
                    "kind": str(getattr(node.action, "kind", ""))
                    if node.action
                    else node.node_type.value,
                    "label": node.label,
                    "node_type": node.node_type.value,
                    "attempt": node_state.attempt,
                    "retry": retry,
                    "channel": str(getattr(node.action, "channel", "SIMULATED"))
                    if node.action
                    else None,
                }
            ),
        )

    def _request_decision(
        self,
        run: ExecutionRun,
        node: GraphNode,
        state: dict[str, Any],
        options: ExecuteOptions,
    ) -> HumanDecision:
        """Persist the blocker, its evidence packet and the smallest question.

        Detection is declarative and evidence is resolved from persisted state and
        events, so the question can be traced back to facts rather than prose.
        """
        now = self.clock.now()
        delayed = (
            options.scenario is not None
            and options.scenario.fault_type == "DELAYED_APPROVAL"
            and options.scenario.target_ordinal == node.ordinal
        )
        delay_seconds = 0
        if delayed and options.scenario is not None:
            delay_seconds = int((options.scenario.parameters or {}).get("delay_seconds", 0))
        events = list(self.uow.events.read(run.id))
        findings = blocker_service.detect_blockers(
            node,
            state,
            run,
            now=now,
            actor_role=options.actor_role,
            events=events,
        )
        action_input = dict(getattr(node.action, "input", None) or {})
        raw_template = action_input.get("decision")
        template: dict[str, Any] = raw_template if isinstance(raw_template, dict) else {}
        if not findings:
            findings = [
                blocker_service.BlockerFinding(
                    kind=blocker_service.BlockerKind.HUMAN_APPROVAL_REQUIRED,
                    code="HUMAN_APPROVAL_REQUIRED",
                    title=f"Authorize: {node.label}",
                    detail="This node needs a human decision before it can run.",
                    node_key=node.key,
                    ordinal=node.ordinal,
                    required_role=blocker_service.BLOCKER_DEFAULT_ROLE[
                        blocker_service.BlockerKind.HUMAN_APPROVAL_REQUIRED
                    ],
                    question=f"Authorize '{node.label}' to continue this run?",
                    options=tuple(template.get("options") or []),
                )
            ]

        finding = findings[0]
        blocker, evidence_rows = blocker_service.persist_blocker(
            self.uow,
            self.ids,
            self.clock,
            run=run,
            finding=finding,
            state=state,
            events=events,
        )
        for extra in findings[1:]:
            sibling, _siblings = blocker_service.persist_blocker(
                self.uow,
                self.ids,
                self.clock,
                run=run,
                finding=extra,
                state=state,
                events=events,
            )
            sibling.detail = f"{sibling.detail} (secondary blocker; primary is {blocker.kind})"

        declared_options = [dict(item) for item in finding.options if isinstance(item, dict)]
        values = {str(item.get("value")) for item in declared_options}
        if "approve" not in values:
            declared_options.append(
                {
                    "value": "approve",
                    "label": "Approve",
                    "consequence": "Re-evaluates preconditions and resumes the run",
                }
            )
        if "reject" not in values and "deny" not in values:
            declared_options.append(
                {
                    "value": "reject",
                    "label": "Reject",
                    "consequence": "Stops the run; the node never executes",
                }
            )
        if finding.input_spec and "provide_info" not in values:
            declared_options.append(
                {
                    "value": "provide_info",
                    "label": "Provide the missing information",
                    "consequence": "Writes the supplied values and re-checks preconditions",
                }
            )
        if "defer" not in values:
            declared_options.append(
                {
                    "value": "defer",
                    "label": "Defer",
                    "consequence": "Keeps the run blocked and records why",
                }
            )

        # A delayed-approval scenario shortens the window so "nobody approved in
        # time" is reproducible and deterministic.
        ttl_seconds = max(self.settings.decision_ttl_seconds - delay_seconds, 0)
        expires = now + timedelta(seconds=ttl_seconds)
        decision = HumanDecision(
            id=self.ids.new(),
            run_id=run.id,
            action_ordinal=node.ordinal,
            node_key=node.key,
            question=str(finding.question or f"Authorize '{node.label}' to continue this run?"),
            options=declared_options,
            recommended_option=finding.recommended,
            reason=str(finding.reason or finding.detail or "")
            + (
                f" Injected approval delay: {delay_seconds}s exceeds the "
                f"{self.settings.decision_ttl_seconds}s decision TTL."
                if delayed
                else ""
            ),
            policy_citation=str(finding.policy_citation or ""),
            status=str(DecisionStatus.NOTIFIED),
            origin=str(options.origin),
            seed_batch=options.seed_batch,
            requested_at=now,
            expires_at=expires,
            blocker_id=blocker.id,
            kind=str(finding.kind),
            required_role=str(finding.required_role),
            resume_conditions=list(finding.resume_conditions),
            evidence_hash=blocker.evidence_hash,
        )
        self.uow.decisions.add(decision)
        blocker.decision_id = decision.id

        evidence_packet = [
            {
                "evidence_id": row.id,
                "source_kind": row.source_kind,
                "source_path": row.source_path,
                "source_ref": row.source_ref,
                "label": row.label,
                "relation": row.relation,
                "value": row.value_json,
                "validation": row.validation,
                "detail": row.detail,
                "observed_at": row.observed_at.isoformat(),
                "fresh_until": row.fresh_until.isoformat() if row.fresh_until else None,
            }
            for row in evidence_rows
        ]
        bundle = {
            "decision_id": decision.id,
            "run_id": run.id,
            "blocker_id": blocker.id,
            "node_key": node.key,
            "blocker": {
                "kind": blocker.kind,
                "code": blocker.code,
                "title": blocker.title,
                "detail": blocker.detail,
                "required_role": blocker.required_role,
            },
            "question": decision.question,
            "evidence": evidence_packet,
            "evidence_hash": blocker.evidence_hash,
            "state_summary": self._safe(
                {"invoice": state.get("invoice"), "review": state.get("review")}
            ),
            "recent_events": [
                {"seq": item.seq, "type": item.type, "hash": item.hash} for item in events[-6:]
            ],
        }
        record = self.evidence.write_json(
            run_id=run.id, kind=EvidenceKind.DECISION_BUNDLE, payload=bundle
        )
        self.uow.evidence.add(record)

        run.status = str(RunStatus.AWAITING_DECISION)
        run.current_ordinal = node.ordinal
        checkpoint = self._save_checkpoint(
            run, state, node.ordinal, CheckpointKind.PRE_ACTION, node.key
        )

        self.audit.append(
            run.id,
            EventType.BLOCKER_DETECTED,
            action_ordinal=node.ordinal,
            node_key=node.key,
            payload=self._safe(
                {
                    "blocker_id": blocker.id,
                    "kind": blocker.kind,
                    "code": blocker.code,
                    "title": blocker.title,
                    "required_role": blocker.required_role,
                    "evidence_hash": blocker.evidence_hash,
                    "evidence_count": len(evidence_rows),
                }
            ),
        )
        event = self.audit.append(
            run.id,
            EventType.DECISION_REQUESTED,
            action_ordinal=node.ordinal,
            node_key=node.key,
            payload=self._safe(
                {
                    "decision_id": decision.id,
                    "blocker_id": blocker.id,
                    "kind": blocker.kind,
                    "required_role": blocker.required_role,
                    "question": decision.question,
                    "expires_at": expires.isoformat(),
                    "evidence_id": record.id,
                    "evidence_hash": blocker.evidence_hash,
                    "policy_citation": decision.policy_citation,
                    "checkpoint_id": checkpoint.id,
                }
            ),
        )
        record.event_id = event.id
        if delayed and options.scenario is not None:
            options.scenario.status = "OBSERVING"
            self.audit.append(
                run.id,
                EventType.FAULT_INJECTED,
                action_ordinal=node.ordinal,
                node_key=node.key,
                payload={
                    "scenario_id": options.scenario.id,
                    "fault_type": "DELAYED_APPROVAL",
                    "delay_seconds": delay_seconds,
                    "expires_at": expires.isoformat(),
                    "note": "a human approval after this delay cannot be accepted",
                },
            )
        return decision

    def _fault_for(
        self,
        run: ExecutionRun,
        node: GraphNode,
        scenario: FaultScenario | None,
    ) -> dict[str, Any] | None:
        if scenario is None or scenario.target_ordinal != node.ordinal:
            return None
        already = [
            item for item in self.uow.events.read(run.id) if item.type == EventType.FAULT_INJECTED
        ]
        if any(
            item.action_ordinal == node.ordinal and (item.node_key in {None, node.key})
            for item in already
        ):
            return None
        return {"type": scenario.fault_type, "parameters": scenario.parameters or {}}

    def _idempotency_key(self, run: ExecutionRun, node: GraphNode) -> str | None:
        if (
            node.node_type != NodeType.ACTION
            or node.side_effect_class not in _IDEMPOTENT_SIDE_EFFECTS
        ):
            return None
        material = {
            "run_id": run.id,
            "node_key": node.key,
            "input": getattr(node.action, "input", {}) or {},
        }
        digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
        return f"idem_{digest[:40]}"

    def _cancellation_requested(self, run: ExecutionRun) -> bool:
        if run.status == RunStatus.CANCELLED:
            return True
        return bool(self.cancellations is not None and self.cancellations.is_requested(run.id))

    def _cancel_execution(
        self,
        run: ExecutionRun,
        graph: WorkflowGraph,
        states: dict[str, ExecutionNodeState],
        options: ExecuteOptions,
    ) -> None:
        now = self.clock.now()
        reason = (
            self.cancellations.reason(run.id)
            if self.cancellations is not None
            else "cancelled by operator"
        )
        run.status = str(RunStatus.CANCELLED)
        run.verdict = str(RunVerdict.CANCELLED)
        run.ended_at = now
        run.failure_code = "CANCELLED"
        run.failure_message = reason
        for state in states.values():
            if state.status in {
                NodeStatus.PENDING.value,
                NodeStatus.RUNNING.value,
                NodeStatus.BLOCKED.value,
            }:
                state.status = str(NodeStatus.CANCELLED)
                state.ended_at = now
                state.error_class = ErrorClass.CANCELLED.value
                state.error_code = "RUN_CANCELLED"
                state.error_message = reason
        self.audit.append(
            run.id,
            EventType.RUN_CANCELLED,
            actor=ActorKind.HUMAN,
            actor_id=options.actor,
            payload={"reason": reason, "safe_interruption": True},
        )
        if self.cancellations is not None:
            self.cancellations.clear(run.id)

    # --------------------------------------------------------------- lifecycle

    def _verify(
        self,
        run: ExecutionRun,
        invariants: Sequence[Invariant],
        state: dict[str, Any],
        options: ExecuteOptions,
        version: WorkflowVersion,
    ) -> None:
        run.status = str(RunStatus.VERIFYING)
        events = list(self.uow.events.read(run.id))
        results: list[InvariantResult] = [
            evaluate_invariant(invariant, state, events) for invariant in invariants
        ]
        for result in results:
            self.uow.verdicts.add(
                InvariantVerdictRecord(
                    id=self.ids.new(),
                    run_id=run.id,
                    invariant_id=result.invariant_id,
                    invariant_name=result.name,
                    severity=result.severity,
                    verdict=str(result.verdict),
                    detail=result.detail,
                    observed=result.observed,
                )
            )
            self.audit.append(
                run.id,
                EventType.INVARIANT_EVALUATED,
                payload={
                    "invariant_id": result.invariant_id,
                    "name": result.name,
                    "severity": result.severity,
                    "verdict": str(result.verdict),
                    "detail": result.detail,
                },
            )

        node_outputs = {
            item.node_key: item.output
            for item in self.uow.node_states.list(run_id=run.id)
            if item.output
        }
        run.outputs = redact(node_outputs, self._redact_paths)
        output_errors = validate_schema(
            run.outputs, getattr(version, "output_schema", None), path="run.outputs"
        )
        if output_errors:
            run.ended_at = self.clock.now()
            run.status = str(RunStatus.FAILED)
            run.verdict = str(RunVerdict.FAILED)
            run.failure_code = "OUTPUT_SCHEMA_VALIDATION_FAILED"
            run.failure_message = "; ".join(output_errors)
            self.audit.append(
                run.id,
                EventType.RUN_FAILED,
                payload={
                    "code": run.failure_code,
                    "detail": run.failure_message,
                    "outputs": run.outputs,
                },
            )
            return

        self.audit.append(
            run.id,
            EventType.RUN_OUTPUTS_RECORDED,
            payload=self._safe({"outputs": run.outputs}),
        )

        verdict = aggregate([item.verdict for item in results])
        run.ended_at = self.clock.now()
        if verdict == InvariantVerdict.SATISFIED:
            run.status = str(RunStatus.SUCCEEDED)
            run.verdict = str(RunVerdict.VERIFIED)
            self.audit.append(
                run.id,
                EventType.RUN_VERIFIED,
                actor=ActorKind.POLICY,
                actor_id="invariant-evaluator",
                payload={"invariants": len(results), "verdict": str(verdict)},
            )
        else:
            run.status = str(RunStatus.FAILED)
            run.verdict = str(RunVerdict.FAILED)
            run.failure_code = f"INVARIANT_{verdict}"
            violated = [item for item in results if item.verdict == InvariantVerdict.VIOLATED]
            run.failure_message = (
                f"{len(violated)} invariant(s) violated: "
                + ", ".join(item.name for item in violated[:3])
                if violated
                else "Invariant evaluation was inconclusive."
            )
            self.audit.append(
                run.id,
                EventType.RUN_FAILED,
                actor=ActorKind.POLICY,
                actor_id="invariant-evaluator",
                payload={
                    "code": run.failure_code,
                    "detail": run.failure_message,
                    "verdicts": {item.name: str(item.verdict) for item in results},
                },
            )

        if options.scenario is not None:
            options.scenario.status = "COMPLETED"

    def _fail(self, run: ExecutionRun, code: str, message: str, ordinal: int | None = None) -> None:
        run.status = str(RunStatus.FAILED)
        run.verdict = str(RunVerdict.FAILED)
        run.failure_code = code
        run.failure_message = message
        run.failure_ordinal = ordinal
        run.ended_at = self.clock.now()
        self.audit.append(
            run.id,
            EventType.RUN_FAILED,
            action_ordinal=ordinal,
            payload={"code": code, "detail": message},
        )

    def _time_out(self, run: ExecutionRun, detail: str) -> None:
        run.status = str(RunStatus.TIMED_OUT)
        run.verdict = str(RunVerdict.TIMED_OUT)
        run.failure_code = "RUN_TIMED_OUT"
        run.failure_message = detail
        run.ended_at = self.clock.now()
        self.audit.append(run.id, EventType.RUN_TIMED_OUT, payload={"detail": detail})

    def _save_checkpoint(
        self,
        run: ExecutionRun,
        state: dict[str, Any],
        ordinal: int,
        kind: CheckpointKind,
        node_key: str | None = None,
    ) -> Checkpoint:
        snapshot = redact_state(state, self._redact_paths)
        snapshot["__engine__"] = {
            "node_key": node_key,
            "node_states": {
                item.node_key: item.status for item in self.uow.node_states.list(run_id=run.id)
            },
        }
        body = canonical_json(snapshot)
        last = self.uow.events.last(run.id)
        checkpoint = Checkpoint(
            id=self.ids.new(),
            run_id=run.id,
            event_seq=last.seq if last else 0,
            action_ordinal=ordinal,
            kind=str(kind),
            state_json=snapshot,
            state_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            created_at=self.clock.now(),
        )
        self.uow.checkpoints.add(checkpoint)
        self.audit.append(
            run.id,
            EventType.CHECKPOINT_SAVED,
            action_ordinal=ordinal,
            node_key=node_key,
            payload={
                "checkpoint_id": checkpoint.id,
                "kind": str(kind),
                "state_hash": checkpoint.state_hash,
            },
        )
        return checkpoint

    def _safe(self, payload: dict[str, Any], node: GraphNode | None = None) -> dict[str, Any]:
        paths = list(self._redact_paths)
        if node is not None:
            paths.extend(getattr(node.action, "redact_paths", None) or [])
        return redact(payload, paths)

    # --------------------------------------------------------------- utilities

    def latest_checkpoint(
        self,
        run_id: str,
        *,
        upto_ordinal: int | None = None,
        kind: CheckpointKind | None = None,
    ) -> Checkpoint | None:
        rows = [
            item
            for item in self.uow.checkpoints.list(
                run_id=run_id, order_by="action_ordinal", descending=True
            )
            if (upto_ordinal is None or item.action_ordinal <= upto_ordinal)
            and (kind is None or item.kind == kind)
        ]
        rows.sort(key=lambda item: (item.action_ordinal, item.event_seq), reverse=True)
        return rows[0] if rows else None

    def checkpoint_at(self, run_id: str, ordinal: int, kind: CheckpointKind) -> Checkpoint | None:
        rows = [
            item
            for item in self.uow.checkpoints.list(run_id=run_id)
            if item.action_ordinal == ordinal and item.kind == kind
        ]
        rows.sort(key=lambda item: item.event_seq, reverse=True)
        return rows[0] if rows else None

    def restore_state(self, checkpoint: Checkpoint) -> dict[str, Any]:
        body = canonical_json(checkpoint.state_json)
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != checkpoint.state_hash:
            raise ValueError("checkpoint state hash mismatch")
        restored = invoice.snapshot(checkpoint.state_json)
        # Engine metadata is stored alongside the sandbox state but is not part
        # of the state a handler should see.
        restored.pop("__engine__", None)
        return restored

    @staticmethod
    def resume_ordinal(checkpoint: Checkpoint) -> int:
        if checkpoint.kind == CheckpointKind.POST_ACTION:
            return checkpoint.action_ordinal + 1
        return checkpoint.action_ordinal


def event_summary(events: Sequence[ExecutionEvent]) -> list[dict[str, Any]]:
    return [
        {
            "seq": item.seq,
            "type": item.type,
            "actor": item.actor,
            "actor_id": item.actor_id,
            "action_ordinal": item.action_ordinal,
            "node_key": item.node_key,
            "recorded_at": item.recorded_at,
            "hash": item.hash,
        }
        for item in events
    ]
