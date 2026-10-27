"""Turn raw demo-site interactions into reviewed, typed workflow candidates.

Everything here is data transformation: no browser, no network and no
generated code. Playwright enrichment happens at validation and replay time;
this module preserves the recorded intent even when a browser is unavailable.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_RECORDABLE_KINDS = frozenset({"CLICK", "TYPE", "SELECT", "NAVIGATE"})
_SENSITIVE = re.compile(
    r"(pass(word|wd)?|secret|token|auth|api[_-]?key|card|cvv|cvc|iban|account|ssn)",
    re.IGNORECASE,
)
_IDENTIFIER_VALUE = re.compile(r"^[A-Z]{2,6}-[A-Za-z0-9-]{1,32}$")
_FINANCIAL_LABEL = re.compile(
    r"(submit|approve|approval|pay|payment|transfer|delete|confirm|authorize)",
    re.IGNORECASE,
)
_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class RawLocator(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tag: str = Field(default="", max_length=24)
    test_id: str = Field(default="", max_length=160)
    role: str = Field(default="", max_length=48)
    name: str = Field(default="", max_length=240)
    label: str = Field(default="", max_length=240)
    placeholder: str = Field(default="", max_length=240)
    text: str = Field(default="", max_length=240)
    css: str = Field(default="", max_length=400)
    input_name: str = Field(default="", max_length=120)
    param: str = Field(default="", max_length=80)


class RawPageSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = Field(default="", max_length=800)
    route: str = Field(default="", max_length=400)
    page: str = Field(default="", max_length=80)
    heading: str = Field(default="", max_length=300)
    state: dict[str, Any] = Field(default_factory=dict)


class RawCaptureEvent(BaseModel):
    """The contract the in-page recorder posts to the console."""

    model_config = ConfigDict(extra="ignore")

    client_sequence: int = Field(default=0, ge=0)
    kind: str = Field(min_length=1, max_length=24)
    locator: RawLocator = Field(default_factory=RawLocator)
    accessible_name: str = Field(default="", max_length=240)
    value: str | None = Field(default=None, max_length=2000)
    sensitive: bool = False
    redacted_fields: list[str] = Field(default_factory=list)
    param: str = Field(default="", max_length=80)
    page_before: RawPageSnapshot = Field(default_factory=RawPageSnapshot)
    page_after: RawPageSnapshot = Field(default_factory=RawPageSnapshot)
    observed_result: str = Field(default="", max_length=500)
    timestamp_offset_ms: int = Field(default=0, ge=0)
    submit_shaped: bool = False

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        kind = value.strip().upper()
        if kind not in _RECORDABLE_KINDS:
            raise ValueError(f"unsupported capture kind {value!r}")
        return kind


def rank_selectors(locator: RawLocator | dict[str, Any]) -> list[dict[str, Any]]:
    """Rank locator strategies: role+name, label, test id, placeholder, text, CSS.

    Role+accessible-name wins because it survives markup changes and matches the
    console's stated selector policy. CSS is always last and is never allowed to
    outrank an accessible locator.
    """
    raw = locator.model_dump() if isinstance(locator, RawLocator) else dict(locator or {})
    role = str(raw.get("role") or "").strip()
    name = str(raw.get("name") or raw.get("label") or "").strip()
    label = str(raw.get("label") or "").strip()
    test_id = str(raw.get("test_id") or "").strip()
    placeholder = str(raw.get("placeholder") or "").strip()
    text = str(raw.get("text") or "").strip()
    css = str(raw.get("css") or "").strip()

    candidates: list[dict[str, Any]] = []
    if role and name:
        candidates.append(
            {
                "strategy": "role",
                "role": role,
                "name": name,
                "value": name,
                "score": 100,
            }
        )
    if label:
        candidates.append({"strategy": "label", "value": label, "score": 95})
    if test_id:
        candidates.append({"strategy": "testid", "value": test_id, "score": 90})
    if placeholder:
        candidates.append({"strategy": "placeholder", "value": placeholder, "score": 80})
    if text and len(text) <= 120:
        candidates.append({"strategy": "text", "value": text, "score": 70})
    if css:
        candidates.append({"strategy": "css", "value": css, "score": 50})
    candidates.sort(key=lambda item: (-int(item["score"]), str(item["strategy"])))
    return candidates


def _describe(snapshot: RawPageSnapshot) -> str:
    state = snapshot.state or {}
    if state.get("validation") == "VALID":
        return f"Validation passed for {state.get('invoice_id') or 'the invoice'}"
    if state.get("validation") == "DISCREPANCY":
        return "Validation reported a discrepancy"
    if snapshot.page == "invoice":
        return f"Invoice {state.get('invoice_id') or ''} is open".strip()
    if snapshot.page == "missing":
        return "The demo reported that the invoice was not found"
    return f"Invoice queue ({snapshot.route or '#/queue'})"


def _wait_for(before: RawPageSnapshot, after: RawPageSnapshot) -> dict[str, Any]:
    wait: dict[str, Any] = {}
    if after.page and before.page != after.page:
        wait["page"] = after.page
    before_state = before.state or {}
    after_state = after.state or {}
    if not before_state.get("validation") and after_state.get("validation"):
        wait["validation_present"] = True
    if not before_state.get("invoice_id") and after_state.get("invoice_id"):
        wait["invoice_present"] = True
    return wait


def _engine_postconditions(before: RawPageSnapshot, after: RawPageSnapshot) -> list[dict[str, Any]]:
    before_state = before.state or {}
    after_state = after.state or {}
    conditions: list[dict[str, Any]] = []
    if after.page and before.page != after.page:
        conditions.append({"path": "page.page", "op": "eq", "value": after.page})
    if not before_state.get("validation") and after_state.get("validation"):
        conditions.append(
            {
                "path": "page.state.validation",
                "op": "in",
                "value": ["VALID", "DISCREPANCY"],
            }
        )
    if not before_state.get("invoice_id") and after_state.get("invoice_id"):
        conditions.append({"path": "page.state.invoice_id", "op": "exists"})
    if not conditions:
        conditions.append({"path": "page.page", "op": "eq", "value": after.page or before.page})
    return conditions


def _parameter(event: RawCaptureEvent) -> str | None:
    if event.kind not in {"TYPE", "SELECT"} or event.sensitive:
        return None
    candidate = (event.param or event.locator.param or event.locator.input_name or "").strip()
    if not candidate or not _PARAM_NAME.match(candidate):
        return None
    if _SENSITIVE.search(candidate):
        return None
    return candidate


def _ambiguity_reason(
    event: RawCaptureEvent, selectors: list[dict[str, Any]], parameter: str | None
) -> str:
    if event.sensitive or event.redacted_fields:
        return "sensitive_value_redacted"
    accessible = [item for item in selectors if item["strategy"] in {"role", "label", "testid"}]
    if not accessible:
        return "only_css_selector"
    before = event.page_before.state or {}
    after = event.page_after.state or {}
    if (
        event.kind == "CLICK"
        and event.page_before.route == event.page_after.route
        and all(before.get(key) == after.get(key) for key in ("page", "validation", "invoice_id"))
    ):
        return "no_observed_transition"
    value = (event.value or "").strip()
    if event.kind == "TYPE" and value and parameter is None and _IDENTIFIER_VALUE.match(value):
        return "unparameterized_identifier"
    return ""


def action_from_event(
    event: RawCaptureEvent,
    *,
    ordinal: int,
    demo_origin: str = "",
) -> dict[str, Any]:
    selectors = rank_selectors(event.locator)
    parameter = _parameter(event)
    ambiguity = _ambiguity_reason(event, selectors, parameter)
    submit_shaped = event.submit_shaped or (
        event.kind == "CLICK"
        and bool(_FINANCIAL_LABEL.search(event.accessible_name or event.locator.text or ""))
    )
    kind = event.kind
    if kind == "CLICK" and submit_shaped:
        kind = "SUBMIT"

    accessible = event.accessible_name or event.locator.name or event.locator.label
    label = accessible or (
        f"Enter {parameter}" if parameter else f"{event.kind.title()} step {ordinal + 1}"
    )
    observed = event.observed_result or _describe(event.page_after)
    preconditions = [
        {
            "kind": "page_state",
            "route": event.page_before.route,
            "page": event.page_before.page,
            "selector": selectors[0] if selectors else None,
        }
    ]
    postconditions = [
        {
            "kind": "page_state",
            "route": event.page_after.route,
            "page": event.page_after.page,
            "state": event.page_after.state or {},
        }
    ]
    return {
        "ordinal": ordinal,
        "kind": kind,
        "label": label,
        "selector": selectors,
        "accessible_name": accessible,
        "input_param": parameter,
        "input_value": None if event.sensitive else event.value,
        "page_before": event.page_before.model_dump(),
        "page_after": event.page_after.model_dump(),
        "expected_result": observed,
        "observed_result": observed,
        "preconditions": preconditions,
        "postconditions": postconditions,
        "failure_conditions": [
            "locator_not_found",
            "locator_ambiguous",
            "expected_page_state_not_observed",
            "browser_timeout",
        ],
        "redacted_fields": list(
            dict.fromkeys(event.redacted_fields or (["value"] if event.sensitive else []))
        ),
        "ambiguous": bool(ambiguity),
        "ambiguity_reason": ambiguity,
        "status": "AMBIGUOUS" if ambiguity else "CAPTURED",
        "resolution": {},
        "demo_origin": demo_origin,
    }


def build_input_schema(actions: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for action in actions:
        parameter = action.get("input_param")
        if not parameter or parameter in properties:
            continue
        captured = str(action.get("input_value") or "")
        prop: dict[str, Any] = {"type": "string", "minLength": 1, "maxLength": 120}
        if _IDENTIFIER_VALUE.match(captured):
            prop["pattern"] = "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
        properties[parameter] = prop
        required.append(parameter)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def suggested_inputs(actions: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for action in actions:
        parameter = action.get("input_param")
        if not parameter or parameter in values:
            continue
        value = action.get("input_value")
        values[parameter] = value if value not in {None, ""} else f"<{parameter}>"
    # The demo fixtures make this deterministic and useful for review.
    values.setdefault("supplier_id", "SUP-77")
    values.setdefault("invoice_id", "INV-1001")
    return values


def workflow_nodes(
    actions: list[dict[str, Any]], demo_url: str, *, browser_timeout_ms: int = 8000
) -> list[dict[str, Any]]:
    """Derive a linear typed graph from reviewed candidates.

    Only typed ACTION nodes are produced. No node contains executable code; the
    browser driver interprets the declarative ``target`` and ``input`` fields.
    """
    nodes: list[dict[str, Any]] = [
        {
            "ordinal": 0,
            "node_key": "start",
            "node_type": "START",
            "kind": "ASSERT",
            "label": "Start learned workflow",
            "target": {},
            "input": {},
            "depends_on": [],
            "channel": "SIMULATED",
            "side_effect_class": "READ_ONLY",
            "timeout_ms": 5000,
        },
        {
            "ordinal": 1,
            "node_key": "validate_inputs",
            "node_type": "VALIDATION",
            "kind": "ASSERT",
            "label": "Validate required inputs",
            "target": {},
            "input": {
                "path": "inputs",
                "schema": build_input_schema(actions),
            },
            "depends_on": ["start"],
            "channel": "SIMULATED",
            "side_effect_class": "READ_ONLY",
            "timeout_ms": 5000,
        },
        {
            "ordinal": 2,
            "node_key": "open_demo_app",
            "node_type": "ACTION",
            "kind": "NAVIGATE",
            "label": "Open the invoice demo",
            "target": {
                "surface": "demo-app",
                "url": demo_url,
                "origin": demo_url.split("/demo-app/")[0],
                "driver": "playwright",
            },
            "input": {"action": "NAVIGATE", "wait_for": {"page": "queue"}},
            "depends_on": ["validate_inputs"],
            "channel": "BROWSER",
            "side_effect_class": "READ_ONLY",
            "timeout_ms": browser_timeout_ms,
            "postconditions": [{"path": "page.page", "op": "eq", "value": "queue"}],
        },
    ]

    prior = "open_demo_app"
    open_ordinal = 3
    for index, action in enumerate(actions):
        ordinal = open_ordinal + index
        key = f"captured_{index + 1}"
        before = action.get("page_before") or {}
        after = action.get("page_after") or {}
        actor_kind = str(action.get("kind", "CLICK")).upper()
        observed_wait = _wait_for(
            RawPageSnapshot.model_validate(before) if before else RawPageSnapshot(),
            RawPageSnapshot.model_validate(after) if after else RawPageSnapshot(),
        )
        action_input: dict[str, Any] = {
            "action": actor_kind,
            # Typing changes a field, never the page. Waiting for a page
            # transition here would turn a correct entry into a false timeout.
            "wait_for": {} if actor_kind in {"TYPE", "SELECT"} else observed_wait,
        }
        if action.get("input_param"):
            action_input["value_from"] = f"inputs.{action['input_param']}"
        elif action.get("input_value") is not None:
            action_input["value"] = action.get("input_value")
        label = str(action.get("label") or f"Captured step {index + 1}")
        requires_authorization = bool(_FINANCIAL_LABEL.search(label))
        node_type = "HUMAN_DECISION" if requires_authorization else "ACTION"
        if node_type == "HUMAN_DECISION":
            action_input.update(
                {
                    "question": f"Authorize the recorded step “{label}”?",
                    "reason": (
                        "The recorded label looks financial. FLOWSTATE never auto-approves one."
                    ),
                    "policy_citation": "ActionPolicy: financial-shaped actions require a human.",
                }
            )
        nodes.append(
            {
                "ordinal": ordinal,
                "node_key": key,
                "node_type": node_type,
                "kind": str(action.get("kind", "CLICK")),
                "label": label,
                "target": {
                    "surface": "demo-app",
                    "url": demo_url,
                    "origin": demo_url.split("/demo-app/")[0],
                    "driver": "playwright",
                    "locator": action.get("selector") or [],
                    "accessible_name": action.get("accessible_name") or "",
                    "page": before.get("page") or "",
                },
                "input": action_input,
                "preconditions": [],
                "postconditions": _engine_postconditions(
                    RawPageSnapshot.model_validate(before) if before else RawPageSnapshot(),
                    RawPageSnapshot.model_validate(after) if after else RawPageSnapshot(),
                ),
                "failure_conditions": action.get("failure_conditions") or [],
                "depends_on": [prior],
                "channel": "BROWSER",
                "side_effect_class": "IRREVERSIBLE" if requires_authorization else "READ_ONLY",
                "requires_authorization": requires_authorization,
                "timeout_ms": browser_timeout_ms,
                "retry_policy": {"max_attempts": 1, "retry_on": [], "backoff_ms": 0},
                "failure_handler": {"strategy": "FAIL", "target": None},
            }
        )
        prior = key

    end_ordinal = open_ordinal + len(actions)
    nodes.append(
        {
            "ordinal": end_ordinal,
            "node_key": "end",
            "node_type": "END",
            "kind": "ASSERT",
            "label": "End learned workflow",
            "target": {},
            "input": {},
            "depends_on": [prior],
            "channel": "SIMULATED",
            "side_effect_class": "READ_ONLY",
            "timeout_ms": 5000,
        }
    )
    return nodes


def workflow_invariants(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last = (actions[-1].get("page_after") or {}) if actions else {}
    state = last.get("state") or {}
    if state.get("validation"):
        expression: dict[str, Any] = {
            "kind": "state",
            "path": "page.state.validation",
            "op": "in",
            "value": ["VALID", "DISCREPANCY"],
        }
        name = "A validation result is visible"
    elif last.get("page") == "invoice":
        expression = {
            "kind": "state",
            "path": "page.state.page",
            "op": "eq",
            "value": "invoice",
        }
        name = "The invoice detail page is open"
    else:
        expression = {"kind": "state", "path": "page.state.page", "op": "exists"}
        name = "The demo page exposes an observable state"
    return [
        {
            "name": name,
            "description": (
                "Learned from the recorded page transition; evaluated against the "
                "final browser snapshot."
            ),
            "kind": "TERMINAL_STATE",
            "severity": "MAJOR",
            "expression": expression,
        }
    ]
