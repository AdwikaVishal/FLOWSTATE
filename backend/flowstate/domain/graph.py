"""Workflow graph construction and validation.

A version is a typed DAG. The persistence model still calls nodes
``ActionDefinition`` for backwards compatibility; this module gives them graph
semantics without letting a version execute an invalid graph.

The graph builder is deliberately deterministic: nodes are ordered by
``(ordinal, node_key)`` and the scheduler in ``services.execution`` always picks
the first ready node in that order. No wall-clock or hash ordering is used.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from flowstate.domain.enums import ErrorClass, NodeType

VIRTUAL_START = "__start__"
VIRTUAL_END = "__end__"


class GraphValidationError(ValueError):
    """A workflow graph cannot be published or executed."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "GRAPH_INVALID",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class GraphNode:
    key: str
    ordinal: int
    node_type: NodeType
    action: Any
    depends_on: tuple[str, ...]
    virtual: bool = False

    @property
    def label(self) -> str:
        return str(getattr(self.action, "label", self.key))

    @property
    def requires_authorization(self) -> bool:
        return bool(getattr(self.action, "requires_authorization", False))

    @property
    def side_effect_class(self) -> str:
        return str(getattr(self.action, "side_effect_class", "READ_ONLY"))

    @property
    def timeout_ms(self) -> int:
        return int(getattr(self.action, "timeout_ms", 5000))

    @property
    def route_from(self) -> dict[str, str] | None:
        raw = (getattr(self.action, "input", None) or {}).get("route_from")
        if isinstance(raw, dict) and raw.get("node_key"):
            return {
                "node_key": str(raw["node_key"]),
                "outcome": str(raw.get("outcome", "true")),
            }
        return None

    @property
    def branches(self) -> dict[str, str]:
        raw = (getattr(self.action, "input", None) or {}).get("branches")
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items() if value}

    @property
    def retry_policy(self) -> dict[str, Any]:
        raw = getattr(self.action, "retry_policy", None) or {}
        return raw if isinstance(raw, dict) else {}

    @property
    def failure_handler(self) -> dict[str, Any]:
        raw = getattr(self.action, "failure_handler", None) or {}
        return raw if isinstance(raw, dict) else {}


@dataclass(frozen=True)
class WorkflowGraph:
    nodes: dict[str, GraphNode]
    order: tuple[str, ...]
    start_key: str
    end_key: str
    route_targets: dict[str, dict[str, str]]

    def node(self, key: str) -> GraphNode:
        return self.nodes[key]

    @property
    def execution_nodes(self) -> list[GraphNode]:
        return [self.nodes[key] for key in self.order]

    @property
    def action_nodes(self) -> list[GraphNode]:
        return [item for item in self.execution_nodes if item.node_type == NodeType.ACTION]

    @property
    def node_keys(self) -> list[str]:
        return list(self.order)


def _node_key(action: Any, ordinal: int) -> str:
    raw = str(getattr(action, "node_key", "") or "").strip()
    return raw or f"n{ordinal}"


def _node_type(action: Any) -> NodeType:
    raw = str(getattr(action, "node_type", "ACTION") or "ACTION")
    try:
        return NodeType(raw.upper())
    except ValueError as exc:
        raise GraphValidationError(
            f"Unknown node type {raw!r} at ordinal {getattr(action, 'ordinal', '?')}.",
            code="UNKNOWN_NODE_TYPE",
            details={"node_type": raw},
        ) from exc


def _depends_on(action: Any) -> list[str]:
    raw = getattr(action, "depends_on", None) or []
    if not isinstance(raw, (list, tuple)):
        raise GraphValidationError(
            "depends_on must be a list of node keys.", code="INVALID_DEPENDENCIES"
        )
    return [str(item) for item in raw if str(item).strip()]


