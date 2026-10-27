# FLOWSTATE AI — Acceptance Criteria

| Field | Value |
| --- | --- |
| Status | Ready to execute |
| Related | [PRD.md](PRD.md), [TEST_STRATEGY.md](TEST_STRATEGY.md), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) |

Each criterion is testable and names the test that proves it. "Test" paths are relative to
`backend/tests/`. A criterion is only met when its test is green in `make verify` (or
`make verify-all` where marked **[browser]**).

---

## A. Workflow learning

**AC-L1 — Capture produces ordered candidate actions**
*Given* a recorded session of N interactions against the demo app,
*when* the capture is finalized,
*then* exactly N `ActionDefinition` candidates exist with contiguous ordinals and a
ranked selector list each.
`unit/test_learning_service.py::test_capture_yields_ordered_candidates`

**AC-L2 — Selector ranking prefers accessible locators**
*Given* an element with a role, accessible name and a test id,
*when* selectors are ranked,
*then* role+name is preferred over test id, which is preferred over CSS.
`unit/test_selector_heuristics.py::test_ranking_order`

**AC-L3 — Unresolvable selector blocks publish**
*Given* a draft version containing one selector that does not resolve on the live page,
*when* publish is requested,
*then* the request is rejected with the offending ordinal and the version stays `DRAFT`.
`integration/test_api_workflows.py::test_publish_rejects_unresolved_selector` **[browser]**

**AC-L4 — Published versions are immutable**
*Given* a `PUBLISHED` version,
*when* an edit is attempted,
*then* the edit is rejected and a new `DRAFT` fork is offered instead.
`integration/test_api_workflows.py::test_published_version_immutable`

**AC-L5 — Version content is hashed**
*Given* a published version,
*when* its actions or invariants change by any means,
*then* the recomputed `content_hash` differs and the repository refuses the write.
`unit/test_workflow_service.py::test_content_hash_detects_change`

---

## B. Workflow execution

**AC-E1 — Only published versions execute**
*Given* a `DRAFT` version,
*when* a run is requested,
*then* the request fails with `VERSION_NOT_PUBLISHED` and no run is created.
`integration/test_api_runs.py::test_cannot_run_draft_version`

**AC-E2 — Preconditions are enforced**
*Given* an action whose precondition is false,
*when* the engine reaches it,
*then* the run ends `FAILED` with `PRECONDITION_FAILED` and the action is never executed.
`integration/test_engine_preconditions.py::test_precondition_failure_stops_run`

**AC-E3 — Success requires a passed postcondition**
*Given* an action whose postcondition does not hold after execution,
*when* the engine evaluates it,
*then* the action is `FAILED` and the run does not report success.
`integration/test_engine_postconditions.py::test_postcondition_failure_fails_action`

**AC-E4 — Ambiguous outcome is `UNKNOWN`, never success**
*Given* an action whose response is lost after the request was sent,
*when* the engine cannot observe the effect,
*then* the action enters `UNKNOWN` and the run cannot reach `SUCCEEDED` until a probe
resolves it.
`integration/test_engine_unknown.py::test_lost_response_is_unknown`

**AC-E5 — Probe confirms or refutes**
*Given* an `UNKNOWN` action,
*when* a read-only probe finds the effect,
*then* the action becomes `CONFIRMED` and the run may continue;
*when* the probe finds no effect, the action becomes `FAILED`.
`integration/test_engine_unknown.py::test_probe_resolution_paths`

**AC-E6 — Unavailable probe never succeeds**
*Given* an `UNKNOWN` action whose probe cannot run,
*when* the engine gives up,
*then* the action is `FAILED` and the run is not `SUCCEEDED`.
`integration/test_engine_unknown.py::test_probe_unavailable_fails`

**AC-E7 — Terminal success requires verification**
*Given* a run in `VERIFYING`,
*when* any attached invariant is `VIOLATED` or `INCONCLUSIVE`,
*then* the run cannot become `SUCCEEDED`.
`unit/test_statemachine_run.py::test_verify_requires_satisfied_invariants`

**AC-E8 — Every transition is audited**
*Given* any accepted transition,
*when* the unit of work commits,
*then* exactly one `ExecutionEvent` exists for it and the hash chain verifies.
`unit/test_audit_chain.py::test_every_transition_emits_event`

**AC-E9 — Audit chain is tamper-evident**
*Given* a stored run,
*when* one event payload is mutated,
*then* verification fails at that event's `seq`.
`unit/test_audit_chain.py::test_tampering_detected_at_seq`

