"""Deterministic graph engine: scheduling, retries, ambiguity and cancellation."""

from __future__ import annotations

from fastapi.testclient import TestClient
from flowstate.main import create_app


def node(
    ordinal: int,
    key: str,
    node_type: str,
    label: str,
    *,
    depends_on: list[str] | None = None,
    kind: str = "ASSERT",
    input: dict | None = None,
    preconditions: list[dict] | None = None,
    postconditions: list[dict] | None = None,
    side_effect_class: str = "READ_ONLY",
    requires_authorization: bool = False,
    retry_policy: dict | None = None,
    failure_handler: dict | None = None,
    timeout_ms: int = 5000,
) -> dict:
    return {
        "ordinal": ordinal,
        "node_key": key,
        "node_type": node_type,
        "kind": kind,
        "label": label,
        "target": (
            {"surface": "sandbox-gateway", "origin": "http://127.0.0.1:8100"}
            if kind == "PAYMENT_SIMULATED"
            else {"surface": "sandbox"}
        ),
        "input": input or {},
        "preconditions": preconditions or [],
        "postconditions": postconditions or [],
        "depends_on": depends_on or [],
        "side_effect_class": side_effect_class,
        "requires_authorization": requires_authorization,
        "timeout_ms": timeout_ms,
        "retry_policy": retry_policy or {},
        "failure_handler": failure_handler or {},
    }


def paid_invariant() -> dict:
    return {
        "name": "Invoice reaches PAID",
        "kind": "TERMINAL_STATE",
        "severity": "CRITICAL",
        "expression": {"kind": "state", "path": "invoice.status", "op": "eq", "value": "PAID"},
    }


def pay_workflow() -> dict:
    """A branching DAG: START -> validate -> branch -> pay -> checkpoint -> END."""

    return {
        "name": "Graph payment flow",
        "description": "Branch on the invoice amount, pay it, and checkpoint the result.",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(
                1,
                "validate_amount",
                "VALIDATION",
                "Validate the invoice amount",
                depends_on=["start"],
                input={
                    "path": "invoice",
                    "schema": {
                        "type": "object",
                        "required": ["amount_cents", "status"],
                        "properties": {
                            "amount_cents": {"type": "integer", "minimum": 1},
                            "status": {"type": "string", "enum": ["SUBMITTED", "APPROVED"]},
                        },
                    },
                },
            ),
            node(
                2,
                "route_amount",
                "BRANCH",
                "Is the invoice above 1000 USD?",
                depends_on=["validate_amount"],
                input={
                    "condition": {"path": "invoice.amount_cents", "op": "gte", "value": 100000},
                    "branches": {"true": "approve", "false": "auto_pay"},
                },
            ),
            node(
                3,
                "approve",
                "ACTION",
                "Approve the high-value invoice",
                depends_on=["route_amount"],
                kind="AUTHORIZE",
                input={
                    "route_from": {"node_key": "route_amount", "outcome": "true"},
                    "effects": [
                        {"op": "set", "path": "invoice.status", "value": "APPROVED"},
                        {"op": "set", "path": "invoice.approver", "value": "graph-operator"},
                    ],
                },
                postconditions=[{"path": "invoice.status", "op": "eq", "value": "APPROVED"}],
                side_effect_class="IDEMPOTENT",
                retry_policy={"max_attempts": 2, "retry_on": ["HANDLER_ERROR"]},
            ),
            node(
                4,
                "auto_pay",
                "ACTION",
                "Pay the low-value invoice",
                depends_on=["route_amount"],
                kind="PAYMENT_SIMULATED",
                input={
                    "route_from": {"node_key": "route_amount", "outcome": "false"},
                    "effects": [{"op": "set", "path": "invoice.status", "value": "PAID"}],
                    "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                },
                postconditions=[{"path": "invoice.status", "op": "eq", "value": "PAID"}],
                side_effect_class="IRREVERSIBLE",
            ),
            node(
                5,
                "disburse",
                "ACTION",
                "Disburse funds in the sandbox",
                depends_on=["approve"],
                kind="PAYMENT_SIMULATED",
                input={
                    "effects": [
                        {"op": "append", "path": "invoice.ledger", "value": {"entry": "debit"}},
                        {"op": "increment", "path": "invoice.ledger_count", "value": 1},
                        {"op": "set", "path": "invoice.status", "value": "PAID"},
                    ],
                    "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                },
                postconditions=[
                    {"path": "invoice.status", "op": "eq", "value": "PAID"},
                    {"path": "invoice.ledger_count", "op": "eq", "value": 1},
                ],
                side_effect_class="IRREVERSIBLE",
            ),
            node(6, "safe_point", "CHECKPOINT", "Record a safe point", depends_on=["disburse"]),
            node(7, "end", "END", "End", depends_on=["safe_point"]),
        ],
        "invariants": [paid_invariant()],
        "input_schema": {
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "additionalProperties": True,
        },
        "output_schema": {},
        "redact_paths": [],
    }


def create(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/workflows", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def start(client: TestClient, workflow: dict, **extra) -> dict:
    response = client.post("/api/v1/runs", json={"workflow_id": workflow["id"], **extra})
    assert response.status_code == 201, response.text
    return response.json()


def run_scenario(
    client: TestClient, workflow: dict, ordinal: int, fault: str, **parameters
) -> dict:
    created = client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow["id"],
            "name": f"{fault} at {ordinal}",
            "description": "engine test scenario",
            "target_ordinal": ordinal,
            "fault_type": fault,
            "parameters": parameters,
            "seed": 5,
        },
    )
    assert created.status_code == 201, created.text
    response = client.post(f"/api/v1/scenarios/{created.json()['id']}/run")
    assert response.status_code == 201, response.text
    return response.json()


