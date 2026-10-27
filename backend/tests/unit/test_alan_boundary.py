"""Adapter boundary: confirmation tokens, URL safety and MCP ingress auth."""

from __future__ import annotations

import httpx
import pytest
from flowstate.alan.client import AlanConfig, FlowstateClient
from flowstate.alan.mcp_server import BearerTokenMiddleware
from flowstate.alan.service import confirmation_token
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def test_confirmation_token_binds_the_exact_action_and_arguments() -> None:
    first = confirmation_token("run_workflow", {"workflow_id": "a", "inputs": {"x": 1}})
    same = confirmation_token("run_workflow", {"inputs": {"x": 1}, "workflow_id": "a"})
    changed = confirmation_token("run_workflow", {"workflow_id": "a", "inputs": {"x": 2}})
    other_action = confirmation_token("request_recovery", {"workflow_id": "a", "inputs": {"x": 1}})
    assert first == same
    assert first != changed
    assert first != other_action
    assert len(first) == 32


def test_base_url_never_accepts_remote_plaintext_or_embedded_credentials() -> None:
    with pytest.raises(Exception) as exc:
        FlowstateClient(AlanConfig(base_url="http://example.com/api/v1"))
    assert getattr(exc.value, "code", None) == "INSECURE_BASE_URL"

    with pytest.raises(Exception) as exc:
        FlowstateClient(AlanConfig(base_url="https://user:pass@example.com/api/v1"))
    assert getattr(exc.value, "code", None) == "INVALID_BASE_URL"


def test_mcp_ingress_requires_the_configured_bearer_token() -> None:
    async def endpoint(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])])
    wrapped = BearerTokenMiddleware(app, token="mcp-secret")

    async def call(headers: dict[str, str]) -> int:
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/mcp", headers=headers, json={})
            return response.status_code

    import anyio

    assert anyio.run(call, {}) == 401
    assert anyio.run(call, {"Authorization": "Bearer wrong"}) == 401
    assert anyio.run(call, {"Authorization": "Bearer mcp-secret"}) == 200