**AC-E10 — Terminal states are absorbing**
*Given* a run in `SUCCEEDED`, `FAILED`, `CANCELLED` or `TIMED_OUT`,
*when* any transition is attempted,
*then* it is rejected.
`unit/test_statemachine_run.py::test_terminal_states_absorbing`

**AC-E11 — Run budget is enforced**
*Given* a run whose deadline has passed on the injected clock,
*when* the engine requests the next action,
*then* the run becomes `TIMED_OUT`.
`integration/test_engine_budget.py::test_deadline_times_out`

---

## C. Chaos testing

**AC-C1 — Scenarios are reproducible**
*Given* the same `FaultScenario`, seed and workflow version,
*when* it is run three times,
*then* the injected fault fires at the same action and the verdicts are identical.
`integration/test_chaos_reproducibility.py::test_same_seed_same_result`

**AC-C2 — Faults are injected at the boundary**
*Given* an armed `TIMEOUT` scenario,
*when* the target action executes,
*then* the injector reports `fault_applied` before the run continues.
`integration/test_chaos_injection.py::test_fault_applied_before_observe`

**AC-C3 — Invariant violation is detected**
*Given* a `PARTIAL_COMMIT` scenario on a two-step action pair,
*when* the scenario completes,
*then* the cross-action invariant is `VIOLATED` with a recorded evidence bundle.
`integration/test_chaos_invariants.py::test_partial_commit_violates_cross_action`

**AC-C4 — `INCONCLUSIVE` is never a pass**
*Given* a scenario whose evidence capture failed,
*when* results are aggregated,
*then* the scenario result is `INCONCLUSIVE` and the run is not reported as passing.
`unit/test_chaos_service.py::test_inconclusive_not_pass`

**AC-C5 — Scenarios cannot target real origins**
*Given* a scenario whose target resolves outside the allow-list,
*when* it is validated,
*then* it cannot become `READY`.
`unit/test_policy.py::test_scenario_origin_denied`

---

## D. Human decisions

**AC-D1 — Authorization requirement pauses the run**
*Given* an action with `requires_authorization = true`,
*when* the engine reaches it,
*then* the run becomes `AWAITING_DECISION` and a `HumanDecision` is created.
`integration/test_decisions.py::test_authorization_pauses_run`

**AC-D2 — Minimum unresolved decision is chosen**
*Given* three blocked steps where only the first is actionable,
*when* the decision is computed,
*then* only the lowest-ordinal actionable step is requested.
`unit/test_decision_service.py::test_minimum_unresolved_decision`

**AC-D3 — Evidence bundle is complete and intact**
*Given* a decision request,
*when* it becomes `NOTIFIED`,
*then* every evidence file exists and matches its `sha256`.
`integration/test_decisions.py::test_evidence_bundle_integrity`

**AC-D4 — Approval issues a scoped token and resumes**
*Given* a notified decision,
*when* a human approves,
*then* a single-use token bound to `(run_id, ordinal)` is issued and the run resumes at
that ordinal, not from the start.
`integration/test_decisions.py::test_approval_resumes_at_decision_point`

**AC-D5 — Denial and expiry fail safely**
*Given* a notified decision,
*when* it is denied, or the injected clock passes `expires_at`,
*then* the run becomes `FAILED` and no action is executed.
`integration/test_decisions.py::test_deny_and_expiry`

**AC-D6 — Tokens are single-use**
*Given* a consumed capability token,
*when* it is presented again,
*then* it is rejected and the attempt is audited.
`unit/test_tokens.py::test_single_use`

**AC-D7 — Agents cannot approve**
*Given* a request authenticated as an agent identity,
*when* it calls the approve endpoint,
*then* it is rejected with `HUMAN_ACTOR_REQUIRED`.
`integration/test_api_auth.py::test_agent_cannot_approve`

---

## E. Recovery

**AC-R1 — Checkpoints are created at safe points**
*Given* a running workflow,
*when* an action completes,
*then* a `POST_ACTION` checkpoint exists with the correct `event_seq` and `action_ordinal`.
`integration/test_recovery.py::test_checkpoint_after_action`

**AC-R2 — Recovery requires verification**
*Given* a recovery attempt that applied successfully,
*when* verification invariants fail,
*then* the attempt is `FAILED`, not `VERIFIED`, and the run does not resume.
`integration/test_recovery.py::test_applied_without_verification_is_failure`

