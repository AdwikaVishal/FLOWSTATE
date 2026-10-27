"""Demonstration fixtures.

These are *fixtures*, not execution history produced by this application. Every
record created from this module is stamped ``origin=DEMO`` plus a seed batch id,
and the API reports that origin so the console can label it.

Nothing here is imported by the execution path. Removing this package must leave
the product fully functional for live runs.
"""

from __future__ import annotations

from typing import Any

DEMO_BATCH = "demo-invoice-approval-v1"
DEMO_SLUG = "invoice-approval"

WORKFLOW: dict[str, Any] = {
    "name": "Invoice Approval",
    "description": (
        "Review a submitted vendor invoice, approve it for payment, schedule the "
        "disbursement and confirm the ledger entry. Runs against the sandboxed "
        "invoice workspace."
    ),
    "owner": "demo-workspace",
    "actions": [
        {
            "ordinal": 0,
            "kind": "NAVIGATE",
            "label": "Open the invoice queue",
            "target": {"surface": "invoice-queue", "route": "/queue"},
            "input": {"effects": [{"op": "set", "path": "queue.loaded", "value": True}]},
            "postconditions": [{"path": "queue.loaded", "op": "eq", "value": True}],
            "side_effect_class": "READ_ONLY",
        },
        {
            "ordinal": 1,
            "kind": "ASSERT",
            "label": "Confirm the invoice is awaiting review",
            "target": {"surface": "invoice-row", "invoice_id": "INV-2041"},
            "input": {"effects": []},
            "preconditions": [{"path": "queue.loaded", "op": "eq", "value": True}],
            "postconditions": [{"path": "invoice.status", "op": "eq", "value": "SUBMITTED"}],
            "side_effect_class": "READ_ONLY",
        },
        {
            "ordinal": 2,
            "kind": "EXTRACT",
            "label": "Read vendor and amount",
            "target": {"surface": "invoice-header"},
            "input": {
                "effects": [
                    {
                        "op": "copy",
                        "from": "invoice.amount_cents",
                        "path": "extracted.amount_cents",
                    },
                    {"op": "copy", "from": "invoice.vendor", "path": "extracted.vendor"},
                ]
            },
            "postconditions": [
                {"path": "extracted.amount_cents", "op": "eq", "value": 184500},
                {"path": "extracted.vendor", "op": "eq", "value": "Northwind Supply"},
            ],
            "side_effect_class": "READ_ONLY",
        },
        {
            "ordinal": 3,
            "kind": "CLICK",
            "label": "Open the approval panel",
            "target": {"surface": "approval-panel", "role": "button", "name": "Review"},
            "input": {"effects": [{"op": "set", "path": "approvals.panel_open", "value": True}]},
            "postconditions": [{"path": "approvals.panel_open", "op": "eq", "value": True}],
            "side_effect_class": "IDEMPOTENT",
        },
        {
            "ordinal": 4,
            "kind": "TYPE",
            "label": "Record the approval note",
            "target": {"surface": "approval-note", "role": "textbox"},
            "input": {
                "effects": [
                    {
                        "op": "set",
                        "path": "approvals.note",
                        "value": "Amount matches PO-8841. Approved for payment.",
                    }
                ]
            },
            "postconditions": [{"path": "approvals.note", "op": "exists"}],
            "side_effect_class": "IDEMPOTENT",
        },
        {
            "ordinal": 5,
            "kind": "AUTHORIZE",
            "label": "Approve the invoice for payment",
            "target": {"surface": "approve-button", "role": "button", "name": "Approve invoice"},
            "input": {
                "effects": [
                    {"op": "set", "path": "invoice.status", "value": "APPROVED"},
                    {"op": "set", "path": "invoice.approver", "value": "local-operator"},
                ],
                "decision": {
                    "question": ("Approve invoice INV-2041 for 1,845.00 USD for payment?"),
                    "options": [
                        {
                            "value": "approve",
                            "label": "Approve for payment",
                            "consequence": "Schedules a simulated disbursement of 1,845.00 USD",
                        },
                        {
                            "value": "deny",
                            "label": "Reject",
                            "consequence": "Stops the run; no payment is scheduled",
                        },
                    ],
                    "recommended": "approve",
                    "reason": (
                        "Approving an invoice changes financial state and cannot be undone "
                        "by the engine."
                    ),
                    "policy_citation": (
                        "ActionPolicy 4.2: IRREVERSIBLE actions require explicit human "
                        "authorization."
                    ),
                },
            },
            "postconditions": [{"path": "invoice.status", "op": "eq", "value": "APPROVED"}],
            "side_effect_class": "IRREVERSIBLE",
            "requires_authorization": True,
        },
        {
            "ordinal": 6,
            "kind": "SUBMIT",
            "label": "Schedule the payment",
            "target": {"surface": "schedule-payment"},
            "input": {
                "effects": [
                    {"op": "set", "path": "invoice.status", "value": "SCHEDULED"},
                    {
                        "op": "set",
                        "path": "invoice.payment_reference",
                        "value": "pay_demo_7f3a",
                    },
                ]
            },
            "postconditions": [
                {"path": "invoice.status", "op": "eq", "value": "SCHEDULED"},
                {"path": "invoice.payment_reference", "op": "eq", "value": "pay_demo_7f3a"},
            ],
            "side_effect_class": "COMPENSABLE",
        },
        {
            "ordinal": 7,
            "kind": "PAYMENT_SIMULATED",
            "label": "Disburse funds (sandbox)",
            "target": {"surface": "sandbox-gateway", "origin": "http://127.0.0.1:8100"},
            "input": {
                "effects": [
                    {
                        "op": "append",
                        "path": "invoice.ledger",
                        "value": {
                            "entry": "debit",
                            "amount_cents": 184500,
                            "reference": "pay_demo_7f3a",
                        },
                    },
                    {"op": "increment", "path": "invoice.ledger_count", "value": 1},
                    {"op": "set", "path": "invoice.status", "value": "PAID"},
                ],
                "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                "recovery": {
                    "strategy": "RESUME",
                    "reversible": True,
                    "allowed_strategies": ["RESUME", "COMPENSATE"],
                    "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                    "idempotency_path": "invoice.payment_reference",
                    "contract": [
                        {
                            "path": "invoice.status",
                            "op": "in",
                            "value": ["SCHEDULED", "PAID"],
                        },
                        {"path": "invoice.ledger_count", "op": "lte", "value": 1},
                    ],
                    "compensate_contract": [
                        {"path": "invoice.status", "op": "eq", "value": "SCHEDULED"},
                        {"path": "invoice.ledger_count", "op": "eq", "value": 0},
                    ],
                    "compensate": [
                        {"op": "remove_last", "path": "invoice.ledger"},
                        {"op": "decrement", "path": "invoice.ledger_count", "value": 1},
                        {"op": "set", "path": "invoice.status", "value": "SCHEDULED"},
                        {"op": "set", "path": "invoice.payment_reference", "value": None},
                    ],
                    "max_attempts": 1,
                    "notes": (
                        "The sandbox gateway supports idempotency on payment_reference and "
                        "an explicit compensating reversal."
                    ),
                },
            },
            "postconditions": [
                {"path": "invoice.ledger_count", "op": "gte", "value": 1},
            ],
            "side_effect_class": "IRREVERSIBLE",
        },
    ],
    "invariants": [
        {
            "name": "Invoice reaches PAID",
            "description": "A run only succeeds if the invoice ends in the PAID state.",
            "kind": "TERMINAL_STATE",
            "severity": "CRITICAL",
            "expression": {"kind": "state", "path": "invoice.status", "op": "eq", "value": "PAID"},
        },
        {
            "name": "Exactly one ledger entry",
            "description": "Disbursement must never write more than one ledger entry.",
            "kind": "CROSS_ACTION",
            "severity": "CRITICAL",
            "expression": {
                "kind": "state",
                "path": "invoice.ledger_count",
                "op": "eq",
                "value": 1,
            },
        },
        {
            "name": "Single successful disbursement",
            "description": "The disbursement action may report success at most once.",
            "kind": "HISTORY",
            "severity": "MAJOR",
            "expression": {
                "kind": "history",
                "event_type": "ACTION_SUCCEEDED",
                "action_ordinal": 7,
                "op": "count_lte",
                "value": 1,
            },
        },
        {
            "name": "Financial status requires matching totals",
            "description": (
                "An invoice may only reach a financial state when its amount matches "
                "the purchase order under the configured policy."
            ),
            "kind": "POLICY",
            "severity": "CRITICAL",
            "expression": {
                "kind": "implies",
                "if": {
                    "path": "invoice.status",
                    "op": "in",
                    "value": ["APPROVED", "SCHEDULED", "PAID"],
                },
                "then": {
                    "kind": "compare",
                    "left": "invoice.amount_cents",
                    "op": "eq",
                    "right_path": "purchase_order.amount_cents",
                },
            },
        },
        {
            "name": "Financial success requires authorization",
            "description": (
                "An authorization-gated action may only succeed after a human grant "
                "or an explicit pre-authorization event."
            ),
            "kind": "AUTHORIZATION",
            "severity": "CRITICAL",
            "expression": {"kind": "authorization"},
        },
        {
            "name": "Completed disbursement satisfies its postcondition",
            "description": (
                "A successful disbursement must have written at least one ledger entry "
                "and must not have a recorded postcondition failure."
            ),
            "kind": "POSTCONDITION",
            "severity": "MAJOR",
            "expression": {
                "kind": "postcondition",
                "action_ordinal": 7,
                "conditions": [{"path": "invoice.ledger_count", "op": "gte", "value": 1}],
            },
        },
        {
            "name": "Recovery verifies resume preconditions",
            "description": (
                "A recovery may not apply a checkpoint when the resumed action's "
                "preconditions are false."
            ),
            "kind": "CHECKPOINT",
            "severity": "CRITICAL",
            "expression": {"kind": "checkpoint"},
        },
    ],
}

SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "Payment gateway timeout",
        "description": (
            "The disbursement request times out after the server accepted it. The "
            "engine must classify the outcome as UNKNOWN and confirm it with a probe."
        ),
        "target_ordinal": 7,
        "fault_type": "TIMEOUT",
        "parameters": {"timeout_ms": 5000},
        "seed": 41,
    },
    {
        "name": "Partial ledger commit",
        "description": (
            "The server writes the ledger entry and then dies before updating the "
            "invoice status. Postconditions can pass while invariants fail."
        ),
        "target_ordinal": 7,
        "fault_type": "PARTIAL_COMMIT",
        "parameters": {"commit_fraction": 0.5},
        "seed": 42,
    },
    {
        "name": "Duplicated gateway response",
        "description": (
            "The gateway delivers a duplicate response, so the effect is applied twice. "
            "The ledger invariant must catch it."
        ),
        "target_ordinal": 7,
        "fault_type": "DUPLICATE_RESPONSE",
        "parameters": {"duplicates": 2},
        "seed": 43,
    },
    {
        "name": "Connection drop before send",
        "description": (
            "The connection drops before the request reaches the gateway. No effect is "
            "applied and the run must become recoverable, not successful."
        ),
        "target_ordinal": 7,
        "fault_type": "NETWORK_DROP",
        "parameters": {"drop_after_ms": 12},
        "seed": 44,
    },
    {
        "name": "Timeout before the operation executes",
        "description": (
            "The gateway times out before invoking the disbursement. No effect may be "
            "applied; the engine must not treat this as a lost response."
        ),
        "target_ordinal": 7,
        "fault_type": "TIMEOUT_BEFORE_EXECUTION",
        "parameters": {"failures": 1},
        "seed": 45,
    },
    {
        "name": "Response lost after commit",
        "description": (
            "The gateway applies the disbursement and the response is lost. A read-only "
            "probe must confirm the effect before the run can continue."
        ),
        "target_ordinal": 7,
        "fault_type": "RESPONSE_LOST",
        "parameters": {},
        "seed": 46,
    },
    {
        "name": "Malformed gateway response",
        "description": (
            "The operation commits but returns a malformed response. The outcome is "
            "UNKNOWN and the probe decides whether it really happened."
        ),
        "target_ordinal": 7,
        "fault_type": "MALFORMED_RESPONSE",
        "parameters": {},
        "seed": 47,
    },
    {
        "name": "Missing payment reference",
        "description": (
            "The response omits the payment reference required by the action. The "
            "engine must fail the action instead of accepting a partial response."
        ),
        "target_ordinal": 6,
        "fault_type": "MISSING_FIELD",
        "parameters": {"field": "invoice.payment_reference"},
        "seed": 48,
    },
    {
        "name": "Contradictory invoice and PO totals",
        "description": (
            "The operation succeeds but the invoice total no longer matches its "
            "purchase order. The totals policy invariant must catch it."
        ),
        "target_ordinal": 7,
        "fault_type": "CONTRADICTORY_TOTALS",
        "parameters": {"po_amount_cents": 180000},
        "seed": 49,
    },
    {
        "name": "Payment gateway rate limit",
        "description": (
            "The gateway rejects the request with a rate limit before the effect. An "
            "irreversible action must not be automatically retried."
        ),
        "target_ordinal": 7,
        "fault_type": "RATE_LIMITED",
        "parameters": {"failures": 1},
        "seed": 50,
    },
    {
        "name": "Delayed human approval",
        "description": (
            "The human approval arrives after the decision TTL. Approval must be "
            "rejected and the run must not resume."
        ),
        "target_ordinal": 5,
        "fault_type": "DELAYED_APPROVAL",
        "parameters": {"delay_seconds": 900},
        "seed": 51,
    },
    {
        "name": "Postcondition fails after commit",
        "description": (
            "The gateway acknowledges success but leaves the invoice in a state that "
            "violates the declared postcondition. The engine must fail the action."
        ),
        "target_ordinal": 6,
        "fault_type": "POSTCONDITION_FAILURE",
        "parameters": {"path": "invoice.status", "value": "SUBMITTED"},
        "seed": 52,
    },
]

