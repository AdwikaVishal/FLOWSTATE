# FLOWSTATE AI — Verified Recovery and the Learning Feedback Loop

| Field | Value |
| --- | --- |
| Status | Implemented |
| Related | [STATE_MACHINES.md](STATE_MACHINES.md), [DECISION_ENGINE.md](DECISION_ENGINE.md), [SECURITY.md](SECURITY.md) |

Two promises are enforced here: **a recovery is only real when it is verified**, and
**a failure becomes a test that proves whether later versions still fail**.

## 1. Recovery is opt-in per action

An action is recoverable only when it declares an explicit recovery policy:

```json
{
  "strategy": "COMPENSATE",
  "reversible": true,
  "allowed_strategies": ["COMPENSATE", "RESUME"],
  "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
  "idempotency_path": "invoice.payment_reference",
  "contract": [
    {"path": "invoice.status", "op": "in", "value": ["SCHEDULED", "PAID"]},
    {"path": "invoice.ledger_count", "op": "lte", "value": 1}
  ],
  "compensate_contract": [
    {"path": "invoice.ledger_count", "op": "eq", "value": 0}
  ],
  "compensate": [
    {"op": "remove_last", "path": "invoice.ledger"},
    {"op": "decrement", "path": "invoice.ledger_count", "value": 1},
    {"op": "set", "path": "invoice.status", "value": "SCHEDULED"}
  ],
  "max_attempts": 1
}
```

If there is no policy, `GET /runs/{id}/recovery-plan` reports `NO_RECOVERY_POLICY` and a
recovery attempt is recorded as `MANUAL_INTERVENTION`. The engine never guesses an
inverse.

An irreversible action (`reversible: false`) must declare `strategy: "MANUAL"`. A policy
that claims an automated inverse for an irreversible operation is rejected at publish
time. Email and real payment operations are the canonical examples: there is no tested
undo, so a human must decide.

## 2. Strategy semantics

| Strategy | What it does | Run outcome on success |
| --- | --- | --- |
| `RESUME` | Restore a verified checkpoint and continue from the failed node | Run resumes; only `VERIFIED` if invariants pass |
| `COMPENSATE` | Restore the post-action state and apply the declared inverses | Run stays `FAILED`; the attempt is `VERIFIED` when the contract holds |
| `REPLAY` | Re-execute every node from the start in the sandbox, including earlier gated actions | Run resumes from ordinal 0 |
| `MANUAL` | No automated inverse | `MANUAL_INTERVENTION`; the run stays blocked |

`COMPENSATE` deliberately does not resume dependent actions. Undoing a financial
mutation and then continuing to spend would be worse than stopping.

## 3. Checkpoints and before-state capture

| Checkpoint | When | Purpose |
| --- | --- | --- |
| `PRE_ACTION` | Before an action, and again on an ambiguous outcome | Safe resume point |
| `POST_ACTION` | After every successful action | Source state for compensation |
| `ATTEMPT_STATE` | After an action whose outcome is `UNKNOWN` | Inspect what the attempt actually did |
| `SAFE_POINT` | At an explicit checkpoint node | Operator-declared restore point |

Every checkpoint stores a SHA-256 of its canonical state. A restore whose hash does not
match is rejected with `STALE_CHECKPOINT`; it is never applied. The attempt records
`before_state_hash` and `after_state_hash` so "restored the original state" is a
comparable claim rather than a narrative.

## 4. Ambiguous outcomes

A timeout, lost response or 5xx after the request was sent is `UNKNOWN`, never success.
Recovery resolves it in one of two ways:

1. **Inspect the target state.** The `ATTEMPT_STATE` checkpoint is probed with the
   declared `probe`. If the effect is present, the action is *not* executed again; the
   run resumes after it.
2. **Use an idempotency mechanism.** If the effect is absent and the policy declares an
   `idempotency_path` (for example `invoice.payment_reference`), the retry is recorded as
   idempotent and may proceed. Without a probe result and without an idempotency key, the
   attempt fails with `NO_IDEMPOTENCY_MECHANISM` and the action is not retried.

The console shows which path was taken: `effect present — not retried` or
`effect absent — idempotent retry`.

## 5. Verification

A recovery is `VERIFIED` only when **all** of the following hold:

1. The checkpoint hash matches.
2. The recovery contract holds on the restored state (`compensate_contract` after
   compensation, `contract` before a resume).
3. The resumed node's declared preconditions hold.
4. The resumed run passes its invariants.

The attempt records a structured `verification` object with every check, plus a
content-addressed evidence artefact (`recovery_report`) that stores both state hashes, the
contract, the probe, the steps and the relevant state. The artefact is downloadable
through the hash-checked evidence route.

Outcomes are honest:

| Status | Meaning |
| --- | --- |
| `VERIFIED` | Contract and verification passed |
| `PARTIAL` | Some inverses applied, but the contract does not hold; nothing resumed |
| `FAILED` | No progress, a stale checkpoint, or verification failed |
| `MANUAL_INTERVENTION` | No policy, an irreversible action, or an explicit manual strategy |
| `ESCALATED` | No usable checkpoint or version |

A verified recovery is terminal for that run: a second attempt is refused with
`ALREADY_RECOVERED`, because compensating twice would apply the inverse twice.

## 6. Regression tests

A test is generated from a failed run as a structured template, not a summary:

```json
{
  "template_version": 1,
  "workflow_id": "...", "workflow_name": "Vendor Payout",
  "workflow_version_id": "...", "version": 1,
  "source_run_id": "...", "seed": 70, "driver": "simulated-v1",
  "scenario": {"id": "...", "fault_type": "PARTIAL_COMMIT", "target_ordinal": 3, "seed": 70},
  "expected": {"kind": "INVARIANTS_SATISFIED", "invariant_name": "Invoice reaches PAID",
               "description": "No VIOLATED verdicts on replay."},
  "observed": {"failure_code": "INVARIANT_VIOLATED", "violated_invariants": [ ... ]},
  "reproduction": {"replay": "...", "compare": "...", "trigger": "REGRESSION"}
}
```

The scenario is snapshotted, so editing the live scenario later cannot silently change
what the test replays. A test starts as `DRAFT`; a human can rename it, edit the expected
behaviour, add notes and activate it. Activation is a separate action.

| Endpoint | Purpose |
| --- | --- |
| `POST /regression-tests` | Generate a `DRAFT` from a failed run |
| `PATCH /regression-tests/{id}` | Review and save name, notes, expected behaviour |
| `POST /regression-tests/{id}/activate` | Make it an active regression test |
| `POST /regression-tests/{id}/replay` | Run against the pinned version (or `?version_id=`) |
| `POST /regression-tests/{id}/compare` | Run against every published version |
| `GET /regression-tests/{id}/results` | Stored per-version results |

Every replay stores a `RegressionResult` with the version, run id, verdict, failing
invariant and detail.

## 7. The feedback loop

`GET /feedback/failure-modes` is read-only. For each regression test it reports:

- the failure mode (the violated invariant or failure code),
- the version where it was detected,
- the current published version,
- which versions reproduce it and which resolve it,
- `RESOLVED` / `STILL_FAILING` / `UNTESTED`,
- the source run and reviewer notes.

Nothing in this path writes to a workflow. Fixing a workflow is a human publishing a new
version. The console displays the result; it does not rewrite production definitions based
on an interpretation.

## 8. Demonstration

The seeded **Vendor Payout** workspace carries the whole loop:

| Version | Behaviour |
| --- | --- |
| v1 | Broken configuration: disbursement writes the ledger entry but never marks the invoice `PAID` |
| v2 | Fixed: the same action also sets `PAID` |

The seeded run on v1 fails `Invoice reaches PAID`. Its recovery policy compensates the
unsafe financial mutation, restores `SCHEDULED` with an empty ledger, and verifies the
contract. The regression test captured from that run reproduces on v1 and passes on v2;
the failure mode therefore shows as `RESOLVED in v2`.

A second run blocks on the irreversible “Notify the vendor” email. Recovery reports
`MANUAL_INTERVENTION` and the run stays `AWAITING_RECOVERY`.

## 9. Tests

`backend/tests/integration/test_recovery_regression.py` covers successful restoration,
stale checkpoints, partial compensation, manual intervention, ambiguous probing without a
retry, missing idempotency, no-policy refusal, regression reproduction across versions,
review/activation, and the read-only feedback loop.