def node_states(run: dict) -> dict[str, dict]:
    return {item["node_key"]: item for item in run["node_states"]}


# --------------------------------------------------------------------------- 1


def test_successful_graph_executes_in_dependency_order(client: TestClient) -> None:
    workflow = create(client, pay_workflow())
    run = start(client, workflow, inputs={"source": "unit-test"})

    assert run["status"] == "SUCCEEDED", run
    assert run["verdict"] == "VERIFIED"
    assert run["chain_valid"] is True
    assert run["inputs"] == {"source": "unit-test"}

    states = node_states(run)
    assert states["start"]["status"] == "PASSED"
    assert states["validate_amount"]["status"] == "PASSED"
    assert states["route_amount"]["status"] == "PASSED"
    assert states["approve"]["status"] == "PASSED"
    assert states["disburse"]["status"] == "PASSED"
    assert states["safe_point"]["status"] == "PASSED"
    assert states["end"]["status"] == "PASSED"
    # The false branch was not taken.
    assert states["auto_pay"]["status"] == "SKIPPED"
    assert states["auto_pay"]["error_code"] == "ROUTE_NOT_SELECTED"

    # Dependencies are persisted and exposed.
    assert states["disburse"]["depends_on"] == ["approve"]
    assert states["approve"]["depends_on"] == ["route_amount"]

    # Ordering: each node started after its dependency passed.
    passed_seq = {
        event["node_key"]: event["seq"]
        for event in run["events"]
        if event["type"] in {"NODE_PASSED", "ACTION_SUCCEEDED"}
    }
    assert passed_seq["route_amount"] < passed_seq["approve"] < passed_seq["disburse"]

    # A run-level output projection exists.
    assert run["outputs"]["disburse"]["kind"] == "PAYMENT_SIMULATED"

    timeline = client.get(f"/api/v1/runs/{run['id']}/timeline").json()
    assert timeline["chain_valid"] is True
    assert timeline["status"] == "SUCCEEDED"
    assert len(timeline["entries"]) == run["event_count"]
    assert all(entry["summary"] for entry in timeline["entries"])
    assert any("Run verified" in entry["summary"] for entry in timeline["entries"])


# --------------------------------------------------------------------------- 2


def test_failed_precondition_stops_before_acting(client: TestClient) -> None:
    workflow = create(client, pay_workflow())
    run = run_scenario_workflow_with_precondition(client, workflow)

    assert run["status"] == "FAILED"
    assert run["failure_code"] == "PRECONDITION_FAILED"
    assert run["failure_ordinal"] == 1

    states = node_states(run)
    assert states["guarded"]["status"] == "FAILED"
    assert states["guarded"]["error_class"] == "PRECONDITION"
    assert states["end"]["status"] in {"PENDING", "SKIPPED", "CANCELLED"}
    assert not any(
        event["type"] == "ACTION_SUCCEEDED" and event["node_key"] == "guarded"
        for event in run["events"]
    )