# (name, scenario index or None, authorize ordinal 5?, still awaiting decision?, minutes ago)
HISTORICAL_RUNS: list[dict[str, Any]] = [
    {
        "name": "Approved and paid",
        "scenario": None,
        "authorized": True,
        "awaiting": False,
        "minutes_ago": 60 * 26,
    },
    {
        "name": "Gateway timeout confirmed by probe",
        "scenario": 0,
        "authorized": True,
        "awaiting": False,
        "minutes_ago": 60 * 7,
    },
    {
        "name": "Partial ledger commit",
        "scenario": 1,
        "authorized": True,
        "awaiting": False,
        "minutes_ago": 60 * 4,
    },
    {
        "name": "Duplicated gateway response",
        "scenario": 2,
        "authorized": True,
        "awaiting": False,
        "minutes_ago": 90,
    },
    {
        "name": "Waiting on approval",
        "scenario": None,
        "authorized": False,
        "awaiting": True,
        "minutes_ago": 6,
    },
]


# ---------------------------------------------------------------------------
# Invoice discrepancy workspace
#
# This is the primary demonstration for the decision engine: the same workflow
# blocks for three different reasons depending on persisted state, and each one
# produces a different question and evidence packet.
# ---------------------------------------------------------------------------

DISCREPANCY_BATCH = "demo-invoice-discrepancy-v1"
DISCREPANCY_SLUG = "invoice-discrepancy-review"

