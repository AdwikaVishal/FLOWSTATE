# FLOWSTATE AI — Chaos Lab Test Report

| Field | Value |
| --- | --- |
| Date | 2026-10-26 |
| Scope | Prompt 5: Chaos Lab, fault injection, invariants, reporting, recovery, regression |
| Environment | macOS, Python 3.15, Node 24, SQLite, Chromium via Playwright |
| Backend suite | 135 passed, 4 browser tests deselected (`pytest backend/tests -q`) |
| Browser suite | 4 passed (`pytest backend/tests -m browser -q`) |
| Frontend unit | 17 passed (`npm --prefix frontend run test`) |
| Console journeys | 3 passed (`npm --prefix frontend run test:e2e`) |
| Static gates | `ruff check`, `ruff format --check`, `mypy` all pass |

Commands run from the repository root:

```bash
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
PYTHONPATH=backend .venv/bin/pytest backend/tests -m browser -q
npm --prefix frontend run test
npm --prefix frontend run test:e2e
make verify-all
```

No benchmark numbers are included. Every row below was produced by evaluating a seeded
or API-created scenario through the real backend; the matrix script only formats persisted
report fields.

## 1. Fault matrix

The matrix evaluates an authorized baseline and the fault-injected run with the same
workflow version, seed and inputs. `Reconciled` counts actions whose ambiguous outcome was
resolved by a read-only probe.

| Fault | Baseline | Fault run | Failure code | First violated invariant | Reconciled | Evaluation | Safe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `TIMEOUT` | SUCCEEDED | SUCCEEDED/VERIFIED | — | — | 1 | NO_FAULT_EFFECT | True |
| `TIMEOUT_BEFORE_EXECUTION` | SUCCEEDED | FAILED/FAILED | TIMEOUT_BEFORE_EXECUTION | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |
| `NETWORK_DROP` | SUCCEEDED | AWAITING_RECOVERY/PENDING | ACTION_OUTCOME_UNKNOWN | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |
| `RATE_LIMITED` | SUCCEEDED | FAILED/FAILED | RATE_LIMITED | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |
| `TRANSIENT_ERROR` | SUCCEEDED | FAILED/FAILED | TRANSIENT_ERROR | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |
| `RESPONSE_LOST` | SUCCEEDED | SUCCEEDED/VERIFIED | — | — | 1 | NO_FAULT_EFFECT | True |
| `HTTP_500` | SUCCEEDED | SUCCEEDED/VERIFIED | — | — | 1 | NO_FAULT_EFFECT | True |
| `MALFORMED_RESPONSE` | SUCCEEDED | SUCCEEDED/VERIFIED | — | — | 1 | NO_FAULT_EFFECT | True |
| `STALE_READ` | SUCCEEDED | AWAITING_RECOVERY/PENDING | ACTION_OUTCOME_UNKNOWN | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |
| `MISSING_FIELD` | SUCCEEDED | FAILED/FAILED | MISSING_REQUIRED_FIELD | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |
| `CONTRADICTORY_TOTALS` | SUCCEEDED | FAILED/FAILED | INVARIANT_VIOLATED | Financial status requires matching totals | 0 | FAULT_DETECTED | True |
| `POSTCONDITION_FAILURE` | SUCCEEDED | FAILED/FAILED | POSTCONDITION_FAILED | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |
| `DUPLICATE_RESPONSE` | SUCCEEDED | FAILED/FAILED | INVARIANT_VIOLATED | Exactly one ledger entry | 0 | FAULT_DETECTED | True |
| `PARTIAL_COMMIT` | SUCCEEDED | FAILED/FAILED | INVARIANT_VIOLATED | Invoice reaches PAID | 0 | FAULT_DETECTED | True |
| `LATENCY` | SUCCEEDED | SUCCEEDED/VERIFIED | — | — | 0 | NO_FAULT_EFFECT | True |
| `DELAYED_APPROVAL` | SUCCEEDED | AWAITING_DECISION/PENDING | — | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |
| `SELECTOR_DRIFT` | SUCCEEDED | FAILED/FAILED | SELECTOR_NOT_FOUND | — | 0 | SAFE_FAILURE_WITHOUT_VIOLATION | True |