def run_scenario_workflow_with_precondition(client: TestClient, workflow: dict) -> dict:
    payload = {
        "name": "Precondition gate",
        "description": "A node that requires an impossible state.",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(
                1,
                "guarded",
                "ACTION",
                "Act only when already paid",
                depends_on=["start"],
                kind="CLICK",
                preconditions=[{"path": "invoice.status", "op": "eq", "value": "PAID"}],
                input={"effects": [{"op": "set", "path": "invoice.status", "value": "PAID"}]},
                postconditions=[{"path": "invoice.status", "op": "eq", "value": "PAID"}],
            ),
            node(2, "end", "END", "End", depends_on=["guarded"]),
        ],
        "invariants": [paid_invariant()],
    }
    guarded = create(client, payload)
    return start(client, guarded)


# --------------------------------------------------------------------------- 3


def test_invalid_graph_is_rejected_before_execution(client: TestClient) -> None:
    cyclic = {
        "name": "Cyclic graph",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(1, "a", "ACTION", "A", depends_on=["b"]),
            node(2, "b", "ACTION", "B", depends_on=["a"]),
            node(3, "end", "END", "End", depends_on=["b"]),
        ],
        "invariants": [],
    }
    response = client.post("/api/v1/workflows", json=cyclic)
    assert response.status_code == 422
    assert response.json()["code"] == "GRAPH_CYCLE"
    assert not any(item["origin"] == "LIVE" for item in client.get("/api/v1/workflows").json())

    unknown_dependency = {
        "name": "Unknown dependency",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(1, "a", "ACTION", "A", depends_on=["does_not_exist"]),
        ],
        "invariants": [],
    }
    response = client.post("/api/v1/workflows", json=unknown_dependency)
    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_DEPENDENCY"

    multiple_start = {
        "name": "Two starts",
        "publish": True,
        "actions": [
            node(0, "s1", "START", "Start one"),
            node(1, "s2", "START", "Start two"),
        ],
        "invariants": [],
    }
    response = client.post("/api/v1/workflows", json=multiple_start)
    assert response.status_code == 422
    assert response.json()["code"] == "MULTIPLE_START"

    draft = client.post(
        "/api/v1/workflows",
        json={**cyclic, "name": "Cyclic draft", "publish": False},
    )
    assert draft.status_code == 201
    draft_version_id = draft.json()["versions"][0]["id"]
    publish = client.post(
        f"/api/v1/workflows/{draft.json()['id']}/versions/{draft_version_id}/publish"
    )
    assert publish.status_code == 422
    assert publish.json()["code"] == "GRAPH_CYCLE"


# --------------------------------------------------------------------------- 4


def test_timeout_after_execution_is_reconciled_not_retried(client: TestClient) -> None:
    workflow = create(client, pay_workflow())
    run = run_scenario(client, workflow, 5, "TIMEOUT", timeout_ms=5000)

    assert run["status"] == "SUCCEEDED", run
    states = node_states(run)
    assert states["disburse"]["reconciled"] is True
    assert states["disburse"]["attempt"] == 1, "an ambiguous outcome must not be retried"

    types = [event["type"] for event in run["events"]]
    assert "ACTION_UNKNOWN" in types
    assert "ACTION_CONFIRMED" in types
    assert "NODE_RECONCILED" in types
    assert "NODE_RETRY_SCHEDULED" not in types


# --------------------------------------------------------------------------- 5


def test_failed_postcondition_is_not_success(client: TestClient) -> None:
    payload = {
        "name": "Postcondition mismatch",
        "description": "The handler reports success but the expected state never arrives.",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(
                1,
                "wrong_effect",
                "ACTION",
                "Apply the wrong effect",
                depends_on=["start"],
                input={"effects": [{"op": "set", "path": "invoice.status", "value": "APPROVED"}]},
                postconditions=[{"path": "invoice.status", "op": "eq", "value": "PAID"}],
                side_effect_class="IDEMPOTENT",
            ),
            node(2, "end", "END", "End", depends_on=["wrong_effect"]),
        ],
        "invariants": [paid_invariant()],
    }
    workflow = create(client, payload)
    run = start(client, workflow)

    assert run["status"] == "FAILED"
    assert run["failure_code"] == "POSTCONDITION_FAILED"
    states = node_states(run)
    assert states["wrong_effect"]["status"] == "FAILED"
    assert states["wrong_effect"]["error_class"] == "POSTCONDITION"
    assert "ACTION_SUCCEEDED" not in [event["type"] for event in run["events"]]
    assert run["verdict"] != "VERIFIED"


# --------------------------------------------------------------------------- 6


