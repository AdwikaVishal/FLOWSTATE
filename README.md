# FLOWSTATE AI

FLOWSTATE is a **workflow reliability platform**. It executes structured workflows with
explicit preconditions and postconditions, injects controlled faults to find broken
invariants, escalates the minimum unresolved human decision with evidence, recovers
supported state, and turns failures into regression tests. Every transition lands in a
hash-linked audit chain.

This repository contains the working application foundation:

- **FastAPI backend** — versioned REST API, SQLite schema initialisation, repository/service
  separation, structured logging, problem+json errors, CORS for the console.
- **Live execution monitoring** — console-started background runs use a persisted bounded
  queue with in-process workers; each node's projection, checkpoint and audit events
  commit together, then an authenticated SSE stream delivers them with sequence-based
  reconnect.
- **Workflow learning** — a controlled invoice demo, a privacy-aware in-page recorder,
  ranked accessibility locators, human review of ambiguous steps, and Playwright replay
  that produces a normal audited execution run.
- **Human decision engine** — eight blocker causes, an evidence packet resolved from
  persisted state and the audit log, role-based authority, four decision actions and a
  resumption check that re-validates authority, evidence freshness and node preconditions.
- **Verified recovery** — explicit per-action recovery policies, compensating actions,
  ambiguous-outcome probing before any retry, recovery contracts and stored verification
  evidence. Irreversible actions require manual intervention.
- **Learning feedback loop** — structured regression tests generated from failures,
  review and activation, replay against the pinned version, comparison across published
  versions, and a read-only view of which versions resolved each failure mode.
- **Invoice processing vertical slice** — one workflow (retrieve invoice → retrieve
  purchase order → validate → compare → decide → schedule → disburse → record) that
  exercises every module, with four required chaos scenarios, a guided console page and
  an end-to-end acceptance test that reports each step.
- **Alan integration** — a typed adapter, streamable-HTTP MCP server, CLI fallback and a
  team Skill. Natural-language requests map to tested operations; consequential actions
  require a two-step confirmation token and every success claim comes from a persisted
  `SUCCEEDED`/`VERIFIED` result.
- **React developer console** — Overview, Workflow Studio, Learning Studio, workflow
  detail with version history, Execution Inspector, Chaos Lab, Decision Inbox, Recovery &
  Audit, and Settings.
- **Labelled demonstration workspace** — the **invoice-processing** slice (one normal
  verified run, a discrepancy waiting on finance and a recoverable PO timeout, plus five
  fault scenarios), an invoice-approval workflow with five sample runs and four fault
  scenarios, an invoice-discrepancy workflow with three runs that block for three
  different reasons, and a vendor-payout workflow with a broken v1, a fixed v2, a recovered
  failure and a cross-version regression matrix. Fixtures are tagged `origin=DEMO` and are
  never counted as live activity.

> **Execution drivers in this build:** `simulated-v1` for engine workflows against the
> in-process invoice sandbox, and `playwright-v1` for replaying learned workflows against
> the controlled `/demo-app` site. A browser run is labelled with its driver and its real
> Chromium evidence. The browser path is deliberately limited to the demo we own; it is
> not general-purpose website automation.

## Why

Automation breaks at the edges: a button that moved, a payment that timed out, a human
approval nobody asked for, a retry that double-charged. FLOWSTATE treats a workflow as a
**verifiable state machine** rather than a script, so "it worked" is a claim backed by
evidence.

## Quick start

Prerequisites: macOS with Homebrew Python 3.15 (`/opt/homebrew/bin/python3`), Node 24 /
npm 11, and network access for the first install. Docker is not required.

```bash
make bootstrap        # .venv, backend[dev,alan], frontend deps, Chromium
make run-backend      # http://127.0.0.1:8000  (OpenAPI at /docs)
make run-frontend     # http://localhost:5173
```

Open <http://localhost:5173>. The first backend start creates the SQLite database at
`.flowstate/flowstate.db`, applies the schema, and seeds the demonstration workspace.

Manual equivalent:

```bash
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/python -m pip install -e "backend[dev]"
npm --prefix frontend ci
.venv/bin/playwright install chromium

# terminal 1
cd backend && ../.venv/bin/uvicorn flowstate.main:app --reload --port 8000

# terminal 2
npm --prefix frontend run dev
```

