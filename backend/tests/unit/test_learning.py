"""Pure learning logic: ranking, redaction, parameterisation and graph shape."""

from __future__ import annotations

from types import SimpleNamespace

from flowstate.capture.recording import (
    RawCaptureEvent,
    RawLocator,
    action_from_event,
    build_input_schema,
    rank_selectors,
    suggested_inputs,
    workflow_invariants,
    workflow_nodes,
)
from flowstate.domain.graph import build_graph
from flowstate.domain.policy import ActionPolicy, PolicyDecision
from tests.fakes import invoice_lookup_events, queue_snapshot, raw_event


def test_selector_ranking_prefers_accessible_locators() -> None:
    ranked = rank_selectors(
        RawLocator(
            role="button",
            name="Validate invoice",
            label="Validate invoice",
            test_id="validate-invoice",
            placeholder="validate",
            text="Validate invoice",
            css="#validate-invoice",
        )
    )
    assert [item["strategy"] for item in ranked] == [
        "role",
        "label",
        "testid",
        "placeholder",
        "text",
        "css",
    ]
    assert ranked[0]["name"] == "Validate invoice"
    assert ranked[0]["score"] > ranked[2]["score"] > ranked[-1]["score"]


def test_css_only_locator_is_flagged_as_ambiguous() -> None:
    event = RawCaptureEvent.model_validate(
        raw_event(
            0,
            "CLICK",
            role="",
            name="",
            test_id="",
            css="div > button:nth-of-type(2)",
            before=queue_snapshot(),
            after=queue_snapshot(),
        )
    )
    action = action_from_event(event, ordinal=0)
    assert action["ambiguous"] is True
    assert action["ambiguity_reason"] == "only_css_selector"
    assert [item["strategy"] for item in action["selector"]] == ["css"]


def test_sensitive_values_are_never_kept() -> None:
    event = RawCaptureEvent.model_validate(
        raw_event(
            0,
            "TYPE",
            role="textbox",
            name="Password",
            label="Password",
            css="#password",
            input_name="password",
            value="hunter2",
            sensitive=True,
        )
    )
    action = action_from_event(event, ordinal=0)
    assert action["input_value"] is None
    assert action["input_param"] is None
    assert action["redacted_fields"] == ["value"]
    assert action["ambiguity_reason"] == "sensitive_value_redacted"


def test_parameters_and_input_schema_are_derived_from_fields() -> None:
    events = [RawCaptureEvent.model_validate(item) for item in invoice_lookup_events()]
    actions = [action_from_event(event, ordinal=index) for index, event in enumerate(events)]
    assert [item["input_param"] for item in actions] == [
        "supplier_id",
        "invoice_id",
        None,
        None,
    ]
    schema = build_input_schema(actions)
    assert schema["required"] == ["supplier_id", "invoice_id"]
    assert schema["additionalProperties"] is False
    assert suggested_inputs(actions) == {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}


def test_generated_graph_is_valid_typed_and_browser_only() -> None:
    events = [RawCaptureEvent.model_validate(item) for item in invoice_lookup_events()]
    actions = [action_from_event(event, ordinal=index) for index, event in enumerate(events)]
    nodes = workflow_nodes(actions, "http://127.0.0.1:8000/demo-app/")
    graph = build_graph([SimpleNamespace(**node) for node in nodes])

    assert graph.start_key == "start"
    assert graph.end_key == "end"
    assert [node.node_type.value for node in graph.execution_nodes] == [
        "START",
        "VALIDATION",
        "ACTION",
        "ACTION",
        "ACTION",
        "ACTION",
        "ACTION",
        "END",
    ]
    browser_nodes = [node for node in graph.execution_nodes if node.node_type.value == "ACTION"]
    assert all(node.action.channel == "BROWSER" for node in browser_nodes)
    assert browser_nodes[0].action.input["action"] == "NAVIGATE"
    for node in browser_nodes[1:]:
        source = node.action.input
        assert "code" not in source
        assert "script" not in source
        assert "locator" in node.action.target


def test_financial_shaped_action_requires_human_decision() -> None:
    event = RawCaptureEvent.model_validate(
        raw_event(
            0,
            "CLICK",
            role="button",
            name="Approve payment",
            test_id="approve-payment",
            css="#approve-payment",
            before=queue_snapshot(),
            after=queue_snapshot(),
            submit_shaped=True,
        )
    )
    action = action_from_event(event, ordinal=0)
    nodes = workflow_nodes([action], "http://127.0.0.1:8000/demo-app/")
    decision_nodes = [node for node in nodes if node["node_type"] == "HUMAN_DECISION"]
    assert len(decision_nodes) == 1
    assert decision_nodes[0]["requires_authorization"] is True
    assert decision_nodes[0]["side_effect_class"] == "IRREVERSIBLE"


def test_invariant_is_derived_from_observed_validation() -> None:
    events = [RawCaptureEvent.model_validate(item) for item in invoice_lookup_events()]
    actions = [action_from_event(event, ordinal=index) for index, event in enumerate(events)]
    invariants = workflow_invariants(actions)
    assert invariants[0]["expression"] == {
        "kind": "state",
        "path": "page.state.validation",
        "op": "in",
        "value": ["VALID", "DISCREPANCY"],
    }


def test_capture_is_limited_to_loopback_demo_origins() -> None:
    policy = ActionPolicy()
    assert policy.resolve_origin("http://127.0.0.1:8123/demo-app/").decision == PolicyDecision.ALLOW
    assert policy.resolve_origin("https://example.com/demo-app/").decision == PolicyDecision.DENY
    assert policy.resolve_origin("https://api.stripe.com/v1/charges").code == "REAL_PAYMENT_HOST"