def normalize_retry_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    policy = raw if isinstance(raw, dict) else {}
    try:
        max_attempts = int(policy.get("max_attempts", 1))
    except (TypeError, ValueError) as exc:
        raise GraphValidationError(
            "retry_policy.max_attempts must be an integer.", code="INVALID_RETRY_POLICY"
        ) from exc
    if not 1 <= max_attempts <= 10:
        raise GraphValidationError(
            "retry_policy.max_attempts must be between 1 and 10.",
            code="INVALID_RETRY_POLICY",
        )
    retry_on = policy.get("retry_on", [])
    if retry_on is None:
        retry_on = []
    if not isinstance(retry_on, list):
        raise GraphValidationError(
            "retry_policy.retry_on must be a list.", code="INVALID_RETRY_POLICY"
        )
    allowed = {item.value for item in ErrorClass}
    for item in retry_on:
        if str(item) not in allowed:
            raise GraphValidationError(
                f"retry_policy.retry_on contains unknown error class {item!r}.",
                code="INVALID_RETRY_POLICY",
            )
    try:
        backoff_ms = int(policy.get("backoff_ms", 0))
    except (TypeError, ValueError) as exc:
        raise GraphValidationError(
            "retry_policy.backoff_ms must be an integer.", code="INVALID_RETRY_POLICY"
        ) from exc
    if backoff_ms < 0:
        raise GraphValidationError(
            "retry_policy.backoff_ms cannot be negative.", code="INVALID_RETRY_POLICY"
        )
    return {
        "max_attempts": max_attempts,
        "retry_on": [str(item) for item in retry_on],
        "backoff_ms": backoff_ms,
    }


def active_failure_handler(raw: dict[str, Any] | None) -> dict[str, Any]:
    """A handler is active only when it changes default behaviour."""
    handler = raw if isinstance(raw, dict) else {}
    strategy = str(handler.get("strategy", "FAIL")).upper()
    target = str(handler.get("target") or handler.get("node_key") or "")
    if strategy == "FAIL" and not target:
        return {}
    return {"strategy": strategy, "target": target or None}