def test_cancellation_is_a_safe_interruption(client: TestClient) -> None:
    # The demo flow pauses at its authorization-gated action.
    demo = next(item for item in client.get("/api/v1/workflows").json() if item["origin"] == "DEMO")
    paused = start(client, demo)
    assert paused["status"] == "AWAITING_DECISION"

    response = client.post(f"/api/v1/runs/{paused['id']}/cancel?reason=operator%20stop")
    assert response.status_code == 200, response.text
    cancelled = response.json()
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["verdict"] == "CANCELLED"
    assert cancelled["failure_code"] == "CANCELLED"

    states = node_states(cancelled)
    assert states["n5"]["status"] == "CANCELLED"
    assert any(event["type"] == "RUN_CANCELLED" for event in cancelled["events"])

    decisions = client.get("/api/v1/decisions", params={"limit": 200}).json()
    decision = next(item for item in decisions if item["run_id"] == paused["id"])
    assert decision["status"] == "CANCELLED"

    # Cancelling an already-cancelled run is idempotent, not an error.
    again = client.post(f"/api/v1/runs/{paused['id']}/cancel")
    assert again.status_code == 200
    assert again.json()["status"] == "CANCELLED"


def test_cancellation_before_start_does_not_execute_nodes(client: TestClient, settings) -> None:
    from flowstate.repositories import UnitOfWork
    from flowstate.services.cancellation import CancellationRegistry
    from flowstate.services.execution import ExecuteOptions, ExecutionEngine

    workflow = create(client, pay_workflow())
    state = client.app.state
    registry = CancellationRegistry()
    registry.request("placeholder", "pre-cancelled")

    with state.db.session_factory() as session:
        uow = UnitOfWork(session)
        wf = uow.workflows.get(workflow["id"])
        version = uow.versions.get(wf.current_version_id)
        actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
        invariants = list(uow.invariants.list(workflow_version_id=version.id))
        engine = ExecutionEngine(
            uow, state.clock, state.ids, state.settings, state.evidence, cancellations=registry
        )

        # The registry keys by run id; request cancellation for every new id by
        # wrapping the id factory is overkill, so start then cancel at the first
        # boundary via a registry that reports True for any id.
        class AlwaysCancelled(CancellationRegistry):
            def is_requested(self, run_id: str) -> bool:
                return True

            def reason(self, run_id: str) -> str:
                return "pre-cancelled"

        engine.cancellations = AlwaysCancelled()
        run = engine.start_run(
            workflow=wf,
            version=version,
            actions=actions,
            invariants=invariants,
            options=ExecuteOptions(actor="test-operator"),
        )
        session.commit()
        assert run.status == "CANCELLED"
        states = {item.node_key: item.status for item in uow.node_states.list(run_id=run.id)}
        assert "PASSED" not in states.values()
        assert set(states.values()) == {"CANCELLED"}
        assert not any(
            event.type in {"ACTION_STARTED", "NODE_PASSED"} for event in uow.events.read(run.id)
        )


# --------------------------------------------------------------------------- 7


def test_retry_policy_is_bounded_and_audited(client: TestClient) -> None:
    payload = {
        "name": "Retry budget",
        "description": "A transient dependency failure is retried exactly twice.",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(
                1,
                "flaky",
                "ACTION",
                "Call the flaky dependency",
                depends_on=["start"],
                input={"effects": [{"op": "set", "path": "invoice.status", "value": "PAID"}]},
                postconditions=[{"path": "invoice.status", "op": "eq", "value": "PAID"}],
                side_effect_class="IDEMPOTENT",
                retry_policy={"max_attempts": 2, "retry_on": ["HANDLER_ERROR"], "backoff_ms": 0},
            ),
            node(2, "end", "END", "End", depends_on=["flaky"]),
        ],
        "invariants": [paid_invariant()],
    }
    workflow = create(client, payload)
    run = run_scenario(client, workflow, 1, "TRANSIENT_ERROR", failures=5)

    assert run["status"] == "FAILED"
    assert run["failure_code"] == "RETRY_EXHAUSTED"
    states = node_states(run)
    assert states["flaky"]["attempt"] == 2
    assert states["flaky"]["max_attempts"] == 2
    retries = [event for event in run["events"] if event["type"] == "NODE_RETRY_SCHEDULED"]
    assert len(retries) == 1
    assert retries[0]["payload"]["next_attempt"] == 2