DISCREPANCY_WORKFLOW: dict[str, Any] = {
    "name": "Invoice Discrepancy Review",
    "description": (
        "Match an invoice against its purchase order and approval policy. When the "
        "records agree the run proceeds; when they do not, the engine blocks the "
        "approval node, classifies the cause and asks a human for the smallest "
        "decision that unblocks it."
    ),
    "owner": "demo-workspace",
    "actions": [
        {
            "ordinal": 0,
            "node_key": "load_records",
            "kind": "NAVIGATE",
            "label": "Load the invoice and purchase order",
            "target": {"surface": "invoice-review"},
            "input": {
                "effects": [
                    {"op": "set", "path": "review.loaded", "value": True},
                ]
            },
            "postconditions": [{"path": "review.loaded", "op": "eq", "value": True}],
            "side_effect_class": "READ_ONLY",
        },
        {
            "ordinal": 1,
            "node_key": "match_totals",
            "kind": "EXTRACT",
            "label": "Compare the invoice total with the purchase order",
            "depends_on": ["load_records"],
            "target": {"surface": "invoice-review"},
            "input": {
                "effects": [
                    {
                        "op": "copy",
                        "from": "invoice.amount_cents",
                        "path": "review.invoice_amount_cents",
                    },
                    {
                        "op": "copy",
                        "from": "purchase_order.amount_cents",
                        "path": "review.po_amount_cents",
                    },
                    {
                        "op": "subtract",
                        "left": "invoice.amount_cents",
                        "right": "purchase_order.amount_cents",
                        "path": "review.delta_cents",
                    },
                ]
            },
            "postconditions": [{"path": "review.delta_cents", "op": "exists"}],
            "side_effect_class": "READ_ONLY",
        },
        {
            "ordinal": 2,
            "node_key": "approve_invoice",
            "kind": "AUTHORIZE",
            "label": "Approve the invoice for payment",
            "depends_on": ["match_totals"],
            "target": {"surface": "approval", "role": "button", "name": "Approve invoice"},
            "input": {
                "effects": [
                    {"op": "set", "path": "invoice.status", "value": "APPROVED_WITH_EXCEPTION"},
                    {"op": "set", "path": "invoice.approver", "value": "local-operator"},
                    {"op": "set", "path": "review.resolution", "value": "approved"},
                ],
                "blockers": [
                    {
                        "kind": "CONFLICTING_EVIDENCE",
                        "code": "INVOICE_PO_VARIANCE",
                        "when": {"path": "review.delta_cents", "op": "ne", "value": 0},
                        "title": "Invoice total does not match the purchase order",
                        "detail": (
                            "The invoice and the purchase order disagree. Approving would "
                            "pay an amount the purchase order does not authorise."
                        ),
                        "required_role": "FINANCE_APPROVER",
                        "reason": (
                            "Two system-of-record documents disagree; only a finance "
                            "approver can accept the variance."
                        ),
                        "policy_citation": (
                            "ActionPolicy 4.2: a variance between the invoice and its "
                            "purchase order requires finance approval."
                        ),
                        "question": (
                            "Approve invoice INV-2041 for 1,845.00 USD despite a "
                            "45.00 USD variance against PO-8841?"
                        ),
                        "options": [
                            {
                                "value": "approve",
                                "label": "Approve the variance",
                                "consequence": "Pays 1,845.00 USD and records an exception",
                            },
                            {
                                "value": "reject",
                                "label": "Reject the invoice",
                                "consequence": "Stops the run; nothing is paid",
                            },
                        ],
                        "recommended": "reject",
                        "evidence": [
                            {
                                "source": "invoice.amount_cents",
                                "label": "Invoice total (cents)",
                                "relation": "observed amount",
                                "record_ref": "INV-2041",
                                "validate": {
                                    "path": "invoice.amount_cents",
                                    "op": "eq",
                                    "value": 180000,
                                },
                            },
                            {
                                "source": "purchase_order.amount_cents",
                                "label": "Purchase order total (cents)",
                                "relation": "authorised amount",
                                "record_ref": "PO-8841",
                                "validate": {
                                    "path": "purchase_order.amount_cents",
                                    "op": "eq",
                                    "value": 180000,
                                },
                            },
                            {
                                "source": "review.delta_cents",
                                "label": "Variance (cents)",
                                "relation": "invoice minus purchase order",
                            },
                            {
                                "source_kind": "AUDIT_EVENT",
                                "event_type": "ACTION_SUCCEEDED",
                                "node_key": "match_totals",
                                "label": "Comparison step",
                                "relation": "produced the variance for this node",
                            },
                        ],
                        "resume_conditions": [
                            {
                                "code": "invoice_still_open",
                                "path": "invoice.status",
                                "op": "eq",
                                "value": "SUBMITTED",
                            },
                            {
                                "code": "variance_recorded",
                                "path": "review.delta_cents",
                                "op": "exists",
                            },
                        ],
                    },
                    {
                        "kind": "MISSING_INPUT",
                        "code": "COST_CENTRE_REQUIRED",
                        "when": {"path": "invoice.cost_center", "op": "eq", "value": None},
                        "title": "A cost centre is required before approval",
                        "detail": (
                            "The invoice has no cost centre, so the booking cannot be "
                            "attributed. The engine will not guess one."
                        ),
                        "required_role": "OPERATOR",
                        "reason": "A required accounting field is absent.",
                        "policy_citation": (
                            "FinancePolicy 2.1: every booked invoice needs a cost centre."
                        ),
                        "question": "Which cost centre should invoice INV-2041 be booked to?",
                        "inputs": [
                            {
                                "key": "cost_center",
                                "label": "Cost centre",
                                "path": "invoice.cost_center",
                                "required": True,
                                "type": "string",
                                "placeholder": "CC-200",
                            }
                        ],
                        "evidence": [
                            {
                                "source": "invoice.cost_center",
                                "label": "Cost centre on the invoice",
                                "relation": "required field",
                                "record_ref": "INV-2041",
                            },
                            {
                                "source": "invoice.id",
                                "label": "Invoice",
                                "relation": "blocked record",
                            },
                        ],
                        "resume_conditions": [
                            {
                                "code": "cost_center_present",
                                "path": "invoice.cost_center",
                                "op": "exists",
                            }
                        ],
                    },
                    {
                        "kind": "VALIDATION_FAILED",
                        "code": "POLICY_EXPIRED",
                        "when": {"path": "policy.expires_at", "op": "expired"},
                        "title": "The approval policy has expired",
                        "detail": (
                            "The policy that authorises this approval is past its expiry "
                            "date. Any approval made under it would be unsupported."
                        ),
                        "required_role": "REVIEWER",
                        "reason": "The governing policy is no longer in force.",
                        "policy_citation": (
                            "PolicyControl 7.3: expired policies cannot authorise payment."
                        ),
                        "question": (
                            "Record the renewed policy before this invoice can be approved."
                        ),
                        "inputs": [
                            {
                                "key": "policy_expires_at",
                                "label": "Renewed policy expiry (ISO 8601)",
                                "path": "policy.expires_at",
                                "required": True,
                                "type": "datetime",
                                "placeholder": "2027-04-01T00:00:00Z",
                            },
                            {
                                "key": "policy_renewal_reference",
                                "label": "Renewal reference",
                                "path": "policy.renewal_reference",
                                "required": True,
                                "type": "string",
                                "placeholder": "AP-2027-01",
                            },
                        ],
                        "evidence": [
                            {
                                "source": "policy.expires_at",
                                "label": "Policy expiry",
                                "relation": "governing policy",
                                "record_ref": "AP-2026-04",
                                "expires_path": "policy.expires_at",
                            },
                            {
                                "source": "invoice.amount_cents",
                                "label": "Amount requiring authorisation (cents)",
                                "relation": "amount in scope",
                            },
                        ],
                        "resume_conditions": [
                            {
                                "code": "policy_valid",
                                "path": "policy.expires_at",
                                "op": "not_expired",
                            }
                        ],
                    },
                ],
            },
            "postconditions": [
                {"path": "invoice.status", "op": "eq", "value": "APPROVED_WITH_EXCEPTION"}
            ],
            "side_effect_class": "IRREVERSIBLE",
        },
        {
            "ordinal": 3,
            "node_key": "schedule_payment",
            "kind": "SUBMIT",
            "label": "Schedule the payment",
            "depends_on": ["approve_invoice"],
            "target": {"surface": "payment"},
            "input": {
                "effects": [
                    {"op": "set", "path": "invoice.status", "value": "SCHEDULED"},
                    {"op": "set", "path": "invoice.payment_reference", "value": "pay_demo_7f3a"},
                ]
            },
            "postconditions": [
                {"path": "invoice.status", "op": "eq", "value": "SCHEDULED"},
            ],
            "side_effect_class": "COMPENSABLE",
        },
        {
            "ordinal": 4,
            "node_key": "disburse",
            "kind": "PAYMENT_SIMULATED",
            "label": "Disburse funds (sandbox)",
            "depends_on": ["schedule_payment"],
            "target": {"surface": "sandbox-gateway", "origin": "http://127.0.0.1:8100"},
            "input": {
                "effects": [
                    {
                        "op": "append",
                        "path": "invoice.ledger",
                        "value": {
                            "entry": "debit",
                            "amount_cents": 184500,
                            "reference": "pay_demo_7f3a",
                        },
                    },
                    {"op": "increment", "path": "invoice.ledger_count", "value": 1},
                    {"op": "set", "path": "invoice.status", "value": "PAID"},
                ],
                "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                "recovery": {
                    "strategy": "RESUME",
                    "reversible": True,
                    "allowed_strategies": ["RESUME", "COMPENSATE"],
                    "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
                    "idempotency_path": "invoice.payment_reference",
                    "contract": [
                        {
                            "path": "invoice.status",
                            "op": "in",
                            "value": ["SCHEDULED", "PAID"],
                        },
                        {"path": "invoice.ledger_count", "op": "lte", "value": 1},
                    ],
                    "compensate_contract": [
                        {"path": "invoice.status", "op": "eq", "value": "SCHEDULED"},
                        {"path": "invoice.ledger_count", "op": "eq", "value": 0},
                    ],
                    "compensate": [
                        {"op": "remove_last", "path": "invoice.ledger"},
                        {"op": "decrement", "path": "invoice.ledger_count", "value": 1},
                        {"op": "set", "path": "invoice.status", "value": "SCHEDULED"},
                        {"op": "set", "path": "invoice.payment_reference", "value": None},
                    ],
                    "max_attempts": 1,
                    "notes": (
                        "The sandbox gateway supports idempotency on payment_reference and "
                        "an explicit compensating reversal."
                    ),
                },
            },
            "postconditions": [{"path": "invoice.ledger_count", "op": "gte", "value": 1}],
            "side_effect_class": "IRREVERSIBLE",
        },
    ],
    "invariants": [
        {
            "name": "Invoice reaches PAID",
            "description": "A run only succeeds if the invoice ends in the PAID state.",
            "kind": "TERMINAL_STATE",
            "severity": "CRITICAL",
            "expression": {"kind": "state", "path": "invoice.status", "op": "eq", "value": "PAID"},
        },
        {
            "name": "Exactly one ledger entry",
            "description": "Disbursement must never write more than one ledger entry.",
            "kind": "CROSS_ACTION",
            "severity": "CRITICAL",
            "expression": {
                "kind": "state",
                "path": "invoice.ledger_count",
                "op": "eq",
                "value": 1,
            },
        },
    ],
}