**AC-R3 — Verified recovery resumes the run**
*Given* a recoverable failure with a valid checkpoint,
*when* recovery applies and invariants pass,
*then* the attempt is `VERIFIED` and the run resumes at the next ordinal.
`integration/test_recovery.py::test_verified_recovery_resumes`

**AC-R4 — No usable checkpoint escalates**
*Given* a failure with no valid checkpoint,
*when* recovery is requested,
*then* the attempt is `ESCALATED` and a human is informed.
`integration/test_recovery.py::test_no_checkpoint_escalates`

**AC-R5 — Corrupt checkpoint is rejected**
*Given* a checkpoint whose `state_hash` does not match its content,
*when* it is loaded,
*then* the attempt is `ESCALATED` and the corrupt state is never applied.
`unit/test_recovery_service.py::test_hash_mismatch_escalates`

---

## F. Regression feedback

**AC-G1 — Failure generates a deterministic spec**
*Given* a confirmed failure,
*when* a regression test is generated,
*then* its spec pins the workflow version, seed and scenario, and excludes wall-clock time.
`unit/test_regression_service.py::test_spec_is_deterministic`

**AC-G2 — Replay is stable**
*Given* an active regression test,
*when* it is replayed three times,
*then* all three results are identical.
`integration/test_regression.py::test_replay_is_stable`

**AC-G3 — Regression detects recurrence**
*Given* an active regression test guarding invariant I,
*when* a change reintroduces the fault,
*then* replay fails and the test becomes `FAILING`.
`integration/test_regression.py::test_recurrence_detected`

---

## G. Security and correctness

**AC-S1 — Non-allow-listed origins are blocked**
*Given* any action or probe targeting a non-demo origin,
*when* it is attempted,
*then* policy denies it and the sandbox aborts the request.
`unit/test_policy.py::test_origin_denied` + `browser/test_sandbox_egress.py::test_external_request_blocked` **[browser]**

**AC-S2 — No real financial transactions**
*Given* any payment-shaped action,
*when* it executes,
*then* it targets the demo origin only, and a static scan finds no real payment host in
the repo.
`unit/test_policy.py::test_payment_routes_to_demo` + `unit/test_repo_hygiene.py::test_no_real_payment_hosts`

**AC-S3 — Secrets never reach logs**
*Given* a request carrying a token-shaped value,
*when* logs are emitted,
*then* the value is redacted.
`unit/test_logging_redaction.py::test_secret_redacted`

**AC-S4 — The LLM cannot mint authority**
*Given* any model output,
*when* it proposes an approval or a verdict,
*then* no token is issued and no run status changes.
`integration/test_llm_authority.py::test_model_output_cannot_approve`

**AC-S5 — Unauthenticated access is rejected**
*Given* no credentials,
*when* any `/api/v1` route except `/healthz` is called,
*then* the response is 401.
`integration/test_api_auth.py::test_requires_auth`

---

## H. Console and delivery

**AC-U1 — Runs are visible as a timeline**
*Given* a run with events,
*when* the run detail page loads,
*then* events render in `seq` order with actor, type and timestamp.
`frontend/tests/RunTimeline.test.tsx`

**AC-U2 — Status is not colour-only**
*Given* any status badge,
*when* rendered,
*then* it includes an icon and text label.
`frontend/tests/StatusBadge.test.tsx`

**AC-U3 — Decision inbox shows evidence**
*Given* an open decision,
*when* the inbox is opened,
*then* the question, options, policy citation and evidence thumbnails are visible and
approve/deny are reachable by keyboard.
`frontend/tests/DecisionInbox.test.tsx`

**AC-U4 — Responsive layout**
*Given* a 380 px viewport,
*when* each primary screen renders,
*then* no horizontal scrolling and all actions remain reachable.
`frontend/tests/responsive.test.tsx`

**AC-DEL1 — Clean-checkout bootstrap**
*Given* a fresh clone and the documented prerequisites,
*when* `make bootstrap && make verify` runs,
*then* both succeed.
Manual, recorded in the test report.

**AC-DEL2 — End-to-end demo**
*Given* a seeded environment,
*when* the acceptance suite runs,
*then* learn → execute → chaos → decide → recover → regress completes and the audit chain
verifies for every run.
`acceptance/test_end_to_end.py`

---

## I. Workflow learning implementation (Playwright)

These criteria cover the implemented controlled-demo learning path. They are additional to
the original learning criteria above.

