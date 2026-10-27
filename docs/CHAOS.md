# FLOWSTATE AI — Chaos Lab and Invariant Testing

| Field | Value |
| --- | --- |
| Status | Implemented |
| Scope | In-process sandbox and the controlled `/demo-app` browser target |
| Related | [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md), [LEARNING.md](LEARNING.md), [TEST_STRATEGY.md](TEST_STRATEGY.md) |

The Chaos Lab proves how a workflow **fails**, not only that it runs. Every evaluation
executes an authorized baseline and a deterministic fault-injected run against the same
workflow version, seed and input fixtures. The report is derived from persisted verdicts,
events, recovery attempts and regression records. A scenario is never reported as passed
without a satisfied assertion.

## 1. Safety boundary

- Faults are injected at the execution boundary only. No fault reaches a real third-party
  service.
- Engine workflows use the deterministic `simulated-v1` sandbox.
- Learned browser workflows use `playwright-v1` against the controlled `/demo-app` only.
- Real payment hosts stay on the unconditional deny-list in `ActionPolicy`.
- Chaos runs pre-authorize gated actions **except** the action targeted by a
  `DELAYED_APPROVAL` fault. That exception is deliberate: the approval must remain a real
  human decision so its TTL can expire.
- The report labels the driver, so a simulated result can never be mistaken for a
  Chromium run.

## 2. Fault catalogue

| Fault | Boundary behaviour | Expected handling |
| --- | --- | --- |
| `TIMEOUT_BEFORE_EXECUTION` | Times out before the operation; no effect | Classified `TIMEOUT`; retry only when the side-effect class allows |
| `NETWORK_DROP` | Request never reaches the operation | `UNKNOWN`; probe required; recoverable, never success |
| `RATE_LIMITED` | Rejects before the effect | Classified `RATE_LIMITED`; irreversible actions are not retried |
| `TRANSIENT_ERROR` | Fails before the effect for `failures` attempts | Retry budget may succeed |
| `TIMEOUT` | Applies the effect; no response | `UNKNOWN`; probe reconciles |
| `RESPONSE_LOST` | Applies the effect; response lost | `UNKNOWN`; probe reconciles |
| `HTTP_500` | Applies the effect; returns 5xx | `UNKNOWN`; probe reconciles |
| `MALFORMED_RESPONSE` | Applies the effect; returns an untrusted response | `UNKNOWN`; probe reconciles |
| `STALE_READ` | Applies the effect; probe reads a stale replica | `UNKNOWN`; probe cannot confirm; blocked |
| `MISSING_FIELD` | Applies the effect but omits a required field | Action fails `MISSING_REQUIRED_FIELD` |
| `CONTRADICTORY_TOTALS` | Applies the effect but changes the PO total | Policy/compare invariant catches it |
| `POSTCONDITION_FAILURE` | Acknowledges success but corrupts the postcondition state | Action fails `POSTCONDITION_FAILED` |
| `DUPLICATE_RESPONSE` | Applies the effect `duplicates` times | Duplicate-effect history/state invariant catches it |
| `PARTIAL_COMMIT` | Applies half the effect, then acknowledges | Terminal/cross-action invariant catches it |
| `LATENCY` | Applies the effect with added latency | May remain verified; timing is not a correctness failure |
| `DELAYED_APPROVAL` | Human approval arrives after the decision TTL | Approval rejected `DECISION_EXPIRED`; run fails safely |
| `SELECTOR_DRIFT` | Reloads the demo with changed ids/labels/names | `SELECTOR_NOT_FOUND`; later nodes never pass |

Fault parameters are recorded on the scenario and repeated in the report. A repeat
evaluation with the same version, seed and parameters produces the same injection point
and the same assertions.

## 3. Invariants

The evaluator is declarative; no generated code is executed.

| Kind | Expression shape | Use |
| --- | --- | --- |
| `state` | `{path, op, value}` | Terminal state, ledger count, validation state |
| `history` | `{event_type, action_ordinal, op, value}` | Duplicate/absent action success events |
| `compare` | `{left, op, right_path}` | Invoice total versus purchase-order total |
| `implies` / `policy` | `{if, then}` | “If financial status is reached, totals must match” |
| `authorization` | `{kind: authorization}` | Every gated success must have a prior grant event |
| `postcondition` | `{action_ordinal, conditions}` | A completed action must satisfy its declared postcondition |
| `checkpoint` | `{kind: checkpoint}` | Recovery may not apply a checkpoint with false preconditions |
| unknown kind | — | `INCONCLUSIVE`, which never becomes a pass |

The demo workflow ships seven invariants, including totals policy, authorization,
completed-postcondition and checkpoint-precondition rules. `VIOLATED` dominates
aggregation; `INCONCLUSIVE` is never promoted to `SATISFIED`.

## 4. Evaluation and report

`POST /api/v1/scenarios/{id}/evaluate` runs:

1. an authorized **baseline** with the scenario's seed and input fixtures but no fault;
2. the **fault-injected run** at the declared ordinal;
3. a report derived from both persisted runs.

The report contains:

- baseline and fault status, verdict, driver, duration and invariant verdicts;
- the first violated invariant in the version's declared order;
- the failure action, error class/code/message, output and screenshot evidence;
- `failed_safely`, computed from status + verdict + violation consistency;
- recovery attempted/verified and its latest status;
- the regression test id when one exists;
- the exact seed, parameters and input fixtures used;
- an evaluation label (`FAULT_DETECTED`, `NO_FAULT_EFFECT`,
  `SAFE_FAILURE_WITHOUT_VIOLATION`, `BASELINE_NOT_VERIFIED`, `INCONCLUSIVE`).

`GET /api/v1/scenarios/{id}/report` recomputes the same report from the stored baseline
and fault run ids, so recovery or later evidence changes are reflected without fabricating
a new history.

## 5. Reproducible regression tests

The report's **Save reproducible regression test** action creates a `RegressionTest`
pinned to the workflow version, fault scenario, seed and first violated invariant. Replay
re-runs the same scenario and is marked `FAILING` while the invariant is still violated;
it becomes `PASSING` only when the workflow verifies with the same injection.

## 6. API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET/POST` | `/api/v1/scenarios` | List / declare a scenario |
| `GET` | `/api/v1/scenarios/{id}` | Configuration and last run ids |
| `POST` | `/api/v1/scenarios/{id}/run` | Fault run only (legacy/shortcut) |
| `POST` | `/api/v1/scenarios/{id}/evaluate` | Authorized baseline + fault run + report |
| `GET` | `/api/v1/scenarios/{id}/report` | Recomputed report from stored runs |
| `POST` | `/api/v1/runs/{id}/recover` | Checkpoint recovery with a precondition guard |
| `POST` | `/api/v1/regression-tests` | Save the failing run as a regression test |

## 7. Known limitations

- Faults cover the in-process sandbox completely; the browser channel currently supports
  only `SELECTOR_DRIFT`. Other browser faults are rejected with
  `FAULT_UNSUPPORTED_FOR_BROWSER`.
- `DELAYED_APPROVAL` models a human decision that arrives after the TTL. It does not
  pause wall-clock execution; tests advance an injected clock.
- Chaos runs pre-authorize gated actions except the delayed-approval target. This is a
  deliberate convenience for exploring downstream faults, not a claim that approvals are
  optional.
- Reports are computed on demand from stored runs; there is no long-term benchmark
  aggregation or cross-version trend chart yet.
- Browser-backed chaos runs cannot resume mid-page; recovery remains a sandbox feature.