def test_transient_failure_succeeds_within_the_retry_budget(client: TestClient) -> None:
    payload = {
        "name": "Retry success",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(
                1,
                "flaky",
                "ACTION",
                "Call the flaky dependency",
                depends_on=["start"],
                input={"effects": [{"op": "set", "path": "invoice.status", "value": "PAID"}]},
                postconditions=[{"path": "invoice.status", "op": "eq", "value": "PAID"}],
                side_effect_class="IDEMPOTENT",
                retry_policy={"max_attempts": 3, "retry_on": ["HANDLER_ERROR"]},
            ),
            node(2, "end", "END", "End", depends_on=["flaky"]),
        ],
        "invariants": [paid_invariant()],
    }
    workflow = create(client, payload)
    run = run_scenario(client, workflow, 1, "TRANSIENT_ERROR", failures=1)

    assert run["status"] == "SUCCEEDED"
    states = node_states(run)
    assert states["flaky"]["attempt"] == 2
    assert states["flaky"]["idempotency_key"], "idempotent actions carry a stable key"


def test_non_idempotent_action_is_never_retried(client: TestClient) -> None:
    payload = {
        "name": "Non-idempotent guard",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(
                1,
                "charge",
                "ACTION",
                "Charge the sandbox gateway",
                depends_on=["start"],
                kind="PAYMENT_SIMULATED",
                input={
                    "effects": [{"op": "set", "path": "invoice.status", "value": "PAID"}],
                    "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                },
                postconditions=[{"path": "invoice.status", "op": "eq", "value": "PAID"}],
                side_effect_class="IRREVERSIBLE",
                retry_policy={"max_attempts": 5, "retry_on": ["HANDLER_ERROR"]},
            ),
            node(2, "end", "END", "End", depends_on=["charge"]),
        ],
        "invariants": [paid_invariant()],
    }
    workflow = create(client, payload)
    run = run_scenario(client, workflow, 1, "TRANSIENT_ERROR", failures=1)

    assert run["status"] == "FAILED"
    states = node_states(run)
    assert states["charge"]["attempt"] == 1
    assert "NODE_RETRY_SCHEDULED" not in [event["type"] for event in run["events"]]


# --------------------------------------------------------------------------- 8


def test_ambiguous_outcome_blocks_then_recovers_from_checkpoint(client: TestClient) -> None:
    payload = {
        "name": "Ambiguous payment",
        "publish": True,
        "actions": [
            node(0, "start", "START", "Start"),
            node(
                1,
                "charge",
                "ACTION",
                "Charge the gateway",
                depends_on=["start"],
                kind="PAYMENT_SIMULATED",
                input={
                    "effects": [
                        {"op": "increment", "path": "invoice.ledger_count", "value": 1},
                        {"op": "set", "path": "invoice.status", "value": "PAID"},
                    ],
                    "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                    "recovery": {
                        "strategy": "RESUME",
                        "reversible": True,
                        "allowed_strategies": ["RESUME"],
                        "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                        "idempotency_path": "invoice.payment_reference",
                        "contract": [
                            {
                                "path": "invoice.status",
                                "op": "in",
                                "value": ["SUBMITTED", "SCHEDULED", "PAID"],
                            }
                        ],
                    },
                },
                postconditions=[{"path": "invoice.ledger_count", "op": "eq", "value": 1}],
                side_effect_class="IRREVERSIBLE",
                retry_policy={"max_attempts": 4, "retry_on": ["HANDLER_ERROR"]},
            ),
            node(2, "end", "END", "End", depends_on=["charge"]),
        ],
        "invariants": [paid_invariant()],
    }
    workflow = create(client, payload)
    blocked = run_scenario(client, workflow, 1, "NETWORK_DROP")

    assert blocked["status"] == "AWAITING_RECOVERY"
    assert blocked["failure_code"] == "ACTION_OUTCOME_UNKNOWN"
    states = node_states(blocked)
    assert states["charge"]["status"] == "BLOCKED"
    assert states["charge"]["error_class"] == "AMBIGUOUS_OUTCOME"
    assert states["charge"]["attempt"] == 1
    types = [event["type"] for event in blocked["events"]]
    assert "ACTION_UNKNOWN" in types
    assert "ACTION_FAILED" in types
    assert "NODE_RETRY_SCHEDULED" not in types
    assert "RUN_VERIFIED" not in types

    recovery = client.post(f"/api/v1/runs/{blocked['id']}/recover", json={"strategy": "RESUME"})
    assert recovery.status_code == 200, recovery.text
    assert recovery.json()["status"] == "VERIFIED"

    resumed = client.get(f"/api/v1/runs/{blocked['id']}").json()
    assert resumed["status"] == "SUCCEEDED"
    assert resumed["verdict"] == "VERIFIED"
    assert node_states(resumed)["charge"]["attempt"] == 2
    assert "RUN_VERIFIED" in [event["type"] for event in resumed["events"]]


