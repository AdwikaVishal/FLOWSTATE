# FLOWSTATE AI — Invoice Processing Vertical Slice

| Field | Value |
| --- | --- |
| Status | Implemented and passing |
| Related | [PRD.md](PRD.md), [DECISION_ENGINE.md](DECISION_ENGINE.md), [RECOVERY.md](RECOVERY.md), [ALAN_INTEGRATION.md](ALAN_INTEGRATION.md) |

One workflow connects every module: learning, normal execution, chaos, the human
decision engine, verified recovery, regression tests and the audit chain. The console
page at `/slice` presents the stages and reads every value from the backend.

## 1. The workflow

`invoice-processing` v1 has eight nodes:

| # | Node | Purpose |
| --- | --- | --- |
| 0 | `retrieve_invoice` | Fetch the invoice (idempotent, recoverable) |
| 1 | `retrieve_purchase_order` | Fetch the purchase order (idempotent, recoverable) |
| 2 | `validate_fields` | Schema validation of required invoice fields |
| 3 | `compare_totals` | Compute the invoice − PO variance |
| 4 | `approve_invoice` | Authorization-gated; declares the discrepancy blocker |
| 5 | `schedule_payment` | Set `SCHEDULED` and a payment reference |
| 6 | `disburse` | Sandbox disbursement with a compensating recovery policy |
| 7 | `record_result` | Persist `review.final_result = PAID` |

Invariants: `Invoice reaches PAID`, `Exactly one ledger entry`, `Final result
recorded`, and `Financial success requires authorization`.

## 2. Chaos scenarios

| Scenario | Fault | Expected behaviour |
| --- | --- | --- |
| PO API timeout | `TIMEOUT_BEFORE_EXECUTION` at node 1 | Node fails safely; recovery plan is `RESUME` with a verified checkpoint and an idempotency key |
| Invoice total mismatch | `CONTRADICTORY_TOTALS` at node 1 | Comparison records the variance; approval blocks as `CONFLICTING_EVIDENCE` for `FINANCE_APPROVER` |
| Lost response after payment executes | `RESPONSE_LOST` at node 6 | The effect applied; the engine probes, reconciles and does **not** pay twice |
| Approval never arrives | `DELAYED_APPROVAL` at node 4 | The decision window collapses; a late approval is refused with `DECISION_EXPIRED` and the run fails without executing the gated action |
| Partial ledger commit | `PARTIAL_COMMIT` at node 6 | Invariants fail; `COMPENSATE` restores the contract |

A scenario may set `pre_authorize: false` to withhold the sandbox pre-authorization, so
the run blocks on the decision it is meant to demonstrate.

## 3. Human bottleneck

The mismatch scenario raises a `CONFLICTING_EVIDENCE` blocker with four evidence rows:

- invoice total (validation `FAIL`),
- purchase order total (validation `PASS`),
- the variance,
- the hash-linked comparison event.

The required role is `FINANCE_APPROVER`. An `OPERATOR` approval is refused with
`ROLE_NOT_AUTHORIZED`; a finance approval re-checks the evidence hash, the resume
conditions and the node preconditions before resuming.

## 4. Recovery and regression

The PO-timeout run declares `RESUME` with a probe and `purchase_order.id` as its
idempotency key. Recovery restores the checkpoint, verifies the contract, resumes the
node and only reports `VERIFIED` when the run's invariants pass. The partial-commit run
declares `COMPENSATE`; its contract requires `ledger_count == 0`.

A regression test created from any failed run pins the version, the scenario snapshot,
the violated invariant and the expected behaviour. It can be reviewed, activated,
replayed against the pinned version, compared across published versions, and replayed
again from the console.

## 5. Learning

The controlled demo site at `/demo-app/` supports the invoice lookup, validation and
approval flow. The Learning Studio records it in-page, ranks accessible locators,
surfaces ambiguities for human review, validates selectors with Playwright and saves a
version. The console does not claim a learned version exists unless a capture row and
version row do.

## 6. Console

`/slice` shows seven stages — learn, normal execution, chaos, decision, recovery,
regression, audit — plus the required-scenario checklist, the published node graph and
recent runs. Every stage is derived from API records:

- a scenario that has not been run shows **“Not run yet”**;
- the normal-execution stage requires a `VERIFIED` run;
- the recovery stage shows the persisted attempt status and contract-check count;
- the audit stage shows recomputed chain counts.

There are no hardcoded counters and no scripted success state.

## 7. Verification

| Check | What it proves |
| --- | --- |
| `backend/tests/acceptance/test_invoice_vertical_slice.py` | 21 named steps: model, normal run, timeline, final state, PO timeout + recovery, mismatch + role refusal + approval, lost response, missing approval, partial commit + compensation, regression capture/replay, chain integrity. Prints a pass/fail table and writes `artifacts/invoice-slice-step-report.json`. |
| `frontend/e2e/invoice-slice.spec.ts` | Six UI steps against a real backend: live slice state, chaos evaluation, decision evidence, finance approval, regression creation + recovery, regression replay. |
| `frontend/src/pages/InvoiceSlicePage.test.tsx` | The page renders live values and shows absence honestly when nothing has run. |

## 8. Driving it through Alan

The MCP server and CLI in `flowstate/alan/` expose the same backend. A session can ask
for the workflow, preview and confirm a chaos run, inspect the blocker, and request
recovery. Mutations return `CONFIRMATION_REQUIRED` first and only apply when repeated
with the token after explicit approval. The adapter cannot approve decisions.

See [ALAN_INTEGRATION.md](ALAN_INTEGRATION.md) for the tool surface and
`scripts/demo_alan_slice.sh` for the exact commands used in the demonstration.