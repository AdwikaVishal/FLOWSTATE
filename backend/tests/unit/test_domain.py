"""Pure-domain tests: graph validation, schema validation, redaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from flowstate.domain.graph import (
    GraphValidationError,
    build_graph,
    normalize_retry_policy,
)
from flowstate.domain.schema_validation import validate_schema
from flowstate.services.redaction import redact


@dataclass
class FakeNode:
    ordinal: int
    node_key: str = ""
    node_type: str = "ACTION"
    depends_on: list[str] = field(default_factory=list)
    input: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(default_factory=dict)
    failure_handler: dict[str, Any] = field(default_factory=dict)
    label: str = "node"
    kind: str = "ASSERT"
    requires_authorization: bool = False
    side_effect_class: str = "READ_ONLY"
    timeout_ms: int = 5000
    target: dict[str, Any] = field(default_factory=dict)


def test_linear_fallback_chains_by_ordinal() -> None:
    graph = build_graph(
        [
            FakeNode(0, node_key="a"),
            FakeNode(1, node_key="b"),
            FakeNode(2, node_key="c"),
        ]
    )
    assert graph.nodes["b"].depends_on == ("a",)
    assert graph.nodes["c"].depends_on == ("b",)
    assert graph.start_key == "__start__"
    assert graph.end_key == "__end__"
    assert graph.nodes["a"].depends_on == ("__start__",)
    assert set(graph.nodes["__end__"].depends_on) == {"a", "b", "c"}


def test_explicit_dependencies_are_honoured() -> None:
    graph = build_graph(
        [
            FakeNode(0, node_key="start", node_type="START"),
            FakeNode(1, node_key="left", depends_on=["start"]),
            FakeNode(2, node_key="right", depends_on=["start"]),
            FakeNode(3, node_key="join", depends_on=["left", "right"]),
        ]
    )
    assert graph.nodes["join"].depends_on == ("left", "right")
    assert graph.nodes["__end__"].depends_on == ("left", "right", "join")


def test_branch_requires_targets_and_route_source() -> None:
    with pytest.raises(GraphValidationError) as exc:
        build_graph(
            [
                FakeNode(0, node_key="s", node_type="START"),
                FakeNode(1, node_key="b", node_type="BRANCH", depends_on=["s"]),
            ]
        )
    assert exc.value.code == "BRANCH_WITHOUT_TARGETS"

    with pytest.raises(GraphValidationError) as exc:
        build_graph(
            [
                FakeNode(0, node_key="s", node_type="START"),
                FakeNode(
                    1,
                    node_key="b",
                    node_type="BRANCH",
                    depends_on=["s"],
                    input={"branches": {"true": "t", "false": "f"}},
                ),
                FakeNode(
                    2,
                    node_key="t",
                    depends_on=["b"],
                    input={"route_from": {"node_key": "s", "outcome": "true"}},
                ),
                FakeNode(3, node_key="f", depends_on=["b"]),
            ]
        )
    assert exc.value.code == "ROUTE_SOURCE_NOT_BRANCH"


def test_cycle_and_unknown_dependency_detected() -> None:
    with pytest.raises(GraphValidationError) as exc:
        build_graph(
            [
                FakeNode(0, node_key="a", depends_on=["b"]),
                FakeNode(1, node_key="b", depends_on=["a"]),
            ]
        )
    assert exc.value.code == "GRAPH_CYCLE"

    with pytest.raises(GraphValidationError) as exc:
        build_graph([FakeNode(0, node_key="a", depends_on=["missing"])])
    assert exc.value.code == "UNKNOWN_DEPENDENCY"


def test_unreachable_node_detected() -> None:
    with pytest.raises(GraphValidationError) as exc:
        build_graph(
            [
                FakeNode(0, node_key="start", node_type="START"),
                FakeNode(1, node_key="blocked", depends_on=["missing_end"]),
            ]
        )
    assert exc.value.code == "UNKNOWN_DEPENDENCY"


def test_retry_policy_validation() -> None:
    assert normalize_retry_policy({"max_attempts": 3, "retry_on": ["HANDLER_ERROR"]}) == {
        "max_attempts": 3,
        "retry_on": ["HANDLER_ERROR"],
        "backoff_ms": 0,
    }
    with pytest.raises(GraphValidationError):
        normalize_retry_policy({"max_attempts": 99})
    with pytest.raises(GraphValidationError):
        normalize_retry_policy({"retry_on": ["NOT_A_CLASS"]})
    with pytest.raises(GraphValidationError):
        normalize_retry_policy({"backoff_ms": -1})


def test_schema_validation_covers_types_and_nested_paths() -> None:
    schema = {
        "type": "object",
        "required": ["amount"],
        "properties": {
            "amount": {"type": "integer", "minimum": 1, "maximum": 100},
            "tag": {"type": "string", "enum": ["a", "b"]},
            "history": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    assert validate_schema({"amount": 42, "tag": "a", "history": ["x"]}, schema) == []
    errors = validate_schema({"amount": 0, "tag": "c", "extra": True, "history": ["x", 3]}, schema)
    assert any("amount" in error for error in errors)
    assert any("tag" in error for error in errors)
    assert any("extra" in error for error in errors)
    assert any("history.1" in error for error in errors)
    assert any("value.amount" in error for error in validate_schema({}, schema, path="value"))


def test_redaction_hides_sensitive_keys_and_declared_paths() -> None:
    data = {
        "api_token": "secret",
        "invoice": {"amount": 100, "approver": {"name": "Ada", "password": "hunter2"}},
        "history": [{"note": "ok", "card": "4111"}],
    }
    redacted = redact(data, ["invoice.approver.name"])
    assert redacted["api_token"] == "[redacted]"
    assert redacted["invoice"]["approver"]["name"] == "[redacted]"
    assert redacted["invoice"]["approver"]["password"] == "[redacted]"
    assert redacted["history"][0]["card"] == "[redacted]"
    assert redacted["invoice"]["amount"] == 100
    assert data["invoice"]["approver"]["name"] == "Ada", "input must not be mutated"


def test_wildcard_paths_redact_every_list_entry() -> None:
    data = {"items": [{"value": 1}, {"value": 2}], "other": 3}
    redacted = redact(data, ["items.*.value"])
    assert redacted["items"] == [{"value": "[redacted]"}, {"value": "[redacted]"}]
    assert redacted["other"] == 3
