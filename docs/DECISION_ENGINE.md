# FLOWSTATE AI — Human Decision Engine

| Field | Value |
| --- | --- |
| Status | Implemented |
| Related | [DOMAIN_MODEL.md](DOMAIN_MODEL.md), [STATE_MACHINES.md](STATE_MACHINES.md), [SECURITY.md](SECURITY.md) |

The decision engine answers one question: **why is this node blocked, and what is the
smallest useful thing a human can do about it?** It never asks a human to approve a
run whose state it has not first explained.

## 1. Blocker taxonomy

A blocker is a persisted classification of run state. Exactly one is primary per
decision; any others are recorded as secondary so the inbox is not lying about how many
problems exist.

| Cause | Meaning | Default role | Typical remedy |
| --- | --- | --- | --- |
| `MISSING_INPUT` | A required field is absent from run state | `OPERATOR` | Provide the declared value |
| `VALIDATION_FAILED` | A check failed, including expired evidence | `REVIEWER` | Refresh the evidence or accept/reject |
| `CONFLICTING_EVIDENCE` | Two system-of-record facts disagree | `FINANCE_APPROVER` | Accept the variance or reject |
| `PERMISSION_REQUIRED` | The acting identity lacks a declared permission | `ADMIN` | Grant authority to a human |
| `HUMAN_APPROVAL_REQUIRED` | The action is authorization-gated | `FINANCE_APPROVER` | Authorize or reject |
| `TOOL_UNAVAILABLE` | The declared tool/handler is not available | `ADMIN` | Escalate to an administrator |
| `UNKNOWN_OUTCOME` | An external effect could not be confirmed | `REVIEWER` | Resolve with evidence, not a retry |
| `RECOVERY_PREREQUISITE_MISSING` | No verified checkpoint exists | `OPERATOR` | Choose a recovery strategy |

A workflow declares blockers on a node:

```json
{
  "kind": "CONFLICTING_EVIDENCE",
  "code": "INVOICE_PO_VARIANCE",
  "when": {"path": "review.delta_cents", "op": "ne", "value": 0},
  "required_role": "FINANCE_APPROVER",
  "title": "Invoice total does not match the purchase order",
  "evidence": [
    {"source": "invoice.amount_cents", "label": "Invoice total", "record_ref": "INV-2041",
     "validate": {"path": "invoice.amount_cents", "op": "eq", "value": 180000}},
    {"source": "purchase_order.amount_cents", "label": "PO total", "record_ref": "PO-8841"},
    {"source_kind": "AUDIT_EVENT", "event_type": "ACTION_SUCCEEDED",
     "node_key": "match_totals", "label": "Comparison step"}
  ],
  "resume_conditions": [
    {"code": "invoice_still_open", "path": "invoice.status", "op": "eq", "value": "SUBMITTED"}
  ]
}
```

`when` is evaluated with the injected clock, so `{"op": "expired"}` is deterministic.

## 2. Evidence packet

Evidence is **facts**, never prose. Each row records:

| Field | Meaning |
| --- | --- |
| `source_kind` | `STATE_FIELD` or `AUDIT_EVENT` |
| `source_path` | The exact state path or event type that was read |
| `record_ref` | The record the fact came from (e.g. `PO-8841`) |
| `value_json` | The observed value, or the audit event's seq/type/hash |
| `validation` | `PASS`, `FAIL`, `MISSING`, `EXPIRED`, `VALID` or `INFO` |
| `observed_at` | When the backend read it |
| `fresh_until` | Optional freshness deadline declared by the workflow |

The packet is hashed (`evidence_hash`). The hash is the contract between the human and
the resumption check: if the underlying records change after the request was raised, the
hash will not match and the run stays blocked.

A required field that is present but `null` is classified `MISSING`, because "the key
exists" is not the same as "the value is usable".

## 3. Decision request

Each request carries: what is blocked (node, ordinal, workflow version), why (blocker
code, title, detail, policy citation, required role), the evidence packet, the exact
question, permitted choices with consequences, the conditions that will be checked before
resuming, and the input fields the blocker needs.

Only the **lowest-ordinal** blocking node is asked about. A later blocker may disappear
depending on the answer, so surfacing it would be noise.

## 4. Actions

| Action | Effect on the run | Recorded as |
| --- | --- | --- |
| `approve` | Re-check, then resume from the blocked node; browser-backed runs replay from ordinal zero in a fresh session | `APPROVE` + `RESUME`, `DECISION_GRANTED` |
| `reject` | Run becomes `FAILED` with `DECISION_DENIED` | `REJECT`, `DECISION_DENIED` |
| `provide-info` | Writes declared fields into run state, then re-checks | `PROVIDE_INFO`, `DECISION_INFO_PROVIDED` |
| `defer` | Run stays `AWAITING_DECISION`; the request stays open | `DEFER`, `DECISION_DEFERRED` |

`provide-info` only accepts keys the blocker declared. Unknown keys are rejected, and a
value that does not clear the blocker returns `INFO_INSUFFICIENT` while leaving the run
blocked.

## 5. Resumption checks

An approval is a request to re-check, not an override. Before resuming, the backend
verifies, in order:

1. **Authority** — the acting role satisfies the blocker's required role.
2. **Freshness** — no evidence row is `EXPIRED` or `MISSING`, and no `fresh_until` has
   passed.
3. **Integrity** — the recomputed evidence hash matches the hash shown to the human.
4. **Declared conditions** — every `resume_conditions` predicate holds.
5. **Preconditions** — the node's own preconditions still pass in the restored state.

Any failure returns `409`/`403` with `checks[]`, leaves the run `AWAITING_DECISION`, and
records the refusal. Stale evidence refreshes the packet so the next human sees current
facts.

A capability token is still issued on a successful approval, but it is scoped to one
`(run, ordinal)`, single-use, TTL-bounded and consumed by the resume.

## 6. Authority model

| Role | Covers |
| --- | --- |
| `OPERATOR` | Missing input, recovery prerequisites |
| `REVIEWER` | Validation failures, unknown outcomes |
| `FINANCE_APPROVER` | Conflicting evidence, financial approval |
| `ADMIN` | Everything |

`ADMIN` is the local default so a single-operator demo is usable. Agent identities
(`X-Flowstate-Actor-Kind: AGENT`) are rejected for every decision action with
`HUMAN_ACTOR_REQUIRED`, regardless of role.

Refusals are auditable outcomes: the role-denied and stale-evidence paths commit their
`DECISION_ROLE_DENIED` / `DECISION_EVIDENCE_CHANGED` events before returning the problem
response.

## 7. Persistence and audit

| Table | Purpose |
| --- | --- |
| `run_blockers` | One row per detected blocker, with the declaration and evidence hash |
| `blocker_evidence` | Resolved facts supporting a blocker |
| `human_decisions` | The request, its role requirement, conditions and provided input |
| `decision_records` | Append-only history of every action and refusal |
| `capability_tokens` | Issued, consumed, single-use |

Every action also appends to the run's hash-linked `ExecutionEvent` chain:
`BLOCKER_DETECTED`, `DECISION_REQUESTED`, `DECISION_GRANTED` / `DECISION_DENIED` /
`DECISION_DEFERRED` / `DECISION_INFO_PROVIDED` / `DECISION_EVIDENCE_CHANGED` /
`DECISION_ROLE_DENIED`, and `BLOCKER_RESOLVED`.

## 8. Tests

`backend/tests/integration/test_decision_engine.py` covers missing information,
contradictory records, expired evidence, unauthorized approval, agent identity, rejection,
deferral, successful resumption, insufficient supplied input, filters, and history
persistence.