# --------------------------------------------------------------------------- 9


def test_checkpoint_resumption_after_human_decision(client: TestClient) -> None:
    demo = next(item for item in client.get("/api/v1/workflows").json() if item["origin"] == "DEMO")
    paused = start(client, demo)
    assert paused["status"] == "AWAITING_DECISION"
    checkpoint_ids = [
        event["payload"].get("checkpoint_id")
        for event in paused["events"]
        if event["type"] == "DECISION_REQUESTED"
    ]
    assert checkpoint_ids and checkpoint_ids[0]

    decision = next(
        item
        for item in client.get("/api/v1/decisions", params={"limit": 200}).json()
        if item["run_id"] == paused["id"]
    )
    approved = client.post(f"/api/v1/decisions/{decision['id']}/approve", json={"note": "ok"})
    assert approved.status_code == 200, approved.text

    resumed = client.get(f"/api/v1/runs/{paused['id']}").json()
    assert resumed["status"] == "SUCCEEDED"
    assert resumed["verdict"] == "VERIFIED"
    started = [
        event["action_ordinal"] for event in resumed["events"] if event["type"] == "ACTION_STARTED"
    ]
    # Earlier actions were not re-executed; execution resumed at the decision point.
    assert started.count(0) == 1
    assert started.count(5) == 2  # blocked attempt, then authorized attempt
    assert started.count(6) == 1


# -------------------------------------------------------------------------- 10


def test_event_history_and_node_projection_persist_across_restart(
    client: TestClient, settings
) -> None:
    workflow = create(client, pay_workflow())
    run = start(client, workflow)
    events_before = client.get(f"/api/v1/runs/{run['id']}/events").json()
    nodes_before = client.get(f"/api/v1/runs/{run['id']}/nodes").json()
    timeline_before = client.get(f"/api/v1/runs/{run['id']}/timeline").json()

    with TestClient(create_app(settings)) as reopened:
        events_after = reopened.get(f"/api/v1/runs/{run['id']}/events").json()
        nodes_after = reopened.get(f"/api/v1/runs/{run['id']}/nodes").json()
        timeline_after = reopened.get(f"/api/v1/runs/{run['id']}/timeline").json()
        verification = reopened.get(f"/api/v1/runs/{run['id']}/events/verify").json()

    assert len(events_after) == len(events_before) == run["event_count"]
    assert [item["seq"] for item in events_after] == list(range(len(events_after)))
    assert nodes_after == nodes_before
    assert [item["summary"] for item in timeline_after["entries"]] == [
        item["summary"] for item in timeline_before["entries"]
    ]
    assert verification["valid"] is True
    assert timeline_after["chain_valid"] is True


# -------------------------------------------------------------------------- 11


def test_inputs_are_validated_and_redacted(client: TestClient) -> None:
    payload = {
        "name": "Redacted inputs",
        "publish": True,
        "input_schema": {
            "type": "object",
            "required": ["api_token"],
            "properties": {"api_token": {"type": "string", "minLength": 4}},
        },
        "actions": [
            node(0, "start", "START", "Start"),
            node(
                1,
                "act",
                "ACTION",
                "Act",
                depends_on=["start"],
                input={"effects": [{"op": "set", "path": "invoice.status", "value": "PAID"}]},
                postconditions=[{"path": "invoice.status", "op": "eq", "value": "PAID"}],
            ),
            node(2, "end", "END", "End", depends_on=["act"]),
        ],
        "invariants": [paid_invariant()],
        "redact_paths": ["invoice.approver"],
    }
    workflow = create(client, payload)

    rejected = client.post(
        "/api/v1/runs", json={"workflow_id": workflow["id"], "inputs": {"api_token": "x"}}
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "INPUT_SCHEMA_VALIDATION_FAILED"

    run = start(client, workflow, inputs={"api_token": "super-secret-value"})
    assert run["status"] == "SUCCEEDED"
    assert run["inputs"] == {"api_token": "[redacted]"}

    events = client.get(f"/api/v1/runs/{run['id']}/events").json()
    serialized = str(events)
    assert "super-secret-value" not in serialized
    assert "[redacted]" in serialized