# (name, sandbox profile, minutes ago)
DISCREPANCY_RUNS: list[dict[str, Any]] = [
    {
        "name": "Variance waiting on finance",
        "profile": "discrepancy",
        "minutes_ago": 38,
    },
    {
        "name": "Missing cost centre",
        "profile": "missing_cost_center",
        "minutes_ago": 21,
    },
    {
        "name": "Expired approval policy",
        "profile": "expired_policy",
        "minutes_ago": 92,
    },
]


# ---------------------------------------------------------------------------
# Vendor payout workspace
#
# This is the recovery + feedback-loop demonstration. Version 1 is a broken
# configuration: the disbursement writes the ledger entry but never marks the
# invoice PAID, so every run violates its terminal-state invariant. Version 2
# fixes that. The failed v1 run is recoverable through an explicit compensating
# policy, and the regression test generated from it can be compared across both
# versions.
# ---------------------------------------------------------------------------

VENDOR_BATCH = "demo-vendor-payout-v1"
VENDOR_SLUG = "vendor-payout"

_VENDOR_LOAD = {
    "ordinal": 0,
    "node_key": "load_invoice",
    "kind": "NAVIGATE",
    "label": "Load the approved invoice",
    "target": {"surface": "invoice"},
    "input": {"effects": [{"op": "set", "path": "review.loaded", "value": True}]},
    "postconditions": [{"path": "review.loaded", "op": "eq", "value": True}],
    "side_effect_class": "READ_ONLY",
}

_VENDOR_PREPARE = {
    "ordinal": 1,
    "node_key": "prepare_payout",
    "kind": "SUBMIT",
    "label": "Prepare the payout",
    "depends_on": ["load_invoice"],
    "target": {"surface": "payment"},
    "input": {
        "effects": [
            {"op": "set", "path": "invoice.status", "value": "SCHEDULED"},
            {"op": "set", "path": "invoice.payment_reference", "value": "pay_demo_9c1"},
        ]
    },
    "postconditions": [{"path": "invoice.status", "op": "eq", "value": "SCHEDULED"}],
    "side_effect_class": "COMPENSABLE",
}

_VENDOR_NOTIFY = {
    "ordinal": 2,
    "node_key": "notify_vendor",
    "kind": "EMAIL_SEND",
    "label": "Notify the vendor",
    "depends_on": ["prepare_payout"],
    "target": {"surface": "email-gateway"},
    "input": {
        "effects": [
            {"op": "set", "path": "review.vendor_notified", "value": True},
        ],
        "recovery": {
            "strategy": "MANUAL",
            "reversible": False,
            "contract": [],
            "notes": (
                "A sent email cannot be recalled. There is no tested inverse, so recovery "
                "requires a human."
            ),
        },
    },
    "postconditions": [{"path": "review.vendor_notified", "op": "eq", "value": True}],
    "side_effect_class": "IRREVERSIBLE",
}

_VENDOR_DISBURSE_EFFECTS: list[dict[str, Any]] = [
    {
        "op": "append",
        "path": "invoice.ledger",
        "value": {"entry": "debit", "amount_cents": 184500, "reference": "pay_demo_9c1"},
    },
    {"op": "increment", "path": "invoice.ledger_count", "value": 1},
]

_VENDOR_DISBURSE: dict[str, Any] = {
    "ordinal": 3,
    "node_key": "disburse",
    "kind": "PAYMENT_SIMULATED",
    "label": "Disburse funds (sandbox)",
    "depends_on": ["notify_vendor"],
    "target": {"surface": "sandbox-gateway", "origin": "http://127.0.0.1:8100"},
    "input": {
        "effects": _VENDOR_DISBURSE_EFFECTS,
        "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
        "recovery": {
            "strategy": "COMPENSATE",
            "reversible": True,
            "allowed_strategies": ["COMPENSATE", "RESUME"],
            "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
            "idempotency_path": "invoice.payment_reference",
            "contract": [
                {"path": "invoice.status", "op": "in", "value": ["SCHEDULED", "PAID"]},
                {"path": "invoice.ledger_count", "op": "lte", "value": 1},
            ],
            "compensate_contract": [
                {"path": "invoice.status", "op": "eq", "value": "SCHEDULED"},
                {"path": "invoice.ledger_count", "op": "eq", "value": 0},
                {"path": "review.payout_reversed", "op": "eq", "value": True},
            ],
            "compensate": [
                {"op": "remove_last", "path": "invoice.ledger"},
                {"op": "decrement", "path": "invoice.ledger_count", "value": 1},
                {"op": "set", "path": "invoice.status", "value": "SCHEDULED"},
                {"op": "set", "path": "invoice.payment_reference", "value": None},
                {"op": "set", "path": "review.payout_reversed", "value": True},
            ],
            "max_attempts": 1,
            "notes": (
                "The sandbox gateway supports a compensating reversal and an idempotency "
                "key on payment_reference."
            ),
        },
    },
    "postconditions": [{"path": "invoice.ledger_count", "op": "gte", "value": 1}],
    "side_effect_class": "IRREVERSIBLE",
}

