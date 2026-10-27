"""Deterministic test double for the Playwright browser session.

The fake models only the controlled demo app. It exists so API tests can cover
selector validation, real engine replay, input substitution, postcondition
failure and selector drift without launching Chromium. A separate browser-marked
test exercises the real driver.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from flowstate.capture.browser import BrowserOutcome, SelectorNotFound
from flowstate.domain.enums import ErrorClass

INVOICES = {
    "INV-1001": {
        "invoice_id": "INV-1001",
        "supplier_id": "SUP-77",
        "amount_cents": 125000,
        "po_amount_cents": 125000,
    },
    "INV-1002": {
        "invoice_id": "INV-1002",
        "supplier_id": "SUP-88",
        "amount_cents": 99900,
        "po_amount_cents": 80000,
    },
    "INV-1003": {
        "invoice_id": "INV-1003",
        "supplier_id": "SUP-77",
        "amount_cents": 450000,
        "po_amount_cents": 450000,
    },
}

_QUEUE_NAMES = {"Supplier ID": "supplier", "Invoice ID": "invoice", "Load invoice": "load"}
_INVOICE_NAMES = {"Validate invoice": "validate", "Back to queue": "back"}


def queue_snapshot() -> dict[str, Any]:
    return {
        "url": "http://127.0.0.1:8100/demo-app/",
        "route": "#/queue",
        "page": "queue",
        "heading": "Find an invoice to review",
        "state": {
            "page": "queue",
            "invoice_id": None,
            "supplier_id": None,
            "status": None,
            "amount_cents": None,
            "po_amount_cents": None,
            "validation": None,
        },
    }


def invoice_snapshot(
    invoice_id: str, *, validation: str | None = None, supplier: str | None = None
) -> dict[str, Any]:
    invoice = INVOICES[invoice_id]
    return {
        "url": f"http://127.0.0.1:8100/demo-app/#/invoice/{invoice_id}",
        "route": f"#/invoice/{invoice_id}",
        "page": "invoice",
        "heading": f"Invoice {invoice_id}",
        "state": {
            "page": "invoice",
            "invoice_id": invoice["invoice_id"],
            "supplier_id": supplier or invoice["supplier_id"],
            "status": "SUBMITTED",
            "amount_cents": invoice["amount_cents"],
            "po_amount_cents": invoice["po_amount_cents"],
            "validation": validation,
        },
    }


class FakeBrowserSession:
    driver_name = "fake-browser-v1"

    def __init__(
        self,
        *,
        fail_selectors: bool = False,
        break_validation: bool = False,
        break_typing: bool = False,
        raise_on_start: bool = False,
    ) -> None:
        self.fail_selectors = fail_selectors
        self.break_validation = break_validation
        self.break_typing = break_typing
        self.raise_on_start = raise_on_start
        self.changed = False
        self.page = "queue"
        self.route = "#/queue"
        self.invoice_id: str | None = None
        self.validation: str | None = None
        self.values: dict[str, Any] = {}
        self.closed = False

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self.raise_on_start:
            raise RuntimeError("fake browser failed to start")

    def close(self) -> None:
        self.closed = True

    def apply_selector_drift(self) -> None:
        self.changed = True

    # ----------------------------------------------------------------- helpers

    def snapshot(self) -> dict[str, Any]:
        if self.page == "invoice" and self.invoice_id:
            return invoice_snapshot(
                self.invoice_id,
                validation=self.validation,
                supplier=self.values.get("supplier_id"),
            )
        return queue_snapshot()

    def _field(self, locator: list[dict[str, Any]]) -> str:
        if self.changed or self.fail_selectors:
            raise SelectorNotFound("The demo structure changed; the selector did not resolve.")
        for candidate in locator or []:
            value = str(candidate.get("value") or candidate.get("name") or "")
            mapping = _QUEUE_NAMES if self.page == "queue" else _INVOICE_NAMES
            if value in mapping:
                return mapping[value]
        raise SelectorNotFound(f"No locator matched the {self.page} page.")

    def _wait(self, wait_for: dict[str, Any] | None) -> bool:
        if not wait_for:
            return True
        snap = self.snapshot()
        state = snap["state"]
        if "page" in wait_for and snap["page"] != wait_for["page"]:
            return False
        if "route" in wait_for and snap["route"] != wait_for["route"]:
            return False
        if wait_for.get("validation_present") and not state.get("validation"):
            return False
        if wait_for.get("invoice_present") and not state.get("invoice_id"):
            return False
        return all(state.get(key) == value for key, value in (wait_for.get("state") or {}).items())

    # ----------------------------------------------------------------- actions

    def perform(
        self,
        *,
        target: dict[str, Any],
        action_input: dict[str, Any],
        inputs: dict[str, Any],
        timeout_ms: int,
    ) -> BrowserOutcome:
        action = str(action_input.get("action") or "CLICK").upper()
        wait_for = (
            action_input.get("wait_for") if isinstance(action_input.get("wait_for"), dict) else None
        )

        if action == "NAVIGATE":
            self.changed = "variant=changed" in str(target.get("url") or "")
            self.page = "queue"
            self.route = "#/queue"
            self.invoice_id = None
            self.validation = None
            return BrowserOutcome(
                "PASSED",
                "fake navigation",
                page=self.snapshot(),
                duration_ms=1,
                screenshot=b"fake-navigate-png",
            )

        try:
            field = self._field(target.get("locator") or [])
        except SelectorNotFound as exc:
            return BrowserOutcome(
                "FAILED",
                str(exc),
                page=self.snapshot(),
                duration_ms=1,
                error_class=ErrorClass.PRECONDITION,
                error_code=exc.code,
                screenshot=b"fake-failed-png",
            )

        if action in {"TYPE", "FILL"}:
            value = action_input.get("value")
            if value is None and action_input.get("value_from"):
                key = str(action_input["value_from"]).split(".")[-1]
                value = inputs.get(key)
            if value is None:
                return BrowserOutcome(
                    "FAILED",
                    "missing value",
                    page=self.snapshot(),
                    duration_ms=1,
                    error_class=ErrorClass.VALIDATION,
                    error_code="ACTION_VALUE_MISSING",
                )
            if self.break_typing:
                return BrowserOutcome(
                    "UNKNOWN",
                    "the field did not accept the value in time",
                    page=self.snapshot(),
                    duration_ms=1,
                    error_class=ErrorClass.TIMEOUT,
                    error_code="ACTION_OUTCOME_UNKNOWN",
                    screenshot=b"fake-type-unknown-png",
                )
            self.values[field] = value
            return BrowserOutcome(
                "PASSED",
                "fake field filled",
                page=self.snapshot(),
                duration_ms=1,
                screenshot=b"fake-typed-png",
            )

        if field == "load":
            invoice_id = str(
                self.values.get("invoice_id") or inputs.get("invoice_id") or ""
            ).upper()
            if invoice_id not in INVOICES:
                self.page = "missing"
                self.route = f"#/missing/{invoice_id}"
                return BrowserOutcome(
                    "UNKNOWN",
                    "The demo reported not found.",
                    page=self.snapshot(),
                    duration_ms=1,
                    error_class=ErrorClass.TIMEOUT,
                    error_code="ACTION_OUTCOME_UNKNOWN",
                )
            self.page = "invoice"
            self.invoice_id = invoice_id
            self.route = f"#/invoice/{invoice_id}"
            ok = self._wait(wait_for)
            return BrowserOutcome(
                "PASSED" if ok else "UNKNOWN",
                "fake invoice loaded" if ok else "page state not observed",
                page=self.snapshot(),
                duration_ms=1,
                error_class=None if ok else ErrorClass.TIMEOUT,
                error_code=None if ok else "ACTION_OUTCOME_UNKNOWN",
                screenshot=b"fake-loaded-png",
            )

        if field == "validate":
            if self.break_validation or self.invoice_id is None:
                return BrowserOutcome(
                    "UNKNOWN",
                    "the validation result never appeared",
                    page=self.snapshot(),
                    duration_ms=1,
                    error_class=ErrorClass.TIMEOUT,
                    error_code="ACTION_OUTCOME_UNKNOWN",
                    screenshot=b"fake-unknown-png",
                )
            invoice = INVOICES[self.invoice_id]
            supplier = str(self.values.get("supplier_id") or inputs.get("supplier_id") or "")
            if (supplier and supplier.upper() != invoice["supplier_id"]) or invoice[
                "amount_cents"
            ] != invoice["po_amount_cents"]:
                self.validation = "DISCREPANCY"
            else:
                self.validation = "VALID"
            ok = self._wait(wait_for)
            return BrowserOutcome(
                "PASSED" if ok else "UNKNOWN",
                "fake validation rendered" if ok else "validation state not observed",
                page=self.snapshot(),
                duration_ms=1,
                error_class=None if ok else ErrorClass.TIMEOUT,
                error_code=None if ok else "ACTION_OUTCOME_UNKNOWN",
                screenshot=b"fake-validated-png",
            )

        if field == "back":
            self.page = "queue"
            self.route = "#/queue"
            return BrowserOutcome("PASSED", "back", page=self.snapshot(), duration_ms=1)

        return BrowserOutcome(
            "FAILED",
            f"unsupported fake action for {field}",
            page=self.snapshot(),
            duration_ms=1,
            error_class=ErrorClass.VALIDATION,
            error_code="UNSUPPORTED_BROWSER_ACTION",
        )

    def probe(
        self,
        *,
        target: dict[str, Any],
        action_input: dict[str, Any],
        inputs: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        wait_for = (
            action_input.get("wait_for") if isinstance(action_input.get("wait_for"), dict) else None
        )
        if self.break_validation:
            return False, "fake probe did not observe a validation result", self.snapshot()
        action = str(action_input.get("action") or "CLICK").upper()
        if action in {"TYPE", "FILL", "SELECT"}:
            if self.break_typing:
                return False, "fake probe did not observe the field value", self.snapshot()
            expected = action_input.get("value")
            if expected is None and action_input.get("value_from"):
                key = str(action_input["value_from"]).split(".")[-1]
                expected = inputs.get(key)
            field = None
            for candidate in target.get("locator") or []:
                value = str(candidate.get("value") or candidate.get("name") or "")
                mapping = _QUEUE_NAMES if self.page == "queue" else _INVOICE_NAMES
                if value in mapping:
                    field = mapping[value]
                    break
            if field and str(self.values.get(field)) == str(expected):
                return True, "fake probe observed the field value", self.snapshot()
            return False, "fake probe did not observe the field value", self.snapshot()
        if not wait_for:
            return False, "no observable expectation was declared", self.snapshot()
        ok = self._wait(wait_for)
        return ok, "fake probe", self.snapshot()

    def validate(
        self, actions: Sequence[Any], url: str, defaults: dict[str, Any]
    ) -> list[dict[str, Any]]:
        report: list[dict[str, Any]] = []
        self.values = dict(defaults)
        for action in actions:
            node_type = str(getattr(action, "node_type", "ACTION"))
            if node_type not in {"ACTION", "NAVIGATE"}:
                continue
            action_input = dict(getattr(action, "input", None) or {})
            target = dict(getattr(action, "target", None) or {})
            entry = {
                "ordinal": int(getattr(action, "ordinal", -1)),
                "node_key": str(getattr(action, "node_key", "")),
                "label": str(getattr(action, "label", "")),
                "ok": True,
                "matches": 1,
                "strategy": "role",
                "value": "",
            }
            if str(action_input.get("action", "")).upper() == "NAVIGATE":
                if "variant=changed" in str(target.get("url") or ""):
                    self.changed = True
                entry["strategy"] = "navigation"
                entry["value"] = target.get("url")
            else:
                try:
                    field = self._field(target.get("locator") or [])
                    entry["value"] = field
                    for candidate in target.get("locator") or []:
                        if candidate.get("strategy") == "role":
                            entry["strategy"] = "role"
                            entry["value"] = candidate.get("value")
                            break
                    if field == "load":
                        self.page = "invoice"
                        self.invoice_id = str(defaults.get("invoice_id") or "INV-1001")
                        self.route = f"#/invoice/{self.invoice_id}"
                    elif field == "validate":
                        self.validation = "VALID"
                except SelectorNotFound as exc:
                    entry.update({"ok": False, "code": exc.code, "detail": str(exc)})
            report.append(entry)
            if not entry["ok"]:
                break
        return report


def raw_event(
    sequence: int,
    kind: str,
    *,
    role: str,
    name: str,
    test_id: str = "",
    label: str = "",
    css: str = "",
    input_name: str = "",
    param: str = "",
    value: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    sensitive: bool = False,
    observed: str = "",
    submit_shaped: bool = False,
) -> dict[str, Any]:
    return {
        "client_sequence": sequence,
        "kind": kind,
        "locator": {
            "tag": "input" if kind == "TYPE" else "button",
            "test_id": test_id,
            "role": role,
            "name": name,
            "label": label or name,
            "placeholder": "",
            "text": name,
            "css": css,
            "input_name": input_name,
            "param": param,
        },
        "accessible_name": name,
        "value": value,
        "sensitive": sensitive,
        "redacted_fields": ["value"] if sensitive else [],
        "param": param,
        "page_before": copy.deepcopy(before or queue_snapshot()),
        "page_after": copy.deepcopy(after or queue_snapshot()),
        "observed_result": observed,
        "timestamp_offset_ms": sequence * 100,
        "submit_shaped": submit_shaped,
    }


def invoice_lookup_events(
    *,
    supplier: str = "SUP-77",
    invoice: str = "INV-1001",
) -> list[dict[str, Any]]:
    loaded = invoice_snapshot(invoice)
    validated = invoice_snapshot(
        invoice,
        validation=(
            "VALID"
            if INVOICES[invoice]["amount_cents"] == INVOICES[invoice]["po_amount_cents"]
            and supplier == INVOICES[invoice]["supplier_id"]
            else "DISCREPANCY"
        ),
    )
    return [
        raw_event(
            0,
            "TYPE",
            role="textbox",
            name="Supplier ID",
            label="Supplier ID",
            css="#supplier-id",
            input_name="supplier_id",
            param="supplier_id",
            value=supplier,
        ),
        raw_event(
            1,
            "TYPE",
            role="textbox",
            name="Invoice ID",
            label="Invoice ID",
            css="#invoice-id",
            input_name="invoice_id",
            param="invoice_id",
            value=invoice,
        ),
        raw_event(
            2,
            "CLICK",
            role="button",
            name="Load invoice",
            test_id="load-invoice",
            css="#load-invoice",
            before=queue_snapshot(),
            after=loaded,
            observed=f"Invoice {invoice} is open",
            submit_shaped=True,
        ),
        raw_event(
            3,
            "CLICK",
            role="button",
            name="Validate invoice",
            test_id="validate-invoice",
            css="#validate-invoice",
            before=loaded,
            after=validated,
            observed="Validation rendered",
        ),
    ]
