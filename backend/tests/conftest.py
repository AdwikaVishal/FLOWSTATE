"""Shared fixtures for the API and persistence tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from flowstate.config import Settings
from flowstate.demo import fixtures
from flowstate.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        log_json=False,
        auto_seed_demo=True,
        demo_seed_enabled=True,
        api_token=None,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    # The context manager runs the lifespan, which initialises the schema and
    # seeds the labelled demonstration workspace.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def demo_workflow(client: TestClient) -> dict:
    response = client.get("/api/v1/workflows")
    assert response.status_code == 200
    for workflow in response.json():
        if workflow["slug"] == fixtures.DEMO_SLUG:
            return workflow
    raise AssertionError("demo workflow was not seeded")