### Configuration

Copy `.env.example` to `.env` for the backend and `frontend/.env.example` to
`frontend/.env.local` only when overriding defaults. All backend settings use the
`FLOWSTATE_` prefix; secrets have no production defaults. The console talks to
`/api/v1` through Vite's proxy by default.

Useful settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLOWSTATE_DATA_DIR` | `.flowstate` | SQLite database and evidence store |
| `FLOWSTATE_ENVIRONMENT` | `development` | Open local mode (`development`, `dev`, `test`, `local`) or a protected deployment mode |
| `FLOWSTATE_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Exact console origins; deployed modes reject loopback and wildcard origins |
| `FLOWSTATE_API_TOKEN` | unset | Optional only in local development; deployed modes require at least 32 characters |
| `FLOWSTATE_SECRET_KEY` | development-only value | Deployed modes require an explicit signing key of at least 32 characters |
| `FLOWSTATE_API_ACTOR_KIND` | `AGENT` | Server-assigned actor kind outside local development; set to `HUMAN` only behind a trusted human-authentication gateway |
| `FLOWSTATE_API_ACTOR_ROLE` | `OPERATOR` | Server-assigned role for the single bearer-authenticated API principal outside local development |
| `FLOWSTATE_AUTO_SEED_DEMO` | `true` | Seed the demo workspace on startup |
| `FLOWSTATE_DEMO_SEED_ENABLED` | `true` | Allow seed/reset endpoints |
| `VITE_API_BASE_URL` | same origin | Point the console at another API origin |
| `VITE_PROXY_TARGET` | `http://127.0.0.1:8000` | Dev/preview proxy target |

## Screens

| Screen | What it does |
| --- | --- |
| **Overview** | Backend-computed counts, verified success rate, run distribution, audit integrity, recent runs and attention queue |
| **Invoice slice** | The end-to-end demo: live stage status for learning, normal execution, chaos, the human bottleneck, recovery, regression and audit |
| **Workflow Studio** | Workflow cards, search, create from a publishable starter or a blank draft |
| **Learning Studio** | Record the controlled invoice demo, review typed candidates and ranked locators, resolve ambiguities, validate selectors, save/publish a version, and replay with different permitted inputs |
| **Workflow detail** | Version history, immutable published versions, publish draft, fork draft, action/invariant definitions, run workflow |
| **Execution Inspector** | Filter runs by status, origin and workflow; start an execution; inspect a run |
| **Run detail** | Ordered event timeline with payloads and hashes, observed action states, invariant verdicts, chain verification, recovery, regression creation |
| **Chaos Lab** | Declare deterministic fault scenarios, evaluate an authorized baseline against a fault-injected run, inspect the first violated invariant, failure action/evidence, safe-failure and recovery status, and save a reproducible regression test |
| **Decision Inbox** | Minimum unresolved decision with blocker classification, persisted evidence, resumption conditions, decision history, and approve / reject / provide-info / defer |
| **Recovery & Audit** | Failure modes by version, audit-chain recomputation, recovery attempts with contract checks and evidence, and regression tests with review, replay and cross-version comparison |
| **Settings & health** | Liveness/readiness, database/schema, runtime config, CORS, execution boundary, demo seed/reset |

## Alan integration

Alan's supported runtime boundary is a **custom MCP connector** (Settings → Plugins → MCP
→ Add custom connector), plus a team **Skill** for instructions. This repo ships both:

```bash
.venv/bin/python -m pip install -e "backend[dev,alan]"
.venv/bin/python -m flowstate.alan.mcp_server --transport streamable-http --port 8765
make alan-demo   # typed adapter demo against a running authenticated backend
```

Register `http://127.0.0.1:8765/mcp` as a custom connector with
`Authorization: Bearer $FLOWSTATE_ALAN_MCP_TOKEN`, or use the `flowstate-alan` CLI from a
session. Connector registration is UI-only; no Alan API for it is exposed in this
environment. See [docs/ALAN_INTEGRATION.md](docs/ALAN_INTEGRATION.md) for verified facts,
operation coverage, confirmation semantics, security and the exact limitation.