_VENDOR_INVARIANTS = [
    {
        "name": "Invoice reaches PAID",
        "description": "A payout run only succeeds if the invoice ends in the PAID state.",
        "kind": "TERMINAL_STATE",
        "severity": "CRITICAL",
        "expression": {"kind": "state", "path": "invoice.status", "op": "eq", "value": "PAID"},
    },
    {
        "name": "Exactly one ledger entry",
        "description": "Disbursement must never write more than one ledger entry.",
        "kind": "CROSS_ACTION",
        "severity": "CRITICAL",
        "expression": {"kind": "state", "path": "invoice.ledger_count", "op": "eq", "value": 1},
    },
]

VENDOR_WORKFLOW_BROKEN: dict[str, Any] = {
    "name": "Vendor Payout",
    "description": (
        "Load an approved invoice, notify the vendor, then disburse funds in the sandbox. "
        "Version 1 is deliberately broken: the disbursement writes the ledger entry but "
        "never marks the invoice PAID."
    ),
    "owner": "demo-workspace",
    "actions": [_VENDOR_LOAD, _VENDOR_PREPARE, _VENDOR_NOTIFY, _VENDOR_DISBURSE],
    "invariants": _VENDOR_INVARIANTS,
}

VENDOR_WORKFLOW_FIXED: dict[str, Any] = {
    "name": "Vendor Payout",
    "description": "The corrected payout flow: the disbursement records the ledger entry and PAID.",
    "owner": "demo-workspace",
    "actions": [
        _VENDOR_LOAD,
        _VENDOR_PREPARE,
        _VENDOR_NOTIFY,
        {
            **_VENDOR_DISBURSE,
            "input": {
                **_VENDOR_DISBURSE["input"],
                "effects": [
                    *_VENDOR_DISBURSE_EFFECTS,
                    {"op": "set", "path": "invoice.status", "value": "PAID"},
                ],
            },
        },
    ],
    "invariants": _VENDOR_INVARIANTS,
}

VENDOR_SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "Vendor notification lost",
        "description": (
            "The connection drops before the email request reaches the gateway. Because a "
            "sent email cannot be recalled, recovery requires a human even though no effect "
            "was observed."
        ),
        "target_ordinal": 2,
        "fault_type": "NETWORK_DROP",
        "parameters": {},
        "seed": 71,
    }
]

VENDOR_RUNS: list[dict[str, Any]] = [
    {
        "name": "Broken v1 payout",
        "version": "v1",
        "scenario": None,
        "minutes_ago": 55,
    },
    {
        "name": "Vendor notification lost",
        "version": "v2",
        "scenario": 0,
        "minutes_ago": 12,
    },
]


# ---------------------------------------------------------------------------
# Invoice processing — the end-to-end vertical slice
#
# One workflow that walks the whole product: retrieve the invoice, retrieve its
# purchase order, validate and compare, block on a genuine discrepancy, ask a
# human with evidence, record the result, and expose every failure mode the
# console needs to demonstrate.
# ---------------------------------------------------------------------------

PROCESSING_BATCH = "demo-invoice-processing-v1"
PROCESSING_SLUG = "invoice-processing"

_RETRIEVE_INVOICE = {
    "ordinal": 0,
    "node_key": "retrieve_invoice",
    "kind": "API_CALL",
    "label": "Retrieve the invoice",
    "target": {"surface": "invoice-api", "record": "invoice"},
    "input": {
        "effects": [{"op": "set", "path": "invoice.retrieved", "value": True}],
        "probe": {"path": "invoice.retrieved", "op": "eq", "value": True},
        "recovery": {
            "strategy": "RESUME",
            "reversible": True,
            "allowed_strategies": ["RESUME"],
            "probe": {"path": "invoice.retrieved", "op": "eq", "value": True},
            "idempotency_path": "invoice.id",
            "contract": [{"path": "session.authenticated", "op": "eq", "value": True}],
            "notes": "Reading the invoice is idempotent on the invoice id.",
        },
    },
    "postconditions": [{"path": "invoice.retrieved", "op": "eq", "value": True}],
    "side_effect_class": "READ_ONLY",
}

_RETRIEVE_PURCHASE_ORDER = {
    "ordinal": 1,
    "node_key": "retrieve_purchase_order",
    "kind": "API_CALL",
    "label": "Retrieve the purchase order",
    "depends_on": ["retrieve_invoice"],
    "target": {"surface": "purchase-order-api", "record": "purchase_order"},
    "input": {
        "effects": [{"op": "set", "path": "purchase_order.retrieved", "value": True}],
        "probe": {"path": "purchase_order.retrieved", "op": "eq", "value": True},
        "recovery": {
            "strategy": "RESUME",
            "reversible": True,
            "allowed_strategies": ["RESUME"],
            "probe": {"path": "purchase_order.retrieved", "op": "eq", "value": True},
            "idempotency_path": "purchase_order.id",
            "contract": [{"path": "invoice.retrieved", "op": "eq", "value": True}],
            "notes": (
                "Reading the purchase order is idempotent. A timeout before the call "
                "is safe to retry; a lost response is probed first."
            ),
        },
    },
    "postconditions": [{"path": "purchase_order.retrieved", "op": "eq", "value": True}],
    "side_effect_class": "READ_ONLY",
}

_VALIDATE_FIELDS = {
    "ordinal": 2,
    "node_key": "validate_fields",
    "node_type": "VALIDATION",
    "kind": "ASSERT",
    "label": "Validate required fields",
    "depends_on": ["retrieve_purchase_order"],
    "target": {"surface": "validation"},
    "input": {
        "path": "invoice",
        "schema": {
            "type": "object",
            "required": ["id", "vendor", "amount_cents", "currency", "status"],
            "properties": {
                "id": {"type": "string", "minLength": 3},
                "vendor": {"type": "string", "minLength": 2},
                "amount_cents": {"type": "integer", "minimum": 1},
                "currency": {"type": "string", "minLength": 3},
                "status": {"type": "string", "enum": ["SUBMITTED", "APPROVED"]},
            },
        },
    },
    "postconditions": [{"path": "purchase_order.retrieved", "op": "eq", "value": True}],
    "side_effect_class": "READ_ONLY",
}

_COMPARE_TOTALS = {
    "ordinal": 3,
    "node_key": "compare_totals",
    "kind": "EXTRACT",
    "label": "Compare invoice and purchase-order totals",
    "depends_on": ["validate_fields"],
    "target": {"surface": "validation"},
    "input": {
        "effects": [
            {
                "op": "copy",
                "from": "invoice.amount_cents",
                "path": "review.invoice_amount_cents",
            },
            {
                "op": "copy",
                "from": "purchase_order.amount_cents",
                "path": "review.po_amount_cents",
            },
            {
                "op": "subtract",
                "left": "invoice.amount_cents",
                "right": "purchase_order.amount_cents",
                "path": "review.delta_cents",
            },
        ]
    },
    "postconditions": [{"path": "review.delta_cents", "op": "exists"}],
    "side_effect_class": "READ_ONLY",
}

