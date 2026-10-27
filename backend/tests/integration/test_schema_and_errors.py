"""Schema initialisation, system reporting and problem+json errors."""

from __future__ import annotations

from fastapi.testclient import TestClient
from flowstate.db.engine import SCHEMA_VERSION, read_schema_version
from flowstate.main import create_app
from sqlalchemy import inspect


def test_database_initialises_with_schema_version_and_tables(client: TestClient) -> None:
    engine = client.app.state.db.engine

    assert client.app.state.schema_version == SCHEMA_VERSION
    assert read_schema_version(engine) == SCHEMA_VERSION

    tables = set(inspect(engine).get_table_names())
    assert {
        "workflows",
        "workflow_versions",
        "action_definitions",
        "invariants",
        "execution_runs",
        "execution_events",
        "fault_scenarios",
        "human_decisions",
        "capability_tokens",
        "checkpoints",
        "recovery_attempts",
        "regression_tests",
        "schema_meta",
    } <= tables


def test_database_initialisation_is_idempotent(settings) -> None:
    from flowstate.db.engine import Database

    db = Database(settings.resolved_database_url)
    assert db.init() == SCHEMA_VERSION
    assert db.init() == SCHEMA_VERSION
    assert db.healthy() is True
    assert read_schema_version(db.engine) == SCHEMA_VERSION
    db.engine.dispose()


def test_system_info_reports_configuration_without_secrets(client: TestClient) -> None:
    body = client.get("/api/v1/system").json()

    assert body["app"] == "FLOWSTATE AI"
    assert body["database"]["healthy"] is True
    assert body["database"]["schema_version"] == SCHEMA_VERSION
    assert body["settings"]["auth_mode"] == "open-development"
    assert "http://localhost:5173" in body["settings"]["cors_origins"]
    assert "secret_key" not in body["settings"]
    assert body["driver"]["name"] == "simulated-v1"


def test_unknown_resource_is_a_problem_document(client: TestClient) -> None:
    response = client.get("/api/v1/workflows/not-a-workflow")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "WORKFLOW_NOT_FOUND"
    assert body["title"] == "Workflow Not Found"
    assert body["status"] == 404
    assert body["instance"].startswith("urn:request:")
    assert response.headers["x-request-id"]


def test_validation_failure_lists_fields(client: TestClient) -> None:
    response = client.post("/api/v1/workflows", json={"name": "x"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert "name" in body["errors"]


def test_request_id_header_is_echoed(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "test-request-1"})

    assert response.headers["x-request-id"] == "test-request-1"


def test_app_factory_can_run_without_demo_data(tmp_path) -> None:
    from flowstate.config import Settings

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'empty.db'}",
        log_json=False,
        auto_seed_demo=False,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/demo").json()["seeded"] is False
        assert client.get("/api/v1/workflows").json() == []
        assert client.get("/api/v1/system/overview").json()["counts"]["runs"] == 0
