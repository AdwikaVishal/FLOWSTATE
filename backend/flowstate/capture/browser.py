"""Playwright browser driver for learned workflows.

The driver is the *only* component that talks to a browser. It is deliberately
narrow:

* it navigates to the controlled demo origin only (``ActionPolicy`` decides);
* it resolves ranked locators in a fixed order and refuses ambiguous matches;
* it performs one typed action at a time and then waits for an observable
  postcondition instead of treating a completed call as success;
* timeouts after an action are ``UNKNOWN`` and are reconciled with a read-only
  page probe before the engine may continue;
* it captures a screenshot for evidence.

No generated JavaScript or model-authored code is ever evaluated.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flowstate.domain.enums import ErrorClass
from flowstate.domain.policy import ActionPolicy

DRIVER_NAME = "playwright-v1"


class BrowserError(RuntimeError):
    code = "BROWSER_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class BrowserUnavailable(BrowserError):
    code = "BROWSER_UNAVAILABLE"


class SelectorNotFound(BrowserError):
    code = "SELECTOR_NOT_FOUND"


class SelectorAmbiguous(BrowserError):
    code = "SELECTOR_AMBIGUOUS"


@dataclass
class BrowserOutcome:
    outcome: str  # PASSED | FAILED | UNKNOWN
    detail: str
    page: dict[str, Any] = field(default_factory=dict)
    locator: dict[str, Any] | None = None
    matches: int = 0
    duration_ms: int = 0
    error_class: ErrorClass | None = None
    error_code: str | None = None
    screenshot: bytes | None = None


class BrowserSession(Protocol):
    """The narrow port the engine depends on; tests provide a fake."""

    driver_name: str

    def start(self) -> None: ...

    def perform(
        self,
        *,
        target: dict[str, Any],
        action_input: dict[str, Any],
        inputs: dict[str, Any],
        timeout_ms: int,
    ) -> BrowserOutcome: ...

    def probe(
        self,
        *,
        target: dict[str, Any],
        action_input: dict[str, Any],
        inputs: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]: ...

    def apply_selector_drift(self) -> None: ...

    def validate(
        self, actions: Sequence[Any], url: str, defaults: dict[str, Any]
    ) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def resolve_value(action_input: dict[str, Any], inputs: dict[str, Any]) -> Any:
    """Resolve a literal or an ``inputs.<path>`` reference. Never evaluates code."""
    source = action_input.get("value_from")
    if source:
        path = str(source).removeprefix("inputs.").removeprefix("input.")
        current: Any = inputs
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise BrowserError(
                    f"The workflow needs an input named {path!r}.", code="INPUT_MISSING"
                )
            current = current[part]
        return current
    return action_input.get("value")


def _matches_wait(snapshot: dict[str, Any], wait_for: dict[str, Any] | None) -> bool:
    if not wait_for:
        return True
    state = snapshot.get("state") or {}
    if "page" in wait_for and snapshot.get("page") != wait_for["page"]:
        return False
    if "route" in wait_for and snapshot.get("route") != wait_for["route"]:
        return False
    if "heading" in wait_for and snapshot.get("heading") != wait_for["heading"]:
        return False
    if wait_for.get("validation_present") and not state.get("validation"):
        return False
    if wait_for.get("invoice_present") and not state.get("invoice_id"):
        return False
    expected_state = wait_for.get("state")
    if isinstance(expected_state, dict):
        for key, value in expected_state.items():
            if state.get(key) != value:
                return False
    return True


class PlaywrightBrowserSession:
    """A single Chromium context owned by one execution run."""

    driver_name = DRIVER_NAME

    def __init__(
        self,
        *,
        policy: ActionPolicy | None = None,
        headless: bool = True,
    ) -> None:
        self.policy = policy or ActionPolicy()
        self.headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BrowserUnavailable(
                "Playwright is not installed. Run `pip install playwright` and "
                "`playwright install chromium`."
            ) from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                timezone_id="UTC",
            )
            self._context.set_default_timeout(5000)
            self.page = self._context.new_page()
        except Exception as exc:
            self.close()
            raise BrowserUnavailable(
                "Chromium could not start. Run `playwright install chromium`.",
                code="BROWSER_UNAVAILABLE",
            ) from exc

    def close(self) -> None:
        for attr in ("_context", "_browser"):
            value = getattr(self, attr, None)
            if value is not None:
                with contextlib.suppress(Exception):
                    value.close()
                setattr(self, attr, None)
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                self._playwright.stop()
            self._playwright = None
        self.page = None

    # ------------------------------------------------------------------ actions

    def _ensure_page(self) -> None:
        if self.page is None:
            raise BrowserError("The browser session has no page.", code="BROWSER_NO_PAGE")

    def _assert_allowed(self, url: str) -> None:
        result = self.policy.resolve_origin(url)
        if result.decision != "ALLOW":
            raise BrowserError(result.detail, code=result.code)

    def _snapshot(self) -> dict[str, Any]:
        self._ensure_page()
        return self.page.evaluate(
            """() => {
                const app = document.querySelector('[data-page]');
                const summary = document.querySelector('#invoice-summary');
                const validation = document.querySelector('#validation-result');
                const page = app ? app.dataset.page : '';
                const state = {
                    page,
                    invoice_id: summary ? (summary.dataset.invoiceId || null) : null,
                    supplier_id: summary ? (summary.dataset.supplierId || null) : null,
                    status: summary ? (summary.dataset.status || null) : null,
                    amount_cents: summary && summary.dataset.amount
                        ? Number(summary.dataset.amount) : null,
                    po_amount_cents: summary && summary.dataset.poAmount
                        ? Number(summary.dataset.poAmount) : null,
                    validation: validation ? (validation.dataset.state || null) : null,
                };
                const heading = document.querySelector('h1');
                return {
                    url: window.location.href,
                    route: window.location.hash || '#/queue',
                    page,
                    heading: heading ? heading.textContent.trim() : '',
                    state,
                };
            }"""
        )

    def _resolve_candidate(self, candidate: dict[str, Any]) -> Any:
        self._ensure_page()
        strategy = str(candidate.get("strategy", ""))
        value = candidate.get("value")
        if strategy == "role":
            return self.page.get_by_role(str(candidate.get("role", "")), name=str(value))
        if strategy == "label":
            return self.page.get_by_label(str(value))
        if strategy == "testid":
            return self.page.get_by_test_id(str(value))
        if strategy == "placeholder":
            return self.page.get_by_placeholder(str(value))
        if strategy == "text":
            return self.page.get_by_text(str(value))
        if strategy == "css":
            return self.page.locator(str(value))
        raise SelectorAmbiguous(
            f"Unknown selector strategy {strategy!r}.", code="SELECTOR_STRATEGY_UNKNOWN"
        )

    def resolve(self, candidates: Sequence[dict[str, Any]]) -> tuple[Any, dict[str, Any], int]:
        """Return the first uniquely matching ranked locator.

        A candidate that matches more than one element is never selected: an
        ambiguous selector could drive the wrong control.
        """
        ambiguous: list[dict[str, Any]] = []
        matched_any = False
        for candidate in candidates or []:
            try:
                locator = self._resolve_candidate(candidate)
                count = int(locator.count())
            except BrowserError:
                raise
            except Exception:
                continue
            if count == 1:
                return locator, candidate, count
            if count > 1:
                matched_any = True
                ambiguous.append({**candidate, "matches": count})
        if matched_any:
            raise SelectorAmbiguous(
                "The recorded selector matches more than one element: "
                + ", ".join(
                    f"{item.get('strategy')}={item.get('value')!r}" for item in ambiguous[:3]
                ),
                code="SELECTOR_AMBIGUOUS",
            )
        raise SelectorNotFound(
            "None of the recorded selectors resolve to a unique element.",
            code="SELECTOR_NOT_FOUND",
        )

    def apply_selector_drift(self) -> None:
        """Reload the current demo page with the changed-layout variant.

        The subsequent locator resolution then fails naturally against real
        markup, instead of a synthetic error.
        """
        self._ensure_page()
        parts = urlsplit(self.page.url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["variant"] = "changed"
        drifted = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
        self.page.goto(drifted, wait_until="domcontentloaded")
        self.page.wait_for_timeout(50)

    def _wait_for(
        self, wait_for: dict[str, Any] | None, timeout_ms: int
    ) -> tuple[bool, dict[str, Any]]:
        deadline = time.monotonic() + max(timeout_ms, 100) / 1000
        snapshot = self._snapshot()
        while True:
            if _matches_wait(snapshot, wait_for):
                return True, snapshot
            if time.monotonic() >= deadline:
                return False, snapshot
            self.page.wait_for_timeout(40)
            snapshot = self._snapshot()

    def perform(
        self,
        *,
        target: dict[str, Any],
        action_input: dict[str, Any],
        inputs: dict[str, Any],
        timeout_ms: int,
    ) -> BrowserOutcome:
        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        action = str(action_input.get("action") or target.get("action") or "CLICK").upper()
        wait_for = (
            action_input.get("wait_for") if isinstance(action_input.get("wait_for"), dict) else None
        )
        screenshot = None
        locator_info: dict[str, Any] | None = None
        matches = 0

        try:
            if action == "NAVIGATE":
                url = str(target.get("url") or action_input.get("url") or "")
                self._assert_allowed(url)
                self._ensure_page()
                self.page.goto(url, wait_until="domcontentloaded", timeout=max(timeout_ms, 1000))
                ok, snapshot = self._wait_for(wait_for, timeout_ms)
                screenshot = self._screenshot()
                if not ok:
                    return BrowserOutcome(
                        "FAILED",
                        "The demo page did not reach the expected state after navigation.",
                        page=snapshot,
                        duration_ms=elapsed(),
                        error_class=ErrorClass.POSTCONDITION,
                        error_code="NAVIGATION_POSTCONDITION_FAILED",
                        screenshot=screenshot,
                    )
                return BrowserOutcome(
                    "PASSED",
                    f"Opened {url}",
                    page=snapshot,
                    duration_ms=elapsed(),
                    screenshot=screenshot,
                )

            locator, locator_info, matches = self.resolve(target.get("locator") or [])
            with contextlib.suppress(Exception):
                # Visibility can lag the DOM; the action below still fails fast.
                locator.wait_for(state="visible", timeout=max(timeout_ms // 2, 250))

            value = resolve_value(action_input, inputs)

            if action in {"TYPE", "FILL"}:
                if value is None:
                    return BrowserOutcome(
                        "FAILED",
                        "A TYPE action needs a value or value_from reference.",
                        locator=locator_info,
                        matches=matches,
                        duration_ms=elapsed(),
                        error_class=ErrorClass.VALIDATION,
                        error_code="ACTION_VALUE_MISSING",
                    )
                locator.fill(str(value), timeout=max(timeout_ms, 500))
                observed_value = locator.input_value(timeout=500)
                if str(observed_value) != str(value):
                    return BrowserOutcome(
                        "FAILED",
                        "The field did not retain the expected value "
                        f"(observed {observed_value!r}).",
                        page=self._snapshot(),
                        locator=locator_info,
                        matches=matches,
                        duration_ms=elapsed(),
                        error_class=ErrorClass.POSTCONDITION,
                        error_code="FIELD_VALUE_NOT_APPLIED",
                        screenshot=self._screenshot(),
                    )
            elif action == "SELECT":
                if value is None:
                    return BrowserOutcome(
                        "FAILED",
                        "A SELECT action needs a value or value_from reference.",
                        locator=locator_info,
                        matches=matches,
                        duration_ms=elapsed(),
                        error_class=ErrorClass.VALIDATION,
                        error_code="ACTION_VALUE_MISSING",
                    )
                locator.select_option(str(value), timeout=max(timeout_ms, 500))
                selected = locator.input_value(timeout=500)
                if str(selected) != str(value):
                    return BrowserOutcome(
                        "FAILED",
                        "The select element did not retain the expected option "
                        f"(observed {selected!r}).",
                        page=self._snapshot(),
                        locator=locator_info,
                        matches=matches,
                        duration_ms=elapsed(),
                        error_class=ErrorClass.POSTCONDITION,
                        error_code="FIELD_VALUE_NOT_APPLIED",
                        screenshot=self._screenshot(),
                    )
            elif action in {"CLICK", "SUBMIT"}:
                locator.click(timeout=max(timeout_ms, 500))
            elif action == "WAIT_FOR":
                pass
            else:
                return BrowserOutcome(
                    "FAILED",
                    f"Unsupported browser action {action!r}.",
                    locator=locator_info,
                    duration_ms=elapsed(),
                    error_class=ErrorClass.VALIDATION,
                    error_code="UNSUPPORTED_BROWSER_ACTION",
                )

            ok, snapshot = self._wait_for(wait_for, timeout_ms)
            screenshot = self._screenshot()
            if not ok:
                # The click/fill may have completed without the expected page
                # change. That is ambiguous, not a success and not a normal retry.
                return BrowserOutcome(
                    "UNKNOWN",
                    "The action completed but the expected page state was not observed in time.",
                    page=snapshot,
                    locator=locator_info,
                    matches=matches,
                    duration_ms=elapsed(),
                    error_class=ErrorClass.TIMEOUT,
                    error_code="ACTION_OUTCOME_UNKNOWN",
                    screenshot=screenshot,
                )
            return BrowserOutcome(
                "PASSED",
                f"{action.title()} completed and the expected page state was observed.",
                page=snapshot,
                locator=locator_info,
                matches=matches,
                duration_ms=elapsed(),
                screenshot=screenshot,
            )
        except (SelectorNotFound, SelectorAmbiguous) as exc:
            return BrowserOutcome(
                "FAILED",
                str(exc),
                page=self._safe_snapshot(),
                locator=locator_info,
                matches=matches,
                duration_ms=elapsed(),
                error_class=ErrorClass.PRECONDITION,
                error_code=exc.code,
                screenshot=self._safe_screenshot(),
            )
        except BrowserError as exc:
            return BrowserOutcome(
                "FAILED",
                str(exc),
                page=self._safe_snapshot(),
                locator=locator_info,
                matches=matches,
                duration_ms=elapsed(),
                error_class=ErrorClass.VALIDATION,
                error_code=exc.code,
                screenshot=self._safe_screenshot(),
            )
        except Exception as exc:
            message = str(exc)
            timeout = "Timeout" in type(exc).__name__ or "timeout" in message.lower()
            outcome = "UNKNOWN" if timeout else "FAILED"
            return BrowserOutcome(
                outcome,
                f"The browser action did not complete as expected: {message.splitlines()[0][:240]}",
                page=self._safe_snapshot(),
                locator=locator_info,
                matches=matches,
                duration_ms=elapsed(),
                error_class=ErrorClass.TIMEOUT if timeout else ErrorClass.HANDLER_ERROR,
                error_code="ACTION_OUTCOME_UNKNOWN" if timeout else "BROWSER_ACTION_FAILED",
                screenshot=self._safe_screenshot(),
            )

    def probe(
        self, *, target: dict[str, Any], action_input: dict[str, Any]
    ) -> tuple[bool, str, dict[str, Any]]:
        wait_for = (
            action_input.get("wait_for") if isinstance(action_input.get("wait_for"), dict) else None
        )
        try:
            ok, snapshot = self._wait_for(wait_for, 400)
        except Exception as exc:
            return False, f"the read-only page probe could not run: {exc}", {}
        if ok:
            return True, "read-only page probe observed the expected state", snapshot
        return (
            False,
            "read-only page probe did not observe the expected state "
            f"(route {snapshot.get('route')!r})",
            snapshot,
        )

    def validate(
        self, actions: Sequence[Any], url: str, defaults: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Resolve every recorded locator in sequence without asserting results."""
        report: list[dict[str, Any]] = []
        try:
            self._assert_allowed(url)
            if self.page is None or self._playwright is None:
                self.start()
            self.page.goto(url, wait_until="domcontentloaded", timeout=10_000)
            self._wait_for({"page": "queue"}, 3000)
        except Exception as exc:
            return [
                {
                    "ordinal": None,
                    "ok": False,
                    "code": "DEMO_UNREACHABLE",
                    "detail": f"The demo app could not be opened: {exc}",
                }
            ]

        for action in actions:
            node_type = str(getattr(action, "node_type", "ACTION"))
            if node_type not in {"ACTION", "NAVIGATE"}:
                continue
            target = dict(getattr(action, "target", None) or {})
            action_input = dict(getattr(action, "input", None) or {})
            entry: dict[str, Any] = {
                "ordinal": int(getattr(action, "ordinal", -1)),
                "node_key": str(getattr(action, "node_key", "")),
                "label": str(getattr(action, "label", "")),
                "ok": True,
                "matches": 0,
            }
            if str(action_input.get("action", "")).upper() == "NAVIGATE":
                entry.update({"strategy": "navigation", "value": target.get("url"), "matches": 1})
                report.append(entry)
                continue
            try:
                _locator, candidate, count = self.resolve(target.get("locator") or [])
                entry.update(
                    {
                        "strategy": candidate.get("strategy"),
                        "value": candidate.get("value"),
                        "matches": count,
                    }
                )
                value = resolve_value(action_input, defaults)
                action_name = str(action_input.get("action", "")).upper()
                if action_name in {"TYPE", "FILL"} and value is not None:
                    _locator.fill(str(value), timeout=2000)
                elif action_name == "SELECT" and value is not None:
                    _locator.select_option(str(value), timeout=2000)
                elif action_name in {"CLICK", "SUBMIT"}:
                    _locator.click(timeout=2000)
                    wait_for = action_input.get("wait_for")
                    self._wait_for(wait_for if isinstance(wait_for, dict) else None, 2000)
            except SelectorNotFound as exc:
                entry.update({"ok": False, "code": exc.code, "detail": str(exc)})
            except SelectorAmbiguous as exc:
                entry.update({"ok": False, "code": exc.code, "detail": str(exc)})
            except Exception as exc:
                entry.update(
                    {
                        "ok": False,
                        "code": "SELECTOR_PROBE_FAILED",
                        "detail": f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}",
                    }
                )
            report.append(entry)
            if not entry["ok"]:
                break
        return report

    # ----------------------------------------------------------------- helpers

    def _screenshot(self) -> bytes | None:
        try:
            return self.page.screenshot(full_page=False)
        except Exception:
            return None

    def _safe_screenshot(self) -> bytes | None:
        return self._screenshot()

    def _safe_snapshot(self) -> dict[str, Any]:
        try:
            return self._snapshot()
        except Exception:
            return {}