def _build_nodes(actions: Sequence[Any]) -> tuple[list[GraphNode], str, str, bool]:
    if not actions:
        raise GraphValidationError(
            "A workflow version needs at least one node.", code="EMPTY_GRAPH"
        )

    ordered = sorted(actions, key=lambda item: int(getattr(item, "ordinal", 0)))
    ordinals = [int(getattr(item, "ordinal", 0)) for item in ordered]
    if len(set(ordinals)) != len(ordinals):
        raise GraphValidationError("Node ordinals must be unique.", code="DUPLICATE_ORDINAL")
    if ordinals != list(range(len(ordinals))):
        raise GraphValidationError(
            "Node ordinals must be contiguous starting at 0.",
            code="NON_CONTIGUOUS_ORDINALS",
            details={"ordinals": ordinals},
        )

    raw_nodes: list[GraphNode] = []
    keys: set[str] = set()
    for action in ordered:
        ordinal = int(getattr(action, "ordinal", 0))
        key = _node_key(action, ordinal)
        if key in keys:
            raise GraphValidationError(
                f"Duplicate node key {key!r}.",
                code="DUPLICATE_NODE_KEY",
                details={"node_key": key},
            )
        keys.add(key)
        raw_nodes.append(
            GraphNode(
                key=key,
                ordinal=ordinal,
                node_type=_node_type(action),
                action=action,
                depends_on=tuple(_depends_on(action)),
            )
        )

    starts = [item for item in raw_nodes if item.node_type == NodeType.START]
    ends = [item for item in raw_nodes if item.node_type == NodeType.END]
    if len(starts) > 1:
        raise GraphValidationError(
            "A graph may declare at most one START node.", code="MULTIPLE_START"
        )
    if len(ends) > 1:
        raise GraphValidationError(
            "A graph may declare at most one END node; use branch nodes for alternate exits.",
            code="MULTIPLE_END",
        )

    explicit_start = starts[0] if starts else None
    start_key = explicit_start.key if explicit_start else VIRTUAL_START
    if explicit_start and explicit_start.depends_on:
        raise GraphValidationError(
            "The START node cannot depend on another node.",
            code="START_HAS_DEPENDENCIES",
        )

    explicit_end = ends[0] if ends else None
    end_key = explicit_end.key if explicit_end else VIRTUAL_END

    if explicit_start is None:
        raw_nodes.append(
            GraphNode(
                key=VIRTUAL_START,
                ordinal=min(ordinals) - 1,
                node_type=NodeType.START,
                action=None,
                depends_on=(),
                virtual=True,
            )
        )

    # A node with no explicit dependencies falls back to the previous node in
    # ordinal order. This keeps older linear workflows (and the demo fixtures)
    # sequential without forcing every author to declare a chain.
    ordered_raw = sorted(raw_nodes, key=lambda item: (item.ordinal, item.key))
    previous_key: str | None = None
    normalized: list[GraphNode] = []
    for node in ordered_raw:
        deps = list(node.depends_on)
        if node.node_type == NodeType.START:
            deps = []
        elif not deps:
            deps = [previous_key or start_key]
        route_from = node.route_from
        if route_from and route_from["node_key"] not in deps:
            deps.append(route_from["node_key"])
        normalized.append(
            GraphNode(
                key=node.key,
                ordinal=node.ordinal,
                node_type=node.node_type,
                action=node.action,
                depends_on=tuple(dict.fromkeys(deps)),
                virtual=node.virtual,
            )
        )
        if node.node_type != NodeType.RECOVERY_HANDLER:
            previous_key = node.key

    by_key = {item.key: item for item in normalized}
    if explicit_end is not None and not by_key[end_key].depends_on:
        end_deps = [
            item.key
            for item in normalized
            if item.key not in {end_key, start_key}
            and item.node_type not in {NodeType.RECOVERY_HANDLER, NodeType.END}
        ]
        by_key[end_key] = GraphNode(
            key=end_key,
            ordinal=by_key[end_key].ordinal,
            node_type=by_key[end_key].node_type,
            action=by_key[end_key].action,
            depends_on=tuple(end_deps),
            virtual=by_key[end_key].virtual,
        )

    if explicit_end is None:
        end_deps = [
            item.key
            for item in normalized
            if item.key not in {start_key}
            and item.node_type not in {NodeType.RECOVERY_HANDLER, NodeType.END}
        ]
        by_key[VIRTUAL_END] = GraphNode(
            key=VIRTUAL_END,
            ordinal=max(ordinals) + 1,
            node_type=NodeType.END,
            action=None,
            depends_on=tuple(end_deps),
            virtual=True,
        )

    return list(by_key.values()), start_key, end_key, explicit_end is None