_APPROVE_INVOICE = {
    "ordinal": 4,
    "node_key": "approve_invoice",
    "kind": "AUTHORIZE",
    "label": "Approve the invoice for payment",
    "depends_on": ["compare_totals"],
    "target": {"surface": "approval", "role": "button", "name": "Approve invoice"},
    "input": {
        "effects": [
            {"op": "set", "path": "invoice.status", "value": "APPROVED"},
            {"op": "set", "path": "invoice.approver", "value": "local-operator"},
        ],
        "decision": {
            "question": "Authorize payment of invoice INV-2041 for 1,845.00 USD?",
            "options": [
                {
                    "value": "approve",
                    "label": "Authorize payment",
                    "consequence": "Continues to schedule and disburse in the sandbox",
                },
                {
                    "value": "reject",
                    "label": "Reject",
                    "consequence": "Stops the run; nothing is scheduled",
                },
            ],
            "recommended": "approve",
            "reason": "Approving an invoice changes financial state.",
            "policy_citation": (
                "ActionPolicy 4.2: IRREVERSIBLE actions require explicit human authorization."
            ),
        },
        "blockers": [
            {
                "kind": "CONFLICTING_EVIDENCE",
                "code": "INVOICE_PO_VARIANCE",
                "when": {"path": "review.delta_cents", "op": "ne", "value": 0},
                "title": "Invoice total does not match the purchase order",
                "detail": (
                    "The invoice and its purchase order disagree. Paying without a "
                    "decision would authorise an amount the PO does not support."
                ),
                "required_role": "FINANCE_APPROVER",
                "reason": (
                    "Two system-of-record documents disagree; only a finance approver "
                    "can accept the variance."
                ),
                "policy_citation": (
                    "ActionPolicy 4.2: a variance between the invoice and its purchase "
                    "order requires finance approval."
                ),
                "question": (
                    "Approve invoice INV-2041 for 1,845.00 USD despite a 45.00 USD "
                    "variance against PO-8841?"
                ),
                "options": [
                    {
                        "value": "approve",
                        "label": "Approve the variance",
                        "consequence": "Pays 1,845.00 USD and records the exception",
                    },
                    {
                        "value": "reject",
                        "label": "Reject the invoice",
                        "consequence": "Stops the run; nothing is paid",
                    },
                ],
                "recommended": "reject",
                "evidence": [
                    {
                        "source": "invoice.amount_cents",
                        "label": "Invoice total (cents)",
                        "relation": "observed amount",
                        "record_ref": "INV-2041",
                        "validate": {
                            "path": "invoice.amount_cents",
                            "op": "eq",
                            "value": 180000,
                        },
                    },
                    {
                        "source": "purchase_order.amount_cents",
                        "label": "Purchase order total (cents)",
                        "relation": "authorised amount",
                        "record_ref": "PO-8841",
                        "validate": {
                            "path": "purchase_order.amount_cents",
                            "op": "eq",
                            "value": 180000,
                        },
                    },
                    {
                        "source": "review.delta_cents",
                        "label": "Variance (cents)",
                        "relation": "invoice minus purchase order",
                    },
                    {
                        "source_kind": "AUDIT_EVENT",
                        "event_type": "ACTION_SUCCEEDED",
                        "node_key": "compare_totals",
                        "label": "Comparison step",
                        "relation": "produced the variance for this node",
                    },
                ],
                "resume_conditions": [
                    {
                        "code": "invoice_still_open",
                        "path": "invoice.status",
                        "op": "eq",
                        "value": "SUBMITTED",
                    },
                    {
                        "code": "variance_recorded",
                        "path": "review.delta_cents",
                        "op": "exists",
                    },
                ],
            },
            {
                "kind": "MISSING_INPUT",
                "code": "COST_CENTRE_REQUIRED",
                "when": {"path": "invoice.cost_center", "op": "eq", "value": None},
                "title": "A cost centre is required before approval",
                "detail": "The invoice has no cost centre, so the booking cannot be attributed.",
                "required_role": "OPERATOR",
                "reason": "A required accounting field is absent.",
                "policy_citation": ("FinancePolicy 2.1: every booked invoice needs a cost centre."),
                "question": "Which cost centre should invoice INV-2041 be booked to?",
                "inputs": [
                    {
                        "key": "cost_center",
                        "label": "Cost centre",
                        "path": "invoice.cost_center",
                        "required": True,
                        "type": "string",
                        "placeholder": "CC-200",
                    }
                ],
                "evidence": [
                    {
                        "source": "invoice.cost_center",
                        "label": "Cost centre on the invoice",
                        "relation": "required field",
                        "record_ref": "INV-2041",
                    },
                    {
                        "source": "invoice.id",
                        "label": "Invoice",
                        "relation": "blocked record",
                    },
                ],
                "resume_conditions": [
                    {"code": "cost_center_present", "path": "invoice.cost_center", "op": "exists"}
                ],
            },
        ],
    },
    "postconditions": [{"path": "invoice.status", "op": "eq", "value": "APPROVED"}],
    "side_effect_class": "IRREVERSIBLE",
    "requires_authorization": True,
}

_SCHEDULE_PAYMENT = {
    "ordinal": 5,
    "node_key": "schedule_payment",
    "kind": "SUBMIT",
    "label": "Schedule the payment",
    "depends_on": ["approve_invoice"],
    "target": {"surface": "payment"},
    "input": {
        "effects": [
            {"op": "set", "path": "invoice.status", "value": "SCHEDULED"},
            {"op": "set", "path": "invoice.payment_reference", "value": "pay_demo_7f3a"},
        ]
    },
    "postconditions": [{"path": "invoice.status", "op": "eq", "value": "SCHEDULED"}],
    "side_effect_class": "COMPENSABLE",
}

