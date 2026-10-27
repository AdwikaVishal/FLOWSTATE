"""Health, readiness and CORS contract tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from flowstate.api.deps import get_actor
from flowstate.config import Settings
from flowstate.domain.enums import ActorKind, ActorRole
from flowstate.main import create_app
from pydantic import ValidationError


def test_health_endpoint_reports_build_info(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "FLOWSTATE AI"
    assert body["version"]
    assert body["time"]


def test_readiness_checks_database_and_schema(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] is True
    assert body["schema_version"] == 8
    assert body["checks"]["evidence_store"] in {"pass", "warn"}


def test_legacy_probes_and_service_root(client: TestClient) -> None:
    healthz = client.get("/healthz")
    assert healthz.status_code == 200
    assert healthz.json()["status"] == "ok"

    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["api"] == "/api/v1"


def test_cors_allows_the_configured_frontend_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_an_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_bearer_mode_protects_every_non_health_route(tmp_path) -> None:
    from flowstate.config import Settings

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        log_json=False,
        auto_seed_demo=False,
        api_token="test-token",
    )
    with TestClient(create_app(settings)) as secured:
        assert secured.get("/api/v1/health").status_code == 200

        unauthorized = secured.get("/api/v1/workflows")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["content-type"].startswith("application/problem+json")
        assert unauthorized.json()["code"] == "UNAUTHORIZED"
        unauthorized_stream = secured.get(
            "/api/v1/runs/not-a-run/events/stream", params={"once": True}
        )
        assert unauthorized_stream.status_code == 401

        authorized = secured.get(
            "/api/v1/workflows", headers={"Authorization": "Bearer test-token"}
        )
        assert authorized.status_code == 200


def test_empty_token_is_treated_as_open_development(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'open.db'}",
        log_json=False,
        auto_seed_demo=False,
        api_token="",
    )
    assert settings.api_token is None
    assert settings.auth_mode == "open-development"

    with TestClient(create_app(settings)) as open_client:
        assert open_client.get("/api/v1/workflows").status_code == 200


def test_non_development_requires_strong_authentication_configuration(tmp_path) -> None:
    with pytest.raises(ValidationError, match="FLOWSTATE_API_TOKEN"):
        Settings(
            environment="production",
            data_dir=tmp_path,
            cors_origins=[],
            auto_seed_demo=False,
            demo_seed_enabled=False,
        )

    settings = Settings(
        environment="production",
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'production.db'}",
        cors_origins=["https://console.example.com"],
        api_token="a" * 32,
        secret_key="b" * 32,
        auto_seed_demo=False,
        demo_seed_enabled=False,
        capture_enabled=False,
    )
    app = create_app(settings)

    from starlette.requests import Request

    with TestClient(app) as secured:
        actor = get_actor(
            Request({"type": "http", "app": app}),
            actor_id="spoofed-actor",
            actor_kind="AGENT",
            actor_role="ADMIN",
        )
        assert actor.id == "authenticated-api-client"
        assert actor.kind == ActorKind.AGENT
        assert actor.role == ActorRole.OPERATOR
        assert secured.get("/demo-app/").status_code == 404
        assert secured.get("/api/v1/workflows").status_code == 401
