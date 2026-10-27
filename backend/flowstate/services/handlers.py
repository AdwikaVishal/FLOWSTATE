"""Typed node handlers.

There is no arbitrary code generation anywhere in the engine. Each node type
maps to one registered handler; unknown types fail validation before a run is
created. Handlers may read and update the sandbox state, but the engine owns
transitions, events, retries and checkpoints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from flowstate.domain.conditions import evaluate_all, evaluate_condition, resolve_path
from flowstate.domain.enums import ErrorClass, NodeType
from flowstate.domain.graph import GraphNode
from flowstate.domain.schema_validation import validate_schema
from flowstate.sandbox import invoice


@dataclass
class HandlerContext:
    node: GraphNode
    state: dict[str, Any]
    attempt: int
    fault: dict[str, Any] | None
    state_before: dict[str, Any]
    run_id: str
    idempotency_key: str | None = None
    browser: Any = None


@dataclass
class HandlerResult:
    outcome: str  # PASSED | FAILED | UNKNOWN | BLOCKED
    detail: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    error_class: ErrorClass | None = None
    error_code: str | None = None
    retryable: bool = False
    requires_decision: bool = False
    route_outcome: str | None = None
    screenshot: bytes | None = None


class NodeHandler(Protocol):
    node_type: NodeType

    def execute(self, context: HandlerContext) -> HandlerResult: ...


def _failed(
    message: str,
    *,
    error_class: ErrorClass,
    code: str,
    retryable: bool = False,
) -> HandlerResult:
    return HandlerResult(
        "FAILED",
        detail=message,
        error_class=error_class,
        error_code=code,
        retryable=retryable,
    )


def evaluate_postconditions(node: GraphNode, state: dict[str, Any]) -> list[str]:
    checks = evaluate_all(getattr(node.action, "postconditions", None), state)
    return [item.detail for item in checks if not item.ok]


def evaluate_preconditions(node: GraphNode, state: dict[str, Any]) -> list[str]:
    checks = evaluate_all(getattr(node.action, "preconditions", None), state)
    return [item.detail for item in checks if not item.ok]


class StartHandler:
    node_type = NodeType.START

    def execute(self, context: HandlerContext) -> HandlerResult:
        return HandlerResult("PASSED", detail="graph start", output={"started": True})


class EndHandler:
    node_type = NodeType.END

    def execute(self, context: HandlerContext) -> HandlerResult:
        return HandlerResult("PASSED", detail="graph end", output={"completed": True})


class ActionHandler:
    """Runs an ACTION node through a typed, allow-listed channel driver.

    ``SIMULATED`` uses the in-process invoice sandbox. ``BROWSER`` uses the
    narrow Playwright driver. No other channel is registered, and no handler
    ever interprets generated code.
    """

    node_type = NodeType.ACTION

    def execute(self, context: HandlerContext) -> HandlerResult:
        action = context.node.action
        channel = str(getattr(action, "channel", "SIMULATED") or "SIMULATED")
        if channel == "BROWSER":
            return self._browser(context)
        action_input = dict(getattr(action, "input", None) or {})
        result = invoice.execute(
            action_input,
            context.state,
            fault=context.fault,
            attempt=context.attempt,
        )
        if result.outcome == invoice.OUTCOME_UNKNOWN:
            return HandlerResult(
                "UNKNOWN",
                detail=result.detail,
                error_class=ErrorClass(result.error_class or "TIMEOUT"),
                error_code=result.error_code or "ACTION_OUTCOME_UNKNOWN",
                retryable=False,
            )
        if result.outcome == invoice.OUTCOME_FAILED:
            return HandlerResult(
                "FAILED",
                detail=result.detail,
                error_class=ErrorClass(result.error_class or "HANDLER_ERROR"),
                error_code=result.error_code or "HANDLER_FAILED",
                retryable=result.retryable,
            )
        if result.missing_fields:
            return _failed(
                "The response omitted required field(s): " + ", ".join(result.missing_fields),
                error_class=ErrorClass.VALIDATION,
                code="MISSING_REQUIRED_FIELD",
            )
        failed = evaluate_postconditions(context.node, context.state)
        if failed:
            return _failed(
                failed[0],
                error_class=ErrorClass.POSTCONDITION,
                code="POSTCONDITION_FAILED",
            )
        return HandlerResult(
            "PASSED",
            detail=result.detail,
            output={
                "kind": str(getattr(action, "kind", "")),
                "channel": channel,
                "driver": result.driver,
                "effect": action_input.get("effects", []),
            },
        )

    def _browser(self, context: HandlerContext) -> HandlerResult:
        from flowstate.capture.browser import BrowserError

        action = context.node.action
        if context.browser is None:
            return _failed(
                "This workflow needs a browser driver, but none is configured.",
                error_class=ErrorClass.HANDLER_ERROR,
                code="BROWSER_DRIVER_UNAVAILABLE",
            )
        action_input = dict(getattr(action, "input", None) or {})
        target = dict(getattr(action, "target", None) or {})
        fault_type = str((context.fault or {}).get("type", ""))
        if fault_type == "SELECTOR_DRIFT":
            if str(action_input.get("action", "")).upper() == "NAVIGATE":
                url = str(target.get("url") or "")
                if url and "variant=changed" not in url:
                    target["url"] = f"{url}{'&' if '?' in url else '?'}variant=changed"
            else:
                try:
                    context.browser.apply_selector_drift()
                except Exception as exc:
                    return _failed(
                        f"The selector-drift fault could not be applied: {exc}",
                        error_class=ErrorClass.HANDLER_ERROR,
                        code="FAULT_INJECTION_FAILED",
                    )
        elif fault_type:
            return _failed(
                f"Fault {fault_type} is not supported for the browser channel.",
                error_class=ErrorClass.VALIDATION,
                code="FAULT_UNSUPPORTED_FOR_BROWSER",
            )
        try:
            outcome = context.browser.perform(
                target=target,
                action_input=action_input,
                inputs=dict(context.state.get("inputs") or {}),
                timeout_ms=context.node.timeout_ms,
            )
        except BrowserError as exc:
            return _failed(str(exc), error_class=ErrorClass.HANDLER_ERROR, code=exc.code)
        if outcome.page:
            context.state["page"] = outcome.page
        output = {
            "kind": str(getattr(action, "kind", "")),
            "channel": "BROWSER",
            "driver": context.browser.driver_name,
            "action": action_input.get("action"),
            "locator": outcome.locator,
            "matches": outcome.matches,
            "page": outcome.page,
            "duration_ms": outcome.duration_ms,
        }
        if outcome.outcome == "UNKNOWN":
            return HandlerResult(
                "UNKNOWN",
                detail=outcome.detail,
                output=output,
                error_class=outcome.error_class or ErrorClass.AMBIGUOUS_OUTCOME,
                error_code=outcome.error_code or "ACTION_OUTCOME_UNKNOWN",
                retryable=False,
                screenshot=outcome.screenshot,
            )
        if outcome.outcome == "FAILED":
            return HandlerResult(
                "FAILED",
                detail=outcome.detail,
                output=output,
                error_class=outcome.error_class or ErrorClass.HANDLER_ERROR,
                error_code=outcome.error_code or "BROWSER_ACTION_FAILED",
                retryable=False,
                screenshot=outcome.screenshot,
            )
        failed = evaluate_postconditions(context.node, context.state)
        if failed:
            return HandlerResult(
                "FAILED",
                detail=failed[0],
                output=output,
                error_class=ErrorClass.POSTCONDITION,
                error_code="POSTCONDITION_FAILED",
                retryable=False,
                screenshot=outcome.screenshot,
            )
        return HandlerResult(
            "PASSED",
            detail=outcome.detail,
            output=output,
            screenshot=outcome.screenshot,
        )


class ValidationHandler:
    node_type = NodeType.VALIDATION

    def execute(self, context: HandlerContext) -> HandlerResult:
        action_input = dict(getattr(context.node.action, "input", None) or {})
        schema = action_input.get("schema")
        if isinstance(schema, dict) and schema:
            path = str(action_input.get("path", ""))
            subject = resolve_path(context.state, path) if path else context.state
            errors = validate_schema(subject, schema, path=path or "state")
            if errors:
                return _failed(
                    "; ".join(errors),
                    error_class=ErrorClass.VALIDATION,
                    code="DATA_VALIDATION_FAILED",
                )
        failed = evaluate_postconditions(context.node, context.state)
        if failed:
            return _failed(
                failed[0],
                error_class=ErrorClass.VALIDATION,
                code="VALIDATION_FAILED",
            )
        return HandlerResult(
            "PASSED",
            detail="data validation passed",
            output={"validated": True, "path": action_input.get("path", "state")},
        )


class BranchHandler:
    node_type = NodeType.BRANCH

    def execute(self, context: HandlerContext) -> HandlerResult:
        action_input = dict(getattr(context.node.action, "input", None) or {})
        condition = action_input.get("condition") or action_input.get("when")
        if not isinstance(condition, dict):
            return _failed(
                "A branch node must declare an input.condition.",
                error_class=ErrorClass.VALIDATION,
                code="BRANCH_WITHOUT_CONDITION",
            )
        result = evaluate_condition(condition, context.state)
        outcome = "true" if result.ok else "false"
        return HandlerResult(
            "PASSED",
            detail=result.detail,
            output={"outcome": outcome, "condition": condition, "observed": result.observed},
            route_outcome=outcome,
        )


class HumanDecisionHandler:
    """Executes the gated action after a human has authorized this ordinal.

    A HUMAN_DECISION node generated from a learned financial step wraps the
    recorded action. Marking it passed without applying that action would both
    skip the effect and bypass the node's postconditions, so the decision
    handler delegates to the same typed action channel once authorization has
    been established by the engine.
    """

    node_type = NodeType.HUMAN_DECISION

    def execute(self, context: HandlerContext) -> HandlerResult:
        result = ActionHandler().execute(context)
        if result.outcome == "PASSED":
            result.output = {**result.output, "approved": True}
            result.detail = f"human decision approved; {result.detail}"
        return result


class CheckpointHandler:
    node_type = NodeType.CHECKPOINT

    def execute(self, context: HandlerContext) -> HandlerResult:
        return HandlerResult(
            "PASSED",
            detail="safe point reached",
            output={"checkpoint": "requested"},
        )


class RecoveryHandler:
    node_type = NodeType.RECOVERY_HANDLER

    def execute(self, context: HandlerContext) -> HandlerResult:
        action_input = dict(getattr(context.node.action, "input", None) or {})
        effects = action_input.get("effects")
        if isinstance(effects, list):
            invoice.apply_effects(
                context.state, [item for item in effects if isinstance(item, dict)]
            )
        probe = action_input.get("probe")
        if isinstance(probe, dict) and probe:
            result = evaluate_condition(probe, context.state)
            if not result.ok:
                return _failed(
                    f"recovery probe failed: {result.detail}",
                    error_class=ErrorClass.VALIDATION,
                    code="RECOVERY_PROBE_FAILED",
                )
        return HandlerResult(
            "PASSED",
            detail="recovery handler applied",
            output={"recovered": True, "effects": effects or []},
        )


class HandlerRegistry:
    def __init__(self) -> None:
        handlers: list[NodeHandler] = [
            StartHandler(),
            EndHandler(),
            ActionHandler(),
            ValidationHandler(),
            BranchHandler(),
            HumanDecisionHandler(),
            CheckpointHandler(),
            RecoveryHandler(),
        ]
        self._handlers = {handler.node_type: handler for handler in handlers}

    def get(self, node_type: NodeType) -> NodeHandler:
        return self._handlers[node_type]

    def supports(self, node_type: NodeType) -> bool:
        return node_type in self._handlers

    @property
    def registered(self) -> list[str]:
        return sorted(item.value for item in self._handlers)


DEFAULT_HANDLERS = HandlerRegistry()
