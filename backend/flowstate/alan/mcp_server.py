"""MCP server exposing the typed FLOWSTATE operations to Alan.

Alan connects to this process as a custom MCP connector. The server is a narrow
transport adapter: it validates arguments, requires a confirmation token before a
mutation, and returns the backend's persisted result. It cannot approve human
decisions, change workflow definitions, or bypass backend policy.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.types import ASGIApp, Receive, Scope, Send

from flowstate.alan.client import AlanConfig, FlowstateClient
from flowstate.alan.service import AlanOperations


def create_server(
    *,
    operations: AlanOperations | None = None,
    client: FlowstateClient | None = None,
) -> MCPServer:
    owned_client = client
    ops = operations
    if ops is None:
        owned_client = owned_client or FlowstateClient(AlanConfig())
        ops = AlanOperations(owned_client)
    assert ops is not None

    server = MCPServer(
        name="flowstate",
        title="FLOWSTATE",
        version="1.0.0",
        description="Typed access to FLOWSTATE workflows, executions, chaos reports and recovery.",
        instructions=(
            "FLOWSTATE is a workflow reliability platform. Resolve the workflow before "
            "running it, ask for declared inputs that are missing, and treat simulated-v1 "
            "and playwright-v1 runs as different execution kinds. Consequential actions "
            "(run_workflow, run_chaos_test, request_recovery, run_regression_tests) return "
            "CONFIRMATION_REQUIRED with a confirmation_token; show the summary to the user "
            "and only repeat the call with that token after explicit approval. Never claim "
            "success unless a tool returns is_success true or verified true from the "
            "backend. AWAITING_DECISION, AWAITING_RECOVERY, UNKNOWN, INCONCLUSIVE and "
            "FAILED are not success. The adapter cannot approve decisions."
        ),
    )

    @server.tool(
        name="list_workflows",
        description="List FLOWSTATE workflows, optionally filtered by a text query or origin.",
    )
    def list_workflows(query: str = "", origin: str | None = None) -> dict[str, Any]:
        return ops.list_workflows(query=query, origin=origin)

    @server.tool(
        name="inspect_workflow",
        description=(
            "Resolve a workflow by id, slug, name or distinctive phrase and return its "
            "current version, declared inputs, actions, channels and invariants."
        ),
    )
    def inspect_workflow(
        workflow: str | None = None, version_id: str | None = None
    ) -> dict[str, Any]:
        return ops.inspect_workflow(workflow=workflow, version_id=version_id)

    @server.tool(
        name="run_workflow",
        description=(
            "Run a published workflow. Returns MISSING_INPUTS when declared inputs are "
            "absent and CONFIRMATION_REQUIRED before creating a run. Repeat with the exact "
            "confirmation_token only after the user explicitly approves. Simulated and "
            "Chromium runs are labelled by driver."
        ),
    )
    def run_workflow(
        workflow: str | None = None,
        inputs: dict[str, Any] | None = None,
        version_id: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return ops.run_workflow(
            workflow=workflow,
            inputs=inputs,
            version_id=version_id,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="get_execution_status",
        description=(
            "Read persisted execution status, verdicts, failed nodes, blockers, recovery "
            "attempts and evidence. is_success is true only for SUCCEEDED/VERIFIED."
        ),
    )
    def get_execution_status(run_id: str) -> dict[str, Any]:
        return ops.get_execution_status(run_id=run_id)

    @server.tool(
        name="run_chaos_test",
        description=(
            "Evaluate an existing chaos scenario: an authorized baseline and the "
            "fault-injected run with the recorded seed and inputs. Returns "
            "CONFIRMATION_REQUIRED before creating the two runs. Choices are returned when "
            "the scenario is not specific enough."
        ),
    )
    def run_chaos_test(
        workflow: str | None = None,
        scenario: str | None = None,
        fault_type: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return ops.run_chaos_test(
            workflow=workflow,
            scenario=scenario,
            fault_type=fault_type,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="explain_failure",
        description=(
            "Explain a non-success run from persisted data: failure code, first violated "
            "invariant, failed node, blockers, evidence, recovery plan and next steps."
        ),
    )
    def explain_failure(run_id: str) -> dict[str, Any]:
        return ops.explain_failure(run_id=run_id)

    @server.tool(
        name="list_blockers",
        description=(
            "List open blockers, optionally for one run, with evidence counts and required roles."
        ),
    )
    def list_blockers(run_id: str | None = None, open_only: bool = True) -> dict[str, Any]:
        return ops.list_blockers(run_id=run_id, open_only=open_only)

    @server.tool(
        name="inspect_evidence",
        description=(
            "List a run's evidence records or verify one record's hash. JSON evidence under "
            "128 KB is returned as a content excerpt."
        ),
    )
    def inspect_evidence(run_id: str, evidence_id: str | None = None) -> dict[str, Any]:
        return ops.inspect_evidence(run_id=run_id, evidence_id=evidence_id)

    @server.tool(
        name="request_recovery",
        description=(
            "Request checkpoint recovery for a failed or awaiting-recovery run. Returns a "
            "confirmation requirement first. The backend re-checks policy, evidence, "
            "preconditions and the recovery contract; verified is true only when both the "
            "attempt and the run verify."
        ),
    )
    def request_recovery(
        run_id: str,
        strategy: str = "RESUME",
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return ops.request_recovery(
            run_id=run_id,
            strategy=strategy,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="list_regression_tests",
        description=(
            "List regression tests with their status, expected invariant and recorded scenario."
        ),
    )
    def list_regression_tests(workflow: str | None = None) -> dict[str, Any]:
        return ops.list_regression_tests(workflow=workflow)

    @server.tool(
        name="run_regression_tests",
        description=(
            "Replay selected regression tests, or all non-draft/non-quarantined tests. "
            "Returns a confirmation requirement first because every replay creates a run."
        ),
    )
    def run_regression_tests(
        test_ids: list[str] | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return ops.run_regression_tests(
            test_ids=test_ids,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )

    return server


class BearerTokenMiddleware:
    """Constant-time bearer check for the streamable HTTP connector."""

    def __init__(self, app: ASGIApp, *, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.token:
            headers = dict(scope.get("headers") or [])
            raw = headers.get(b"authorization", b"").decode("latin-1")
            expected = f"Bearer {self.token}"
            if not secrets.compare_digest(raw, expected):
                body = b'{"code":"UNAUTHORIZED","detail":"A valid MCP bearer token is required."}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def _loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flowstate-alan-mcp",
        description="Expose FLOWSTATE to Alan as an MCP server.",
    )
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default=os.getenv("FLOWSTATE_ALAN_MCP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("FLOWSTATE_ALAN_MCP_PORT", "8765"))
    )
    args = parser.parse_args(argv)

    server = create_server()
    if args.transport == "stdio":
        server.run("stdio")
        return 0

    token = os.getenv("FLOWSTATE_ALAN_MCP_TOKEN")
    if not _loopback(args.host) and not token:
        print(
            "Refusing to expose the MCP server on a non-loopback host without "
            "FLOWSTATE_ALAN_MCP_TOKEN.",
            file=sys.stderr,
        )
        return 2
    app = server.streamable_http_app(streamable_http_path="/mcp", host=args.host)
    app.add_middleware(BearerTokenMiddleware, token=token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
