"""Typed Alan adapter against a live, authenticated backend."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator

import anyio
import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from flowstate.alan.cli import main as cli_main
from flowstate.alan.client import AdapterError, AlanConfig, FlowstateClient
from flowstate.alan.mcp_server import create_server
from flowstate.alan.service import AlanOperations
from flowstate.config import Settings
from flowstate.main import create_app

TOKEN = "test-alan-token"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    data_dir = tmp_path_factory.mktemp("alan-api")
    port = _free_port()
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'alan.db'}",
        log_json=False,
        auto_seed_demo=True,
        demo_seed_enabled=True,
        api_token=TOKEN,
        decision_ttl_seconds=600,
    )
    app: FastAPI = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}/api/v1"
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("live backend did not start")
    yield base, TOKEN
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def ops(live_api: tuple[str, str]) -> Iterator[AlanOperations]:
    base, token = live_api
    client = FlowstateClient(AlanConfig(base_url=base, token=token, retries=0))
    try:
        yield AlanOperations(client)
    finally:
        client.close()


def _confirmed(ops: AlanOperations, operation: str, **kwargs):  # type: ignore[no-untyped-def]
    preview = getattr(ops, operation)(**kwargs)
    assert preview["code"] == "CONFIRMATION_REQUIRED", preview
    token = preview["details"]["confirmation_token"]
    return getattr(ops, operation)(**kwargs, confirmation_token=token)


def test_list_and_inspect_workflows(ops: AlanOperations) -> None:
    listed = ops.list_workflows()
    assert listed["ok"] is True
    names = {item["name"] for item in listed["data"]["workflows"]}
    assert {"Invoice Approval", "Invoice Discrepancy Review", "Vendor Payout"} <= names

    inspected = ops.inspect_workflow("invoice-approval")
    assert inspected["ok"] is True, inspected
    data = inspected["data"]
    assert data["execution_kind"] == "simulated"
    assert data["driver"] == "simulated-v1"
    assert data["executable"] is True
    assert data["version"]["required_inputs"] == []


def test_ambiguous_and_unknown_workflow(ops: AlanOperations) -> None:
    ambiguous = ops.inspect_workflow("invoice")
    assert ambiguous["ok"] is False
    assert ambiguous["code"] == "WORKFLOW_AMBIGUOUS"
    assert len(ambiguous["details"]["candidates"]) >= 2

    missing = ops.inspect_workflow("does-not-exist")
    assert missing["code"] == "WORKFLOW_NOT_FOUND"
    assert missing["details"]["available"]


def test_missing_declared_inputs_are_reported(ops: AlanOperations) -> None:
    created = ops.client.post(
        "/workflows",
        body={
            "name": "Alan input requirement fixture",
            "description": "Requires an invoice id.",
            "publish": True,
            "actions": [
                {
                    "ordinal": 0,
                    "node_key": "start",
                    "node_type": "START",
                    "kind": "ASSERT",
                    "label": "Start",
                },
                {
                    "ordinal": 1,
                    "node_key": "end",
                    "node_type": "END",
                    "kind": "ASSERT",
                    "label": "End",
                },
            ],
            "input_schema": {
                "type": "object",
                "properties": {"invoice_id": {"type": "string"}},
                "required": ["invoice_id"],
            },
        },
    )
    assert created.get("id")

    missing = ops.run_workflow("Alan input requirement fixture", inputs={})
    assert missing["ok"] is False
    assert missing["code"] == "MISSING_INPUTS"
    assert missing["details"]["missing"] == ["invoice_id"]

    preview = ops.run_workflow("Alan input requirement fixture", inputs={"invoice_id": "INV-1"})
    assert preview["code"] == "CONFIRMATION_REQUIRED"
    assert preview["details"]["summary"]


def test_run_requires_confirmation_and_never_claims_success(ops: AlanOperations) -> None:
    before = len(ops.client.get("/runs", params={"limit": 200}))
    preview = ops.run_workflow("invoice-approval")
    assert preview["code"] == "CONFIRMATION_REQUIRED"
    assert len(ops.client.get("/runs", params={"limit": 200})) == before

    run = _confirmed(
        ops,
        "run_workflow",
        workflow="invoice-approval",
        idempotency_key="alan-test-unconfirmed-success-0001",
    )
    assert run["ok"] is True, run
    assert run["data"]["status"] == "AWAITING_DECISION"
    assert run["data"]["is_success"] is False
    assert run["data"]["simulated"] is True
    assert run["data"]["blockers"]

    status = ops.get_execution_status(run["data"]["run_id"])
    assert status["ok"] is True
    assert status["data"]["is_success"] is False
    assert status["data"]["blockers"]

    explanation = ops.explain_failure(run["data"]["run_id"])
    assert explanation["ok"] is True
    assert explanation["data"]["never_claim_success"] is True
    assert any("Decision Inbox" in item for item in explanation["data"]["next_steps"])


def test_inspect_evidence_verifies_persisted_hash(ops: AlanOperations) -> None:
    run = _confirmed(
        ops,
        "run_workflow",
        workflow="invoice-approval",
        idempotency_key="alan-test-evidence-0001",
    )["data"]
    listed = ops.inspect_evidence(run["run_id"])
    assert listed["ok"] is True
    assert listed["data"]["evidence"]
    evidence_id = listed["data"]["evidence"][0]["id"]
    inspected = ops.inspect_evidence(run["run_id"], evidence_id)
    assert inspected["ok"] is True
    assert inspected["data"]["evidence"]["hash_matches"] is True
    assert inspected["data"]["content_excerpt"] is not None


def test_chaos_report_explanation_and_verified_recovery(ops: AlanOperations) -> None:
    report = _confirmed(
        ops,
        "run_chaos_test",
        workflow="invoice-approval",
        scenario="Connection drop before send",
        idempotency_key="alan-test-chaos-network-drop-0001",
    )
    assert report["ok"] is True, report
    assert report["data"]["evaluation"] == "SAFE_FAILURE_WITHOUT_VIOLATION"
    assert report["data"]["fault"]["status"] == "AWAITING_RECOVERY"
    assert report["data"]["fault_verified"] is False
    run_id = report["data"]["fault"]["run_id"]

    explanation = ops.explain_failure(run_id)
    assert explanation["ok"] is True
    assert any("recovery" in item.lower() for item in explanation["data"]["next_steps"])

    recovery = _confirmed(ops, "request_recovery", run_id=run_id, strategy="RESUME")
    assert recovery["ok"] is True, recovery
    assert recovery["data"]["verified"] is True
    assert recovery["data"]["run"]["status"] == "SUCCEEDED"
    assert recovery["data"]["run"]["verdict"] == "VERIFIED"


def test_idempotent_run_returns_the_same_run(ops: AlanOperations) -> None:
    preview = ops.run_workflow("invoice-approval")
    token = preview["details"]["confirmation_token"]
    first = ops.run_workflow(
        "invoice-approval",
        confirmation_token=token,
        idempotency_key="alan-adapter-duplicate-0001",
    )
    second = ops.run_workflow(
        "invoice-approval",
        confirmation_token=token,
        idempotency_key="alan-adapter-duplicate-0001",
    )
    assert first["ok"] is True, first
    assert second["ok"] is True, second
    assert first["data"]["run_id"] == second["data"]["run_id"]


def test_regression_list_and_confirmed_replay(ops: AlanOperations) -> None:
    listed = ops.list_regression_tests()
    assert listed["ok"] is True
    tests = listed["data"]["tests"]
    assert tests
    test_id = next(item["id"] for item in tests if item["status"] != "QUARANTINED")

    preview = ops.run_regression_tests(test_ids=[test_id])
    assert preview["code"] == "CONFIRMATION_REQUIRED"

    result = _confirmed(ops, "run_regression_tests", test_ids=[test_id])
    assert result["ok"] is True, result
    assert result["data"]["results"][0]["test_id"] == test_id


def test_read_only_and_workflow_allowlist(ops: AlanOperations) -> None:
    read_only = AlanOperations(ops.client, read_only=True)
    blocked = read_only.run_workflow("invoice-approval")
    assert blocked["code"] == "READ_ONLY_MODE"

    scoped = AlanOperations(ops.client, allowed_workflows={"vendor-payout"})
    not_allowed = scoped.inspect_workflow("invoice-approval")
    assert not_allowed["code"] == "WORKFLOW_NOT_ALLOWED"


def test_auth_failure_and_transport_errors(live_api: tuple[str, str]) -> None:
    base, _token = live_api
    wrong = FlowstateClient(AlanConfig(base_url=base, token="wrong-token", retries=0))
    try:
        result = AlanOperations(wrong).list_workflows()
        assert result["ok"] is False
        assert result["code"] in {"UNAUTHORIZED", "INVALID_TOKEN"}
        assert result["backend_status"] == 401
    finally:
        wrong.close()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    timeout_client = FlowstateClient(
        AlanConfig(base_url=base, token=TOKEN, retries=0),
        http=httpx.Client(transport=httpx.MockTransport(timeout_handler)),
    )
    try:
        result = AlanOperations(timeout_client).list_workflows()
        assert result["code"] == "BACKEND_TIMEOUT"
    finally:
        timeout_client.close()

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", request=request)

    malformed_client = FlowstateClient(
        AlanConfig(base_url=base, token=TOKEN, retries=0),
        http=httpx.Client(transport=httpx.MockTransport(malformed_handler)),
    )
    try:
        result = AlanOperations(malformed_client).list_workflows()
        assert result["code"] == "MALFORMED_BACKEND_RESPONSE"
    finally:
        malformed_client.close()


def test_insecure_base_url_is_rejected() -> None:
    with pytest.raises(AdapterError) as exc:
        FlowstateClient(AlanConfig(base_url="http://example.com/api/v1"))
    assert exc.value.code == "INSECURE_BASE_URL"


def test_cli_preview_confirm_and_compact_output(
    ops: AlanOperations, live_api: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    base, token = live_api
    import os

    os.environ["FLOWSTATE_API_TOKEN"] = token
    try:
        code = cli_main(["--base-url", base, "--compact", "inspect-workflow", "invoice-approval"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["ok"] is True

        code = cli_main(["--base-url", base, "--compact", "run-workflow", "invoice-approval"])
        assert code == 2
        preview = json.loads(capsys.readouterr().out.strip())
        assert preview["code"] == "CONFIRMATION_REQUIRED"
        confirmation = preview["details"]["confirmation_token"]

        code = cli_main(
            [
                "--base-url",
                base,
                "--compact",
                "run-workflow",
                "invoice-approval",
                "--confirm",
                confirmation,
            ]
        )
        assert code == 0
        run = json.loads(capsys.readouterr().out.strip())
        assert run["data"]["status"] == "AWAITING_DECISION"
    finally:
        os.environ.pop("FLOWSTATE_API_TOKEN", None)


def test_mcp_server_exposes_typed_tools(ops: AlanOperations) -> None:
    server = create_server(operations=ops)
    tools = anyio.run(server.list_tools)
    names = {tool.name for tool in tools}
    assert {
        "list_workflows",
        "inspect_workflow",
        "run_workflow",
        "get_execution_status",
        "run_chaos_test",
        "explain_failure",
        "list_blockers",
        "inspect_evidence",
        "request_recovery",
        "list_regression_tests",
        "run_regression_tests",
    } == names

    result = anyio.run(server.call_tool, "list_workflows", {})
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    assert structured
    assert structured["ok"] is True
    assert structured["data"]["workflows"]
