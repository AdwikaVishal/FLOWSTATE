"""Real Playwright tests for the controlled demo and the browser driver.

Marked ``browser`` so the default gate stays fast. Run with:

    PYTHONPATH=backend .venv/bin/pytest backend/tests/browser -m browser -q
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flowstate.capture.demo_app.router import router as demo_router
from flowstate.config import Settings
from flowstate.main import create_app

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def demo_server() -> Iterator[str]:
    port = _free_port()
    demo = FastAPI()
    demo.include_router(demo_router)
    config = uvicorn.Config(demo, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/demo-app/", timeout=1).status_code == 200:
                break
        except Exception:
            pass
        threading.Event().wait(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("demo app did not start")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def browser_client(tmp_path: Path, demo_server: str) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'browser.db'}",
        log_json=False,
        auto_seed_demo=False,
        demo_seed_enabled=False,
        demo_app_url=f"{demo_server}/demo-app/",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


def _launch_chromium():  # type: ignore[no-untyped-def]
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:
        playwright.stop()
        pytest.skip(f"Chromium is unavailable: {exc}")
    return playwright, browser


def _record_demo(base: str) -> list[dict]:
    playwright, browser = _launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{base}/demo-app/?capture=1", wait_until="domcontentloaded")
        page.get_by_label("Supplier ID").fill("SUP-77")
        page.get_by_label("Invoice ID").fill("INV-1001")
        page.get_by_role("button", name="Load invoice").click()
        page.wait_for_selector('#invoice-summary[data-invoice-id="INV-1001"]')
        page.get_by_role("button", name="Validate invoice").click()
        page.wait_for_selector('#validation-result[data-state="VALID"]')
        page.wait_for_timeout(250)
        events = page.evaluate("window.__flowstateCaptured")
        assert isinstance(events, list)
        return events
    finally:
        browser.close()
        playwright.stop()


def test_demo_app_records_structured_non_sensitive_events(demo_server: str) -> None:
    events = _record_demo(demo_server)
    assert [event["kind"] for event in events] == ["TYPE", "TYPE", "CLICK", "CLICK"]
    assert [event["param"] for event in events] == [
        "supplier_id",
        "invoice_id",
        "",
        "",
    ]
    assert events[0]["value"] == "SUP-77"
    assert events[0]["page_before"]["page"] == "queue"
    assert events[0]["page_after"]["page"] == "queue"
    assert events[2]["page_after"]["page"] == "invoice"
    assert events[2]["page_after"]["state"]["invoice_id"] == "INV-1001"
    assert events[3]["page_after"]["state"]["validation"] == "VALID"
    assert events[0]["locator"]["name"] == "Supplier ID"
    assert events[0]["locator"]["role"] == "textbox"
    assert events[0]["locator"]["css"] == "#supplier-id"
    assert all(not event["sensitive"] for event in events)


def test_real_playwright_recording_can_be_validated_saved_and_replayed(
    browser_client: TestClient, demo_server: str
) -> None:
    events = _record_demo(demo_server)
    capture = browser_client.post("/api/v1/captures", json={"label": "Browser invoice lookup"})
    assert capture.status_code == 201, capture.text
    session_id = capture.json()["id"]

    recorded = browser_client.post(f"/api/v1/captures/{session_id}/events", json={"events": events})
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["event_count"] == 4
    stopped = browser_client.post(f"/api/v1/captures/{session_id}/stop")
    assert stopped.status_code == 200, stopped.text

    validated = browser_client.post(f"/api/v1/captures/{session_id}/validate")
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["validation"]["ok"] is True, body["validation"]
    assert body["status"] == "VALIDATED"

    saved = browser_client.post(
        f"/api/v1/captures/{session_id}/workflow",
        json={"name": "Browser invoice lookup", "publish": True},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["version"]["status"] == "PUBLISHED"

    first = browser_client.post(
        f"/api/v1/captures/{session_id}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}},
    )
    assert first.status_code == 201, first.text
    run = first.json()
    assert run["status"] == "SUCCEEDED", run
    assert run["verdict"] == "VERIFIED"
    assert run["driver"] == "playwright-v1"
    assert any(item["kind"] == "SCREENSHOT" for item in run["evidence"])

    second = browser_client.post(
        f"/api/v1/captures/{session_id}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1003"}},
    )
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "SUCCEEDED"
    assert second.json()["id"] != run["id"]


def test_real_playwright_selector_drift_fails_safely(
    browser_client: TestClient, demo_server: str
) -> None:
    events = _record_demo(demo_server)
    capture = browser_client.post("/api/v1/captures", json={"label": "Drift check"})
    session_id = capture.json()["id"]
    browser_client.post(f"/api/v1/captures/{session_id}/events", json={"events": events})
    browser_client.post(f"/api/v1/captures/{session_id}/stop")
    validated = browser_client.post(f"/api/v1/captures/{session_id}/validate")
    assert validated.json()["validation"]["ok"] is True
    saved = browser_client.post(
        f"/api/v1/captures/{session_id}/workflow",
        json={"name": "Drift check", "publish": True},
    )
    assert saved.status_code == 201, saved.text

    drifted = browser_client.post(
        f"/api/v1/captures/{session_id}/replay",
        json={
            "inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"},
            "variant": "changed",
        },
    )
    assert drifted.status_code == 201, drifted.text
    run = drifted.json()
    assert run["status"] == "FAILED"
    assert run["failure_code"] == "SELECTOR_NOT_FOUND"
    assert run["verdict"] == "FAILED"


def test_real_playwright_chaos_selector_drift_is_detected(
    browser_client: TestClient, demo_server: str
) -> None:
    events = _record_demo(demo_server)
    capture = browser_client.post("/api/v1/captures", json={"label": "Chaos drift"})
    session_id = capture.json()["id"]
    browser_client.post(f"/api/v1/captures/{session_id}/events", json={"events": events})
    browser_client.post(f"/api/v1/captures/{session_id}/stop")
    validated = browser_client.post(f"/api/v1/captures/{session_id}/validate")
    assert validated.json()["validation"]["ok"] is True, validated.text
    saved = browser_client.post(
        f"/api/v1/captures/{session_id}/workflow",
        json={"name": "Chaos drift workflow", "publish": True},
    )
    assert saved.status_code == 201, saved.text
    workflow_id = saved.json()["workflow"]["id"]

    scenario = browser_client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow_id,
            "name": "Changed browser selector in Chromium",
            "description": "The real demo layout changes before the recorded load click.",
            "target_ordinal": 5,
            "fault_type": "SELECTOR_DRIFT",
            "parameters": {},
            "input_fixtures": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"},
            "seed": 91,
        },
    )
    assert scenario.status_code == 201, scenario.text

    report = browser_client.post(f"/api/v1/scenarios/{scenario.json()['id']}/evaluate")
    assert report.status_code == 201, report.text
    body = report.json()
    assert body["baseline"]["status"] == "SUCCEEDED"
    assert body["fault"]["status"] == "FAILED"
    assert body["fault"]["failure_code"] == "SELECTOR_NOT_FOUND"
    assert body["failed_safely"] is True
    assert body["failure_action"]["error_code"] == "SELECTOR_NOT_FOUND"