The adapter, CLI and MCP server have local automated tests. A real tool invocation from
an Alan workspace has **not** been verified in this environment; the connector must be
registered in Alan and tested manually. Passing adapter tests does not establish that
external integration.

Status is always encoded as icon + text + tone, never colour alone. Loading, empty, error
and success states are rendered explicitly; the console never fabricates execution data.

## Demo workspace

The seed is explicit and reversible:

```bash
make seed-demo
make reset-demo
```

You can also use **Settings → Demonstration workspace**. Reset deletes only rows tagged
`DEMO`; live workflows and runs are untouched. Batch ids are shown wherever fixtures
appear: `demo-invoice-approval-v1` and `demo-invoice-discrepancy-v1`.

The **invoice-processing** workspace is the vertical slice. Its eight nodes cover
retrieval, validation, comparison, the gated approval, scheduling, disbursement and
recording the final result. The four required chaos scenarios are `PO API timeout`,
`Invoice total mismatch`, `Lost response after payment executes` and
`Approval never arrives`; `Partial ledger commit` demonstrates compensation. The guided
page at `/slice` shows every stage from live records only — a scenario that has not run
shows **“Not run yet”**, never a simulated result.

The vendor-payout workspace is the recovery and feedback demonstration:

| Version | Configuration | Result |
| --- | --- | --- |
| v1 | Disbursement writes the ledger entry but never sets `PAID` | Run fails `Invoice reaches PAID`; compensation restores the pre-payment state |
| v2 | Disbursement also sets `PAID` | The same regression test passes; the failure mode shows `RESOLVED in v2` |

A second run blocks on the irreversible “Notify the vendor” email and requires manual
intervention. See [`docs/RECOVERY.md`](docs/RECOVERY.md).

The discrepancy workspace is the decision-engine demonstration. Three runs block at the
same approval node for different reasons, so the inbox holds four open decisions:

| Run | Blocker cause | Required role |
| --- | --- | --- |
| Variance waiting on finance | `CONFLICTING_EVIDENCE` | Finance approver |
| Missing cost centre | `MISSING_INPUT` | Operator |
| Expired approval policy | `VALIDATION_FAILED` (expired evidence) | Reviewer |
| Invoice Approval historical run | `HUMAN_APPROVAL_REQUIRED` | Finance approver |

Approving the variance, supplying the cost centre and refreshing the policy all resume
their runs to a verified `SUCCEEDED` state. Approving the expired-policy decision without
refreshing the evidence is refused with `EVIDENCE_STALE`.

## API