_DISBURSE = {
    "ordinal": 6,
    "node_key": "disburse",
    "kind": "PAYMENT_SIMULATED",
    "label": "Disburse funds (sandbox)",
    "depends_on": ["schedule_payment"],
    "target": {"surface": "sandbox-gateway", "origin": "http://127.0.0.1:8100"},
    "input": {
        "effects": [
            {
                "op": "append",
                "path": "invoice.ledger",
                "value": {"entry": "debit", "amount_cents": 184500, "reference": "pay_demo_7f3a"},
            },
            {"op": "increment", "path": "invoice.ledger_count", "value": 1},
            {"op": "set", "path": "invoice.status", "value": "PAID"},
        ],
        "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
        "recovery": {
            "strategy": "RESUME",
            "reversible": True,
            "allowed_strategies": ["RESUME", "COMPENSATE"],
            "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
            "idempotency_path": "invoice.payment_reference",
            "contract": [
                {"path": "invoice.status", "op": "in", "value": ["SCHEDULED", "PAID"]},
                {"path": "invoice.ledger_count", "op": "lte", "value": 1},
            ],
            "compensate_contract": [
                {"path": "invoice.status", "op": "eq", "value": "SCHEDULED"},
                {"path": "invoice.ledger_count", "op": "eq", "value": 0},
            ],
            "compensate": [
                {"op": "remove_last", "path": "invoice.ledger"},
                {"op": "decrement", "path": "invoice.ledger_count", "value": 1},
                {"op": "set", "path": "invoice.status", "value": "SCHEDULED"},
                {"op": "set", "path": "invoice.payment_reference", "value": None},
            ],
            "max_attempts": 1,
            "notes": (
                "The sandbox gateway supports a compensating reversal and an "
                "idempotency key on payment_reference."
            ),
        },
    },
    "postconditions": [{"path": "invoice.ledger_count", "op": "gte", "value": 1}],
    "side_effect_class": "IRREVERSIBLE",
}

_RECORD_RESULT = {
    "ordinal": 7,
    "node_key": "record_result",
    "kind": "ASSERT",
    "label": "Record the final result",
    "depends_on": ["disburse"],
    "target": {"surface": "result"},
    "input": {
        "effects": [
            {"op": "set", "path": "review.final_result", "value": "PAID"},
            {"op": "set", "path": "review.result_recorded", "value": True},
        ]
    },
    "postconditions": [
        {"path": "review.final_result", "op": "eq", "value": "PAID"},
        {"path": "review.result_recorded", "op": "eq", "value": True},
    ],
    "side_effect_class": "IDEMPOTENT",
}

INVOICE_PROCESSING_WORKFLOW: dict[str, Any] = {
    "name": "Invoice Processing",
    "description": (
        "The end-to-end invoice vertical slice: retrieve the invoice and its purchase "
        "order, validate required fields, compare totals, block on a genuine "
        "discrepancy for a human decision, then record the final result."
    ),
    "owner": "demo-workspace",
    "actions": [
        _RETRIEVE_INVOICE,
        _RETRIEVE_PURCHASE_ORDER,
        _VALIDATE_FIELDS,
        _COMPARE_TOTALS,
        _APPROVE_INVOICE,
        _SCHEDULE_PAYMENT,
        _DISBURSE,
        _RECORD_RESULT,
    ],
    "invariants": [
        {
            "name": "Invoice reaches PAID",
            "description": "A run only succeeds if the invoice ends in the PAID state.",
            "kind": "TERMINAL_STATE",
            "severity": "CRITICAL",
            "expression": {"kind": "state", "path": "invoice.status", "op": "eq", "value": "PAID"},
        },
        {
            "name": "Exactly one ledger entry",
            "description": "Disbursement must never write more than one ledger entry.",
            "kind": "CROSS_ACTION",
            "severity": "CRITICAL",
            "expression": {
                "kind": "state",
                "path": "invoice.ledger_count",
                "op": "eq",
                "value": 1,
            },
        },
        {
            "name": "Final result recorded",
            "description": "The run must persist an explicit final result for the invoice.",
            "kind": "TERMINAL_STATE",
            "severity": "CRITICAL",
            "expression": {
                "kind": "state",
                "path": "review.final_result",
                "op": "eq",
                "value": "PAID",
            },
        },
        {
            "name": "Financial success requires authorization",
            "description": (
                "An authorization-gated action may only succeed after a human grant "
                "or an explicit pre-authorization event."
            ),
            "kind": "AUTHORIZATION",
            "severity": "CRITICAL",
            "expression": {"kind": "authorization"},
        },
    ],
}

INVOICE_PROCESSING_SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "PO API timeout",
        "description": (
            "The purchase-order API times out before executing. No effect is applied, the "
            "node becomes recoverable and a resume retry is safe and idempotent."
        ),
        "target_ordinal": 1,
        "fault_type": "TIMEOUT_BEFORE_EXECUTION",
        "parameters": {},
        "seed": 101,
    },
    {
        "name": "Invoice total mismatch",
        "description": (
            "The purchase order returns a total 45.00 USD below the invoice. Validation "
            "passes, the comparison records the variance, and approval blocks for finance."
        ),
        "target_ordinal": 1,
        "fault_type": "CONTRADICTORY_TOTALS",
        "parameters": {"po_amount_cents": 180000, "pre_authorize": False},
        "seed": 102,
    },
    {
        "name": "Lost response after payment executes",
        "description": (
            "The disbursement applies its effect and the response is lost. The engine must "
            "probe the target state and reconcile instead of paying twice."
        ),
        "target_ordinal": 6,
        "fault_type": "RESPONSE_LOST",
        "parameters": {},
        "seed": 103,
    },
    {
        "name": "Approval never arrives",
        "description": (
            "The decision window is shortened to zero. A late approval must be refused and "
            "the run must fail without executing the gated action."
        ),
        "target_ordinal": 4,
        "fault_type": "DELAYED_APPROVAL",
        "parameters": {"delay_seconds": 600, "pre_authorize": False},
        "seed": 104,
    },
    {
        "name": "Partial ledger commit",
        "description": (
            "The gateway writes the ledger entry and dies before marking the invoice PAID. "
            "The recovery policy compensates the unsafe mutation."
        ),
        "target_ordinal": 6,
        "fault_type": "PARTIAL_COMMIT",
        "parameters": {"commit_fraction": 0.5},
        "seed": 105,
    },
]

INVOICE_PROCESSING_RUNS: list[dict[str, Any]] = [
    {
        "name": "Normal invoice execution",
        "profile": "standard",
        "scenario": None,
        "authorized": True,
        "minutes_ago": 140,
    },
    {
        "name": "Discrepancy waiting on finance",
        "profile": "discrepancy",
        "scenario": None,
        "authorized": False,
        "minutes_ago": 46,
    },
    {
        "name": "PO timeout awaiting recovery",
        "profile": "standard",
        "scenario": 0,
        "authorized": True,
        "minutes_ago": 18,
    },
]