**AC-L6 — Capture lifecycle is explicit**
*Given* a recording session,
*when* the user pauses, resumes and stops it,
*then* events are accepted only while `RECORDING`, `PAUSED` rejects them, and `STOP`
produces a reviewable session.
`backend/tests/integration/test_captures.py::test_capture_lifecycle_and_ordered_candidates`

**AC-L7 — Recorded events are ordered typed candidates**
*Given* N recorded demo interactions,
*when* they are ingested,
*then* N candidates exist with contiguous ordinals, ranked locators, page transitions and
a suggested input value.
`backend/tests/integration/test_captures.py::test_capture_lifecycle_and_ordered_candidates` +
`backend/tests/unit/test_learning.py::test_selector_ranking_prefers_accessible_locators`

**AC-L8 — Secrets never leave the demo page or the database**
*Given* a sensitive input,
*when* it is recorded,
*then* the value is absent from the API response and from persisted candidates, and the
step is marked for human review.
`backend/tests/integration/test_captures.py::test_sensitive_values_are_redacted_and_never_persisted`

**AC-L9 — Ambiguities block saving**
*Given* a candidate with only a CSS selector or no observed transition,
*when* the user saves,
*then* the request fails with `CAPTURE_AMBIGUITIES_UNRESOLVED` until a human accepts or
edits it.
`backend/tests/integration/test_captures.py::test_ambiguous_action_blocks_save_until_reviewed`

**AC-L10 — Unresolved selectors block publish**
*Given* a recorded step whose locator no longer resolves,
*when* validation runs and publish is requested,
*then* validation reports `SELECTOR_NOT_FOUND`, the step is `UNRESOLVED`, and publish is
rejected with `CAPTURE_NOT_VALIDATED`.
`backend/tests/integration/test_captures.py::test_missing_selector_blocks_validation_and_publish` +
`backend/tests/browser/test_playwright_capture.py::test_real_playwright_selector_drift_fails_safely`

**AC-L11 — Learned workflow replays with different permitted inputs**
*Given* a published learned workflow,
*when* it is replayed with two different valid invoice fixtures,
*then* both runs are `SUCCEEDED` and `VERIFIED`, with ordered events, node states and
screenshot evidence.
`backend/tests/integration/test_captures.py::test_record_validate_save_and_replay_with_different_inputs` +
`backend/tests/browser/test_playwright_capture.py::test_real_playwright_recording_can_be_validated_saved_and_replayed`

**AC-L12 — Replay uses real verification, not HTTP/click success**
*Given* a browser action that completes but whose expected page state never appears,
*when* the read-only probe cannot confirm it,
*then* the node is `BLOCKED`, the run is `AWAITING_RECOVERY`, and it is not success.
`backend/tests/integration/test_captures.py::test_timeout_after_action_is_unknown_not_success`

**AC-L13 — Structure drift fails safely**
*Given* a changed demo layout,
*when* a learned workflow is replayed,
*then* it fails with `SELECTOR_NOT_FOUND`, later nodes do not pass, and no success is
reported.
`backend/tests/integration/test_captures.py::test_selector_drift_fails_safely_and_is_not_success` +
`backend/tests/browser/test_playwright_capture.py::test_real_playwright_selector_drift_fails_safely`

**AC-L14 — Saving again creates a new version**
*Given* an already saved learned workflow,
*when* it is saved again,
*then* version 2 is created and becomes the current version while version 1 remains
immutable.
`backend/tests/integration/test_captures.py::test_saving_again_creates_a_new_version`

**AC-L15 — Browser replay is visibly connected to the timeline**
*Given* a replay run,
*when* the console loads the result,
*then* node statuses, driver, screenshot evidence and a link to the ordered execution
timeline are shown.
`frontend/e2e/learning.spec.ts` + `frontend/src/App.test.tsx` (Learning Studio)

**AC-L16 — The demo recorder emits structured, non-sensitive events**
*Given* a user typing, loading and validating in the demo,
*when* the recorder is active,
*then* it emits four ordered structured events with the correct page transitions and no
sensitive values.
`backend/tests/browser/test_playwright_capture.py::test_demo_app_records_structured_non_sensitive_events`

---

## J. Chaos Lab implementation (Prompt 5)

**AC-C6 — Every fault family is deterministic and classified**
*Given* each supported fault type,
*when* it is injected at the matching boundary,
*then* it produces the recorded outcome, error class/code and side-effect state.
`backend/tests/unit/test_faults.py` + `backend/tests/integration/test_chaos_report.py::test_every_invoice_fault_has_a_reported_outcome`