All routes are under `/api/v1`. Errors use `application/problem+json` with a `code`,
`detail` and request id. Health is intentionally unauthenticated.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health`, `/health/ready` | Liveness and readiness |
| `GET` | `/system`, `/system/overview` | Configuration and dashboard aggregate |
| `GET/POST` | `/workflows` | List / create |
| `GET` | `/workflows/{id}` | Detail and version list |
| `GET/POST` | `/workflows/{id}/versions` | List / fork a draft |
| `GET` | `/workflows/{id}/versions/{versionId}` | Actions and invariants |
| `POST` | `/workflows/{id}/versions/{versionId}/publish` | Publish a draft |
| `GET/POST` | `/captures` | List / start a controlled recording |
| `GET` | `/captures/{id}` | Candidates, selector validation and recent replays |
| `POST` | `/captures/{id}/pause`, `/resume`, `/stop` | Recording lifecycle |
| `POST` | `/captures/{id}/events` | Ingest recorded interactions |
| `PATCH` | `/captures/{id}/actions/{actionId}` | Edit or explicitly resolve a candidate |
| `POST` | `/captures/{id}/validate` | Resolve every selector with Playwright |
| `POST` | `/captures/{id}/workflow` | Save a draft or publish a version |
| `POST` | `/captures/{id}/replay` | Execute the saved workflow with inputs |
| `GET` | `/captures/{id}/runs` | Replays for one recording |
| `GET/POST` | `/runs` | List / start a run |
| `GET` | `/runs/{id}` | Run projection with events, verdicts, node states and the final state |
| `GET` | `/runs/{id}/events` | Ordered audit log |
| `GET` | `/runs/{id}/events/stream` | Authenticated SSE audit stream; resumes from `Last-Event-ID` or `after` |
| `GET` | `/runs/{id}/events/verify` | Recompute the hash chain |
| `GET` | `/runs/{id}/evidence/{evidenceId}` | Hash-checked screenshot/evidence download |
| `GET/POST` | `/scenarios` | List / create fault scenarios |
| `POST` | `/scenarios/{id}/run` | Start a fault run only |
| `POST` | `/scenarios/{id}/evaluate` | Authorized baseline + fault run + report (`Idempotency-Key` supported) |
| `GET` | `/scenarios/{id}/report` | Recomputed baseline/fault report |
| `GET` | `/decisions` | Decision inbox (`status`, `kind`, `run_id`, `open_only`) |
| `GET` | `/decisions/{id}` | Blocker, evidence packet, history, permitted actions |
| `GET` | `/decisions/{id}/history` | Append-only decision history |
| `POST` | `/decisions/{id}/approve` | Re-check and resume (single-use token) |
| `POST` | `/decisions/{id}/reject` | Stop the run safely |
| `POST` | `/decisions/{id}/provide-info` | Supply declared missing input and re-evaluate |
| `POST` | `/decisions/{id}/defer` | Keep the run blocked and record why |
| `GET` | `/blockers`, `/runs/{id}/blockers` | Blocker records with evidence |
| `GET` | `/runs/{id}/recovery-plan` | Policy, contract, strategies and whether recovery is supported |
| `POST` | `/runs/{id}/recover` | Apply `RESUME` / `COMPENSATE` / `REPLAY` / `MANUAL` and verify |
| `GET` | `/recoveries`, `/runs/{id}/recoveries`, `/recoveries/{id}` | Recovery attempts with checks and hashes |
| `POST` | `/recoveries/{id}/note` | Record manual intervention |
| `PATCH` | `/regression-tests/{id}` | Review and save a test |
| `POST` | `/regression-tests/{id}/activate` | Activate a reviewed test |
| `POST` | `/regression-tests/{id}/compare` | Compare the spec across published versions |
| `GET` | `/regression-tests/{id}/results` | Stored per-version results |
| `GET` | `/feedback/failure-modes` | Detected failure modes and resolution by version |
| `GET` | `/audit`, `/audit/verify/{runId}` | Chain status across runs |
| `GET/POST` | `/regression-tests` | List / create regression tests |
| `POST` | `/regression-tests/{id}/replay` | Replay a regression test |
| `GET` | `/demo` | Demo workspace state |
| `POST/DELETE` | `/demo/seed`, `/demo/reset` | Seed / reset fixtures (`DELETE /demo/reset`) |

## Human decisions

A blocked node is classified into exactly one of eight causes: `MISSING_INPUT`,
`VALIDATION_FAILED`, `CONFLICTING_EVIDENCE`, `PERMISSION_REQUIRED`,
`HUMAN_APPROVAL_REQUIRED`, `TOOL_UNAVAILABLE`, `UNKNOWN_OUTCOME` or
`RECOVERY_PREREQUISITE_MISSING`. Only the lowest-ordinal actionable blocker is asked
about, because later ones may become moot.

The evidence packet is resolved from persisted run state and hash-linked audit events.
Every row records its source path, the record it came from, when it was observed, and a
validation result (`PASS`, `FAIL`, `MISSING`, `EXPIRED`, `VALID`, `INFO`). Labels and
relations come from the workflow declaration; no model output is evidence.

Authority is enforced per blocker. In local development the console can select a demo role
with `X-Flowstate-Role` (`OPERATOR`, `REVIEWER`, `FINANCE_APPROVER`, `ADMIN`). Outside
local development those client-provided actor and role headers are ignored: the bearer
credential maps to the one server-configured principal and `FLOWSTATE_API_ACTOR_ROLE`.
This is a single-principal deployment boundary, not a user identity provider or multi-user
role system. Configure it for a trusted operator gateway only; do not expose a shared
bearer token as a substitute for per-user authentication.

**Approval is a request to re-check, not an override.** Before a run resumes, the backend
re-validates the acting role, that the evidence still hashes to what the human saw, that
no evidence has expired, that declared resume conditions hold, and that the node's
preconditions still pass. If any check fails the run stays blocked, the evidence is
refreshed, and the response explains which check failed.

See [`docs/DECISION_ENGINE.md`](docs/DECISION_ENGINE.md) for the full contract.

## The demo, end to end

Start the backend with `make run-backend` in one terminal and the frontend with
`npm --prefix frontend run dev` in another. Seeding runs on first startup by default;
run `make seed-demo` only if auto-seeding is disabled. Open <http://localhost:5173/slice>.
The reproducible seeded invoice-processing journey uses separate persisted runs for its
decision and recovery portions:

1. In Chaos Lab, evaluate **Invoice total mismatch** and inspect the resulting fault run,
   violated invariant and evidence.
2. In Decision Inbox, resolve the finance decision and verify the resumed run reaches
   `SUCCEEDED` / `VERIFIED`.
3. On the slice page, open the seeded PO-timeout run, inspect its failure/evidence and
   audit events, create a regression test, and run the supported recovery.
4. Confirm the recovery contract and run invariants pass, then replay the regression
   test and inspect its persisted result.

The automated browser journey is `frontend/e2e/invoice-slice.spec.ts`. This uses the
controlled in-process invoice sandbox; it does not perform real financial transactions.
The same flow can also be called through Alan's tested adapter, but a live Alan workspace
invocation is not yet verified:

```bash
scripts/demo_alan_slice.sh          # list, inspect, chaos, blockers, recovery, regression
```

Every consequential call returns `CONFIRMATION_REQUIRED` first and applies only when
repeated with the token after explicit approval. The adapter cannot approve decisions.

## Verification

```bash
make verify        # backend ruff + mypy, backend/frontend tests, frontend typecheck/build
make test-browser  # real-Chromium capture/replay/chaos tests
make verify-ui     # Playwright console journeys against a real backend + Vite
make verify-all    # verify + browser capture suite + console journeys
make test-alan     # typed adapter, MCP, CLI and idempotency tests
```

Last locally verified on 2026-10-27: `make verify-all` passed with 178 non-browser
backend tests (4 browser tests run separately), 22 frontend tests, 4 real-Chromium
browser tests, and 5 Playwright UI journeys. `make test-alan` passed 23 local adapter,
MCP, CLI and idempotency tests. These local results do not prove an Alan workspace
invocation or production deployment. The production frontend build succeeds but emits a
warning that the main minified JavaScript chunk exceeds 500 kB.

Playwright starts a fresh backend database and the frontend on dedicated local ports.
The console suite follows an execution from a second browser context over SSE, approves
a gated action, checks the audit chain and primary screens, and tests mobile layout.
Other journeys cover controlled-site learning/replay and invoice decision, recovery and
regression. The browser suite tests real Chromium capture/replay paths.

### What is implemented versus verified

| Area | Current status |
| --- | --- |
| Workflow execution, decisions, chaos, recovery, evidence and regression | Implemented and covered by local unit/integration tests; the invoice journey is also covered by browser E2E tests |
| Background runs and SSE | Persisted queue and authenticated event stream implemented; local tests cover live observation, replay cursors and interrupted-job startup handling |
| Browser learning | Tested against the controlled `/demo-app` site only; not general-purpose web automation |
| Alan | Adapter, CLI and MCP server locally tested; no live invocation from an Alan workspace has been verified here |
| Production multi-user use | Not implemented: bearer auth uses one configured principal, without per-user ownership or tenant isolation |
| Public/deployed operation | Not verified; the local test suite is not a production deployment or security certification |

Direct commands:

```bash
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
npm --prefix frontend run test
npm --prefix frontend run typecheck
npm --prefix frontend run build
PYTHONPATH=backend .venv/bin/pytest backend/tests -m browser -q
npm --prefix frontend run test:e2e
```

## Project layout

```
backend/flowstate/
  main.py              app factory, middleware, CORS, router wiring
  config.py            environment settings
  db/                  engine, schema initialisation, SQLAlchemy models
  capture/             demo app, recorder contract, selector ranking, Playwright driver
  domain/              enums, schemas, conditions, invariants, policy
  repositories/        repository ports and SQLite unit of work
  services/            workflows, learning, execution, chaos, decisions, recovery, audit
  api/routers/         versioned REST endpoints
  demo/                labelled fixtures and seed/reset
  sandbox/             deterministic in-process invoice driver