Interpretation:

- `TIMEOUT`, `RESPONSE_LOST`, `HTTP_500` and `MALFORMED_RESPONSE` applied the effect but
  lost or corrupted the response. All four were marked `UNKNOWN`, reconciled by a
  read-only probe, and only then verified. None was treated as success from the response
  alone.
- `NETWORK_DROP` and `STALE_READ` could not be confirmed by a probe and stayed
  `AWAITING_RECOVERY`; recovery was required before success.
- `DUPLICATE_RESPONSE`, `PARTIAL_COMMIT` and `CONTRADICTORY_TOTALS` completed the action
  but were caught by invariant verdicts.
- `MISSING_FIELD` and `POSTCONDITION_FAILURE` failed at the action boundary with a
  classified error.
- `LATENCY` legitimately had no correctness effect; the report says so instead of claiming
  the fault was “handled”.
- `DELAYED_APPROVAL` paused the run safely. With a clock advanced past the TTL, approval
  was rejected with `DECISION_EXPIRED` and the run failed.
- `SELECTOR_DRIFT` failed on a browser action in both the deterministic fake driver and a
  real Chromium run.

## 2. Test coverage map

| Requirement | Test |
| --- | --- |
| Every fault type produces a deterministic outcome | `unit/test_faults.py`, `integration/test_chaos_report.py::test_every_invoice_fault_has_a_reported_outcome` |
| Baseline + fault report and first violation | `test_evaluate_records_baseline_fault_and_first_violated_invariant` |
| Reproducibility | `test_evaluation_is_reproducible_from_recorded_configuration` |
| Unknown outcome is safe | `test_unknown_outcome_is_safe_and_recovery_updates_the_report` |
| Recovery updated in the report | same test after `POST /runs/{id}/recover` |
| Recovery precondition guard | `test_recovery_refuses_false_resume_preconditions` |
| Delayed approval expiry | `test_delayed_approval_expires_and_fails_the_run` |
| Changed browser selector | `test_selector_drift_on_a_browser_workflow` and `test_real_playwright_chaos_selector_drift_is_detected` |
| Save reproducible regression | `test_report_saves_a_reproducible_regression_test` |
| Totals policy / authorization invariants | `unit/test_invariants.py`, `test_totals_policy_and_authorization_invariants_catch_faults` |
| Checkpoint invariant | `unit/test_invariants.py::test_checkpoint_without_true_preconditions_is_violated` |
| Console shows the actual report | `frontend/e2e/console.spec.ts` Chaos Lab journey |

## 3. Observed recovery and regression evidence

- `NETWORK_DROP`: report showed `AWAITING_RECOVERY`, `failed_safely=True`, recovery not
  attempted. After `POST /runs/{id}/recover`, the recomputed report showed
  `recovery.attempted=True`, `recovery.verified=True` and the fault run `SUCCEEDED/VERIFIED`.
- False resume precondition: the restored checkpoint had `gate.open=False`; recovery
  stopped at `check_preconditions`, emitted `RECOVERY_FAILED`, produced no
  `RECOVERY_APPLIED` event, and the run did not become verified.
- `DUPLICATE_RESPONSE`: saving a regression test pinned the scenario, version, seed and
  `Exactly one ledger entry` invariant. Replay remained `FAILING`, which is the correct
  result while the invariant is still violated.

## 4. Known gaps

- Browser-channel chaos supports `SELECTOR_DRIFT` only. Other faults are rejected at
  scenario creation rather than silently ignored.
- Delayed approval uses an injected clock to model a late human response; it does not
  sleep in production execution.
- Reports are computed on demand from stored runs; there is no cross-version benchmark
  aggregate or historical trend chart.
- Chaos runs pre-authorize gated actions except the delayed-approval target. This is
  documented in [CHAOS.md](CHAOS.md) and is not an approval bypass.