**AC-C7 — Baseline and fault-injected runs are both recorded**
*Given* an evaluated scenario,
*when* the report is loaded,
*then* it contains a baseline run and a fault run with the same version, seed and input fixtures.
`backend/tests/integration/test_chaos_report.py::test_evaluate_records_baseline_fault_and_first_violated_invariant`

**AC-C8 — The first violated invariant is ordered by declaration**
*Given* a fault run with multiple verdicts,
*when* the report is built,
*then* `first_violated_invariant` is the first `VIOLATED` verdict in the version's invariant order.
`backend/tests/integration/test_chaos_report.py::test_evaluate_records_baseline_fault_and_first_violated_invariant`

**AC-C9 — Failure action and evidence are attached**
*Given* a failed or blocked fault run,
*when* the report is built,
*then* the failure action, error code/message, node output and any screenshot evidence are included.
`backend/tests/integration/test_chaos_report.py::test_postcondition_failure_is_attributed_to_the_action`

**AC-C10 — Failed safely is derived, never assumed**
*Given* a fault run,
*when* `failed_safely` is computed,
*then* a run with a violated invariant may not be `SUCCEEDED`/`VERIFIED`, an unknown outcome is blocked, and `INCONCLUSIVE` is never a pass.
`backend/tests/integration/test_chaos_report.py::test_unknown_outcome_is_safe_and_recovery_updates_the_report` + `test_totals_policy_and_authorization_invariants_catch_faults`

**AC-C11 — Recovery outcome appears in the report**
*Given* a fault run that is awaiting recovery,
*when* recovery succeeds,
*then* the recomputed report shows recovery attempted and verified, and the fault run verified.
`backend/tests/integration/test_chaos_report.py::test_unknown_outcome_is_safe_and_recovery_updates_the_report`

**AC-C12 — Recovery refuses false resume preconditions**
*Given* a restored checkpoint whose resume preconditions are false,
*when* recovery is requested,
*then* the attempt fails at `check_preconditions`, no `RECOVERY_APPLIED` event exists, and the run never reports success.
`backend/tests/integration/test_chaos_report.py::test_recovery_refuses_false_resume_preconditions`

**AC-C13 — Delayed approval expires safely**
*Given* a delayed-approval fault and a clock advanced beyond the TTL,
*when* a human approves,
*then* the decision is `DECISION_EXPIRED` and the run fails safely.
`backend/tests/integration/test_chaos_report.py::test_delayed_approval_expires_and_fails_the_run`

**AC-C14 — Changed browser selectors fail safely**
*Given* a learned browser workflow and `SELECTOR_DRIFT`,
*when* the baseline and fault runs are evaluated,
*then* the baseline verifies and the fault run fails with `SELECTOR_NOT_FOUND`.
`backend/tests/integration/test_chaos_report.py::test_selector_drift_on_a_browser_workflow` + `backend/tests/browser/test_playwright_capture.py::test_real_playwright_chaos_selector_drift_is_detected`

**AC-C15 — A failing scenario can be saved as a regression test**
*Given* a report with a fault failure,
*when* the operator saves a regression test,
*then* the report exposes its id and replay is `FAILING` while the invariant remains violated.
`backend/tests/integration/test_chaos_report.py::test_report_saves_a_reproducible_regression_test`

**AC-C16 — Chaos Lab shows the actual backend report**
*Given* the console,
*when* the operator evaluates a scenario,
*then* baseline/fault statuses, the first violation, failed-safely, evidence and the regression action are visible, and the fault timeline is linked.
`frontend/e2e/console.spec.ts`

---

## K. Alan integration (Prompt 8)

**AC-A1 — The integration mechanism is verified, not assumed**
*Given* the Alan environment,
*when* the integration is designed,
*then* it uses the documented custom MCP connector + team Skill surfaces, and the
UI-only connector-registration limitation is stated explicitly.
`docs/ALAN_INTEGRATION.md`

**AC-A2 — Natural language resolves to a typed workflow**
*Given* a workflow name, slug, id or phrase,
*when* the user inspects it,
*then* the adapter returns the version, declared inputs, actions, channels and invariants;
ambiguous or missing workflows return candidates instead of guessing.
`backend/tests/integration/test_alan_adapter.py::test_list_and_inspect_workflows` +
`test_ambiguous_and_unknown_workflow`