backend/tests/         unit, API/integration and real-Chromium browser tests
frontend/src/
  api/                 typed client, problem+json mapping, React Query hooks
  components/ui/       reusable primitives (buttons, cards, tables, modal, states)
  components/domain/   run table, event timeline, action table, verdicts
  pages/               the nine console screens
  styles.css           dark design system and responsive layout
frontend/e2e/          Playwright journeys
```

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/PRD.md`](docs/PRD.md) | Problem, personas, journeys, requirements |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System context, components, data flow, API surface |
| [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md) | Domain objects, fields, relationships and invariants |
| [`docs/STATE_MACHINES.md`](docs/STATE_MACHINES.md) | Run, approval, fault and recovery state machines |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Trust boundaries, policy, capability tokens, audit chain |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | Phases, dependencies, file manifest |
| [`docs/LEARNING.md`](docs/LEARNING.md) | Capture format, selector policy, redaction, replay semantics and limitations |
| [`docs/CHAOS.md`](docs/CHAOS.md) | Fault catalogue, invariant evaluator, baseline/fault reporting and recovery rules |
| [`docs/CHAOS_TEST_REPORT.md`](docs/CHAOS_TEST_REPORT.md) | Actual fault-matrix and verification results from this build |
| [`docs/ALAN_INTEGRATION.md`](docs/ALAN_INTEGRATION.md) | Verified Alan MCP/Skill mechanism, setup, operations and limitations |
| [`docs/ACCEPTANCE_CRITERIA.md`](docs/ACCEPTANCE_CRITERIA.md) | Testable criteria |
| [`docs/TEST_STRATEGY.md`](docs/TEST_STRATEGY.md) | Test pyramid, determinism kit, fixtures |

