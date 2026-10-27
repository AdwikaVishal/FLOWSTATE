"""Controlled in-process sandbox driver for the invoice workflow.

This is the foundation driver. It executes the same declarative action contract
that a Playwright driver will implement later, against an isolated invoice
workspace. It is explicitly labelled ``simulated-v1`` everywhere it is stored so
no run can be mistaken for a browser execution.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from flowstate.domain.conditions import evaluate_condition

DRIVER_NAME = "simulated-v1"

OUTCOME_SUCCEEDED = "SUCCEEDED"
OUTCOME_UNKNOWN = "UNKNOWN"
OUTCOME_FAILED = "FAILED"


def _base_state() -> dict[str, Any]:
    return {
        "session": {"authenticated": True, "operator": "local-operator"},
        "queue": {"loaded": False, "count": 3},
        "invoice": {
            "id": "INV-2041",
            "vendor": "Northwind Supply",
            "amount_cents": 184500,
            "currency": "USD",
            "status": "SUBMITTED",
            "approver": None,
            "payment_reference": None,
            "ledger": [],
            "ledger_count": 0,
            "cost_center": "CC-100",
        },
        "purchase_order": {
            "id": "PO-8841",
            "vendor": "Northwind Supply",
            "amount_cents": 184500,
            "status": "OPEN",
            "cost_center": "CC-100",
        },
        "policy": {
            "id": "AP-2026-04",
            "expires_at": "2027-04-01T00:00:00Z",
            "max_auto_approve_cents": 100000,
            "renewal_reference": None,
        },
        "approvals": {"panel_open": False, "note": None},
        "review": {},
        "extracted": {},
    }


PROFILES = ("standard", "discrepancy", "missing_cost_center", "expired_policy")


def initial_state(profile: str = "standard") -> dict[str, Any]:
    """Deterministic starting state for a sandbox run.

    Profiles exist so the invoice discrepancy scenario can be reproduced without
    mutating a shared workspace. An unknown profile raises rather than silently
    falling back, so a typo cannot produce a passing run.
    """
    state = _base_state()
    if profile == "standard":
        return state
    if profile == "discrepancy":
        state["purchase_order"]["amount_cents"] = 180000
        return state
    if profile == "missing_cost_center":
        state["invoice"]["cost_center"] = None
        return state
    if profile == "expired_policy":
        state["policy"]["expires_at"] = "2026-01-01T00:00:00Z"
        return state
    raise ValueError(f"unknown sandbox profile {profile!r}")


@dataclass
class ActionResult:
    outcome: str
    detail: str
    driver: str = DRIVER_NAME
    error_class: str | None = None
    error_code: str | None = None
    retryable: bool = False
    missing_fields: list[str] | None = None


def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(state)


def _get_path(state: dict[str, Any], path: str) -> Any:
    cursor: Any = state
    for part in path.split("."):
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return None
    return cursor


def _set_path(state: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor: Any = state
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = copy.deepcopy(value)


def _append_path(state: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor: Any = state
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    bucket = cursor.setdefault(parts[-1], [])
    bucket.append(copy.deepcopy(value))


def _pop_path(state: dict[str, Any], path: str) -> Any:
    parts = str(path).split(".")
    cursor: Any = state
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    if not isinstance(cursor, dict):
        return None
    return cursor.pop(parts[-1], None)


def _apply_effect(state: dict[str, Any], effect: dict[str, Any]) -> None:
    op = effect.get("op")
    if op == "set":
        _set_path(state, str(effect["path"]), effect.get("value"))
    elif op == "append":
        _append_path(state, str(effect["path"]), effect.get("value"))
    elif op == "copy":
        source = effect.get("from", "")
        parts = source.split(".")
        copy_cursor: Any = state
        for part in parts:
            copy_cursor = copy_cursor.get(part) if isinstance(copy_cursor, dict) else None
        _set_path(state, str(effect["path"]), copy_cursor)
    elif op == "remove_last":
        remove_parts = str(effect["path"]).split(".")
        remove_cursor: Any = state
        for part in remove_parts[:-1]:
            remove_cursor = remove_cursor.setdefault(part, {})
        bucket = remove_cursor.get(remove_parts[-1])
        if isinstance(bucket, list) and bucket:
            bucket.pop()
    elif op == "decrement":
        parts = str(effect["path"]).split(".")
        cursor = state
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = int(cursor.get(parts[-1], 0)) - int(effect.get("value", 1))
    elif op == "subtract":
        left = _get_path(state, str(effect.get("left", "")))
        right = _get_path(state, str(effect.get("right", "")))
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            _set_path(state, str(effect["path"]), left - right)
    elif op == "increment":
        parts = str(effect["path"]).split(".")
        cursor = state
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = int(cursor.get(parts[-1], 0)) + int(effect.get("value", 1))
    # "noop" and unknown ops are ignored: an action with no effect is valid.


def _effects(action_input: dict[str, Any]) -> list[dict[str, Any]]:
    raw = action_input.get("effects")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def apply_effects(state: dict[str, Any], effects: list[dict[str, Any]]) -> None:
    for effect in effects:
        _apply_effect(state, effect)


def execute(
    action_input: dict[str, Any],
    state: dict[str, Any],
    *,
    fault: dict[str, Any] | None,
    attempt: int = 1,
) -> ActionResult:
    """Apply an action once, honouring an optional injected fault.

    Faults are split by the boundary they disturb:

    * request-phase faults never apply the effect (timeout before execution,
      dropped connection, rate limiting, transient dependency failure);
    * response-phase faults may apply the effect and then lose or corrupt the
      response, so the engine must reconcile with a read-only probe;
    * data faults apply the effect but leave a contradiction that an invariant
      must catch;
    * duplicate, partial and postcondition faults prove that acknowledged
      operations are not automatically correct.

    ``attempt`` is used only by deterministic retryable faults so a retry test
    does not depend on wall-clock behaviour.
    """
    effects = _effects(action_input)
    fault_type = str((fault or {}).get("type", "")) if fault else ""
    parameters = (fault or {}).get("parameters") or {}

    if fault_type in {"TIMEOUT_BEFORE_EXECUTION", "RATE_LIMITED", "TRANSIENT_ERROR"}:
        failures = int(parameters.get("failures", 1))
        if attempt <= failures:
            if fault_type == "TIMEOUT_BEFORE_EXECUTION":
                return ActionResult(
                    OUTCOME_FAILED,
                    "the gateway timed out before invoking the operation; no effect was applied",
                    error_class="TIMEOUT",
                    error_code="TIMEOUT_BEFORE_EXECUTION",
                    retryable=True,
                )
            if fault_type == "RATE_LIMITED":
                return ActionResult(
                    OUTCOME_FAILED,
                    "the target rejected the request with a rate limit before the effect",
                    error_class="RATE_LIMITED",
                    error_code="RATE_LIMITED",
                    retryable=True,
                )
            return ActionResult(
                OUTCOME_FAILED,
                "dependency unavailable before the effect was applied",
                error_class="HANDLER_ERROR",
                error_code="TRANSIENT_ERROR",
                retryable=True,
            )
        apply_effects(state, effects)
        return ActionResult(OUTCOME_SUCCEEDED, "dependency recovered; effect applied")

    if fault_type == "NETWORK_DROP":
        # The request never reached the server: no effect.
        return ActionResult(OUTCOME_UNKNOWN, "connection dropped before the request was sent")

    if fault_type == "MISSING_FIELD":
        apply_effects(state, effects)
        field = str(parameters.get("field", "invoice.payment_reference"))
        _pop_path(state, field)
        return ActionResult(
            OUTCOME_SUCCEEDED,
            f"response omitted required field {field!r}",
            missing_fields=[field],
        )

    if fault_type == "CONTRADICTORY_TOTALS":
        apply_effects(state, effects)
        amount = _get_path(state, "invoice.amount_cents")
        po_amount = parameters.get("po_amount_cents")
        if po_amount is None and isinstance(amount, (int, float)):
            po_amount = int(amount) - 100
        _set_path(state, "purchase_order.amount_cents", po_amount)
        return ActionResult(
            OUTCOME_SUCCEEDED,
            "effect applied with a contradictory invoice and purchase-order total",
        )

    if fault_type == "POSTCONDITION_FAILURE":
        apply_effects(state, effects)
        path = str(parameters.get("path", "invoice.status"))
        value = parameters.get("value", "SUBMITTED")
        _set_path(state, path, value)
        return ActionResult(
            OUTCOME_SUCCEEDED,
            f"acknowledged response, but {path!r} was left at {value!r}",
        )

    if fault_type == "PARTIAL_COMMIT":
        # The server committed part of the change and then died. It commits the
        # ledger write but never reaches the status update, so the action can
        # look successful while the terminal-state invariant is broken.
        if effects:
            applied = (len(effects) + 1) // 2
            apply_effects(state, effects[:applied])
        return ActionResult(OUTCOME_SUCCEEDED, "server acknowledged a partial commit")

    if fault_type == "DUPLICATE_RESPONSE":
        duplicates = max(int(parameters.get("duplicates", 2)), 1)
        for _ in range(duplicates):
            apply_effects(state, effects)
        return ActionResult(
            OUTCOME_SUCCEEDED,
            f"duplicate response delivered; effect applied {duplicates} times",
        )

    if fault_type == "TIMEOUT":
        apply_effects(state, effects)
        return ActionResult(OUTCOME_UNKNOWN, "no response within the action timeout")

    if fault_type == "RESPONSE_LOST":
        apply_effects(state, effects)
        return ActionResult(
            OUTCOME_UNKNOWN, "the operation applied the effect but the response was lost"
        )

    if fault_type == "HTTP_500":
        apply_effects(state, effects)
        return ActionResult(OUTCOME_UNKNOWN, "HTTP 500 returned after the request was accepted")

    if fault_type == "MALFORMED_RESPONSE":
        apply_effects(state, effects)
        return ActionResult(
            OUTCOME_UNKNOWN,
            "the operation applied the effect but returned a malformed response",
        )

    if fault_type == "STALE_READ":
        apply_effects(state, effects)
        return ActionResult(
            OUTCOME_UNKNOWN,
            "the operation applied the effect but its confirming read was "
            "served from a stale replica",
        )

    if fault_type == "LATENCY":
        apply_effects(state, effects)
        return ActionResult(OUTCOME_SUCCEEDED, "effect applied with added latency")

    apply_effects(state, effects)
    return ActionResult(OUTCOME_SUCCEEDED, "effect applied")


def probe(
    action_input: dict[str, Any],
    state: dict[str, Any],
    state_before: dict[str, Any],
    *,
    fault: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Read-only confirming probe. Never mutates state, never retries the action."""
    marker = action_input.get("probe")
    if not isinstance(marker, dict) or not marker:
        return False, "no probe marker declared for this action"

    fault_type = str((fault or {}).get("type", "")) if fault else ""
    observed_state = state_before if fault_type == "STALE_READ" else state
    result = evaluate_condition(marker, observed_state)
    detail = f"probe {result.detail}" + (" (stale replica)" if fault_type == "STALE_READ" else "")
    return result.ok, detail