**AC-A3 — Missing required inputs are requested, never invented**
*Given* a workflow with a required input,
*when* the user runs it without that input,
*then* the adapter returns `MISSING_INPUTS` with the schema and does not create a run.
`backend/tests/integration/test_alan_adapter.py::test_missing_declared_inputs_are_reported`

**AC-A4 — Consequential actions require explicit confirmation**
*Given* a run/chaos/recovery/regression request,
*when* it is called without the matching confirmation token,
*then* no mutation occurs and the exact summary plus token is returned.
`backend/tests/integration/test_alan_adapter.py::test_run_requires_confirmation_and_never_claims_success`

**AC-A5 — Success is only claimed from a verified backend result**
*Given* a run in `AWAITING_DECISION`, `AWAITING_RECOVERY` or `FAILED`,
*when* the adapter reports status,
*then* `is_success` is false and `explain_failure` does not claim success.
`backend/tests/integration/test_alan_adapter.py::test_run_requires_confirmation_and_never_claims_success` +
`test_chaos_report_explanation_and_verified_recovery`

**AC-A6 — Status, blockers, evidence and recovery come from persisted data**
*Given* a paused or failed run,
*when* the user asks why,
*then* the adapter returns the persisted verdicts, failed nodes, blockers, evidence hashes
and recovery plan.
`backend/tests/integration/test_alan_adapter.py::test_inspect_evidence_verifies_persisted_hash` +
`test_chaos_report_explanation_and_verified_recovery`

**AC-A7 — Simulated and browser execution are distinguishable**
*Given* a run response,
*when* it is presented,
*then* `driver` and `execution_kind` identify `simulated-v1` versus `playwright-v1`.
`backend/tests/integration/test_alan_adapter.py::test_list_and_inspect_workflows`

**AC-A8 — Duplicate retries are idempotent**
*Given* a mutation with an `Idempotency-Key`,
*when* the same request is retried,
*then* the original run/report is returned; a different payload with the same key is
rejected with `IDEMPOTENCY_CONFLICT`.
`backend/tests/integration/test_idempotency.py` +
`test_alan_adapter.py::test_idempotent_run_returns_the_same_run`

**AC-A9 — Authentication and transport failures are handled**
*Given* a wrong token, timeout or malformed response,
*when* the adapter calls the backend,
*then* it returns a typed failure without claiming success.
`backend/tests/integration/test_alan_adapter.py::test_auth_failure_and_transport_errors`

**AC-A10 — Least-privilege controls work**
*Given* read-only mode or a workflow allowlist,
*when* a disallowed mutation/workflow is requested,
*then* it is rejected without a backend write.
`backend/tests/integration/test_alan_adapter.py::test_read_only_and_workflow_allowlist`

**AC-A11 — The MCP boundary exposes exactly the tested operations**
*Given* the MCP server,
*when* its tools are listed and called,
*then* the eleven typed operations are present and the HTTP ingress enforces its bearer
token.
`backend/tests/integration/test_alan_adapter.py::test_mcp_server_exposes_typed_tools` +
`backend/tests/unit/test_alan_boundary.py::test_mcp_ingress_requires_the_configured_bearer_token`

**AC-A12 — The CLI fallback works**
*Given* no MCP connector,
*when* the CLI previews, confirms and runs an operation,
*then* it emits one JSON envelope and exit code 0/2/1 as documented.
`backend/tests/integration/test_alan_adapter.py::test_cli_preview_confirm_and_compact_output`

**AC-A13 — The demo completes through the adapter**
*Given* a running backend,
*when* `make alan-demo` runs,
*then* it lists/inspects, requests a run, explains the blocker, evaluates chaos, requests
verified recovery and replays a regression test.
`scripts/alan_demo.py` (manual command, documented)

---

## Traceability summary

| Capability | Criteria |
| --- | --- |
| Learning | AC-L1 … AC-L5, AC-L6 … AC-L16 |
| Execution | AC-E1 … AC-E11 |
| Chaos | AC-C1 … AC-C5, AC-C6 … AC-C16 |
| Human decisions | AC-D1 … AC-D7 |
| Recovery | AC-R1 … AC-R5 |
| Feedback | AC-G1 … AC-G3 |
| Security | AC-S1 … AC-S5 |
| Console / delivery | AC-U1 … AC-U4, AC-DEL1, AC-DEL2 |
| Browser learning | AC-L6 … AC-L16 |
| Chaos Lab | AC-C6 … AC-C16 |
| Alan integration | AC-A1 … AC-A13 |