## Safety

FLOWSTATE never performs real financial transactions. Payment-shaped actions target the
sandbox only, outbound origins are allow-listed, real payment hosts are denied by
construction, and every state transition appends to a hash-linked audit chain. Secrets
come from the environment and logs pass through a redaction filter. The hash chain helps
detect altered event history; it is not an absolute tamper-proof guarantee against an
operator who can also alter the database or signing/configuration material.

## Known limits

- Workflow learning is scoped to the controlled `/demo-app` invoice site. Selector
  capture covers text inputs, selects, clicks, links and submit buttons; it does not
  understand arbitrary websites, drag and drop, uploads, canvas, multi-tab flows or
  cross-origin iframes. See [docs/LEARNING.md](docs/LEARNING.md).
- Chaos faults cover the in-process sandbox; the browser channel currently supports
  `SELECTOR_DRIFT` only. Delayed approval is modelled with an injected clock rather than
  by sleeping. See [docs/CHAOS.md](docs/CHAOS.md).
- Browser replay cannot resume mid-page after a pause or decision because live page state
  is not persisted. Replay starts fresh and fails safely on selector drift.
- Background UI-started runs are stored in a database-backed queue, but workers execute
  in-process in a bounded thread pool. On startup, queued work is resubmitted; jobs found
  already `RUNNING` are marked interrupted and their outcome is treated as unknown, not
  retried blindly. This is not a distributed queue: use a single API worker with the
  current SQLite setup. Synchronous API runs and decision/recovery requests still execute
  in the request process, and SQLite is single-writer.
- Bearer auth outside local development maps requests to one configured shared principal.
  Per-user identity, run ownership checks, and tenant isolation are not implemented; do
  not expose this prototype to multiple untrusted users or represent it as production
  multi-tenant security. See [docs/SECURITY.md](docs/SECURITY.md).
- The Alan adapter and MCP server are locally tested, but a real Alan workspace tool
  invocation has not been verified here. Connector registration is a manual UI action;
  see [docs/ALAN_INTEGRATION.md](docs/ALAN_INTEGRATION.md).
- Alembic is not used yet; schema initialisation is explicit and version-stamped
  (`PRAGMA user_version`). A migration runner can branch on that version later.

## License

TBD (open source; license to be chosen before the first public release).