def build_graph(actions: Sequence[Any]) -> WorkflowGraph:
    nodes, start_key, end_key, _ = _build_nodes(actions)
    by_key = {item.key: item for item in nodes}

    # ---- structural validation -------------------------------------------------
    for node in nodes:
        for dep in node.depends_on:
            if dep not in by_key:
                raise GraphValidationError(
                    f"Node {node.key!r} depends on unknown node {dep!r}.",
                    code="UNKNOWN_DEPENDENCY",
                    details={"node_key": node.key, "dependency": dep},
                )
            if dep == node.key:
                raise GraphValidationError(
                    f"Node {node.key!r} cannot depend on itself.",
                    code="SELF_DEPENDENCY",
                )
        if node.node_type == NodeType.BRANCH and not node.branches:
            raise GraphValidationError(
                f"Branch node {node.key!r} must declare at least one branch target.",
                code="BRANCH_WITHOUT_TARGETS",
                details={"node_key": node.key},
            )
        for outcome, target in node.branches.items():
            if target not in by_key:
                raise GraphValidationError(
                    f"Branch {node.key!r} points at unknown node {target!r}.",
                    code="UNKNOWN_BRANCH_TARGET",
                    details={"node_key": node.key, "outcome": outcome, "target": target},
                )
        if node.route_from:
            source = by_key.get(node.route_from["node_key"])
            if source is None:
                raise GraphValidationError(
                    f"Node {node.key!r} routes from unknown node {node.route_from['node_key']!r}.",
                    code="UNKNOWN_ROUTE_SOURCE",
                )
            if source.node_type != NodeType.BRANCH:
                raise GraphValidationError(
                    f"Node {node.key!r} routes from {source.key!r}, which is not a branch.",
                    code="ROUTE_SOURCE_NOT_BRANCH",
                )
        handler = active_failure_handler(node.failure_handler)
        if handler:
            strategy = str(handler["strategy"])
            if strategy not in {"FAIL", "SKIP", "GOTO", "RECOVERY"}:
                raise GraphValidationError(
                    f"Node {node.key!r} has unknown failure strategy {strategy!r}.",
                    code="INVALID_FAILURE_HANDLER",
                )
            handler_target = handler.get("target")
            if strategy in {"GOTO", "RECOVERY"}:
                if not handler_target or str(handler_target) not in by_key:
                    raise GraphValidationError(
                        f"Node {node.key!r} failure handler points at an unknown node.",
                        code="UNKNOWN_FAILURE_TARGET",
                    )
                if by_key[str(handler_target)].node_type != NodeType.RECOVERY_HANDLER:
                    raise GraphValidationError(
                        f"Node {node.key!r} failure target must be a RECOVERY_HANDLER.",
                        code="FAILURE_TARGET_NOT_RECOVERY",
                    )
        if node.node_type != NodeType.ACTION and node.retry_policy:
            normalize_retry_policy(node.retry_policy)
        if node.node_type == NodeType.ACTION:
            normalize_retry_policy(node.retry_policy)

    # ---- cycle detection -------------------------------------------------------
    indegree = dict.fromkeys(by_key, 0)
    outgoing: dict[str, list[str]] = {key: [] for key in by_key}
    for node in nodes:
        for dep in node.depends_on:
            indegree[node.key] += 1
            outgoing[dep].append(node.key)
    queue = deque(
        sorted(
            (key for key, value in indegree.items() if value == 0),
            key=lambda k: (by_key[k].ordinal, k),
        )
    )
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in sorted(outgoing[current], key=lambda k: (by_key[k].ordinal, k)):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(by_key):
        cyclic = sorted(key for key, value in indegree.items() if value > 0)
        raise GraphValidationError(
            "The workflow graph contains a cycle.",
            code="GRAPH_CYCLE",
            details={"nodes": cyclic},
        )

    # ---- reachability ----------------------------------------------------------
    reachable = {start_key}
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node.key in reachable:
                continue
            if node.node_type == NodeType.RECOVERY_HANDLER:
                continue
            if any(dep in reachable for dep in node.depends_on):
                reachable.add(node.key)
                changed = True
    unreachable = [
        item.key
        for item in nodes
        if item.key not in reachable and item.node_type != NodeType.RECOVERY_HANDLER
    ]
    if unreachable:
        raise GraphValidationError(
            "Some nodes are not reachable from START.",
            code="UNREACHABLE_NODES",
            details={"nodes": sorted(unreachable)},
        )

    recovery_targets = {
        str(handler["target"])
        for handler in (active_failure_handler(node.failure_handler) for node in nodes)
        if handler.get("target")
    }
    for target in recovery_targets:
        if target and target not in by_key:
            raise GraphValidationError(
                f"Failure handler points at unknown node {target!r}.",
                code="UNKNOWN_FAILURE_TARGET",
            )

    route_targets = {node.key: node.branches for node in nodes if node.node_type == NodeType.BRANCH}
    order = tuple(item.key for item in sorted(nodes, key=lambda node: (node.ordinal, node.key)))
    return WorkflowGraph(
        nodes=by_key,
        order=order,
        start_key=start_key,
        end_key=end_key,
        route_targets=route_targets,
    )


def graph_summary(graph: WorkflowGraph) -> dict[str, Any]:
    return {
        "nodes": len(graph.nodes),
        "edges": sum(len(item.depends_on) for item in graph.nodes.values()),
        "start": graph.start_key,
        "end": graph.end_key,
        "branches": len(graph.route_targets),
    }


def iter_dependents(graph: WorkflowGraph, key: str) -> Iterable[GraphNode]:
    for node in graph.execution_nodes:
        if key in node.depends_on:
            yield node
