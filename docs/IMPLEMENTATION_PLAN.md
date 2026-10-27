# FLOWSTATE AI — Implementation Plan

| Field | Value |
| --- | --- |
| Status | Ready to execute |
| Related | [ARCHITECTURE.md](ARCHITECTURE.md), [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md), [TEST_STRATEGY.md](TEST_STRATEGY.md) |

The plan builds a **single vertical slice first** (learn → execute → verify one real
workflow), then adds chaos, decisions, recovery and regression on top of the same engine.
No phase introduces a second execution path.

## 1. Dependencies

### Backend (`backend/pyproject.toml`)

| Package | Constraint | Why |
| --- | --- | --- |
| `fastapi` | `>=0.115` | HTTP API + OpenAPI |
| `uvicorn[standard]` | `>=0.32` | ASGI server |
| `pydantic` | `>=2.9` | Boundary and domain models |
| `pydantic-settings` | `>=2.6` | Env-driven config |
| `sqlalchemy` | `>=2.0` | SQLite now, PostgreSQL later |
| `alembic` | `>=1.14` | Schema migrations |
| `playwright` | `>=1.48` | Capture and execution driver |
| `httpx` | `>=0.28` | Probe and API tests |
| `python-multipart` | `>=0.0.12` | Uploads if needed |

Dev extras: `pytest>=8`, `pytest-asyncio`, `pytest-cov`, `hypothesis>=6`, `ruff`,
`mypy`, `types-*` as needed.

### Frontend (`frontend/package.json`)

`react` + `react-dom` (19), `typescript` (~5.7), `vite` (6+), `@vitejs/plugin-react`,
`@tanstack/react-query`, `react-router-dom`, `vitest`, `@testing-library/react`,
`@testing-library/user-event`, `openapi-typescript` (client generation), `eslint`.

### Toolchain facts that shape the plan

- Homebrew Python is **3.15** and PEP 668 managed → all installs happen in `.venv`.
- `docker` is **not installed** → the sandbox is process-level; no container steps.
- Playwright browsers are **not installed** → `playwright install chromium` is a required
  bootstrap step (needs network, ~150 MB).
- Dependency resolution on Python 3.15 was **verified** with dry-run installs in a scratch
  venv. Everything resolves with native `cp315` wheels:

  | Package | Resolved |
  | --- | --- |
  | `SQLAlchemy` | 2.1.6 |
  | `alembic` | 1.20.1 |
  | `playwright` | 1.64.1 |
  | `hypothesis` | 6.170.11 |
  | `mypy` | 2.4.0 |
  | `ruff` | 0.17.2 |

  (`fastapi`, `uvicorn`, `pydantic>=2`, `pytest`, `httpx` also resolved cleanly.) Pin the
  resolved versions in `pyproject.toml` after the first real install.

## 2. Phases

| # | Phase | Depends on | Deliverable | Exit criteria |
| --- | --- | --- | --- | --- |
| 0 | **Scaffold** | — | Repo layout, `pyproject.toml`, venv bootstrap, `Makefile`, lint/type config, empty app factory with `/healthz` | `make bootstrap` and `make verify` succeed; `/healthz` returns 200 |
| 1 | **Domain core** | 0 | Enums, Pydantic models, transition tables, guards, in-memory repositories | Unit tests for every transition table green; property tests for terminal-state and audit invariants |
| 2 | **Persistence** | 1 | SQLAlchemy models, SQLite adapters, `UnitOfWork`, migrations, audit writer with hash chain | Same test suite passes against SQLite and memory adapters; chain verification test green |
| 3 | **API shell** | 1, 2 | Routers for workflows/versions/runs/events, problem+json errors, auth dependency, OpenAPI | Integration tests via `httpx.ASGITransport`; OpenAPI validates; 401 without token |
| 4 | **Execution engine** | 3 | Step loop, pre/postcondition evaluation, `UNKNOWN` + probe, checkpoints, verification gate | A stubbed workflow runs end-to-end; ambiguous-outcome test proves no false success |
| 5 | **Demo app + sandbox** | 4 | Demo FastAPI app (onboarding/refund-shaped), sandbox supervisor, egress allow-list | Browser smoke test: sandbox reaches demo origin and blocks every other origin |
| 6 | **Capture + learning** | 5 | Playwright recorder, selector ranking, `ActionDefinition` derivation, publish validation | Recorded session becomes a publishable version; broken selector rejects publish |
| 7 | **Chaos** | 4, 5 | Fault registry, injectors, scenario lifecycle, invariant DSL + evaluator | Injected timeout produces `VIOLATED` with evidence; `INCONCLUSIVE` never passes |
| 8 | **Human decisions** | 4 | Minimum-unresolved-decision computation, evidence bundle, capability tokens, approve/deny/resume | Approval resumes at the right ordinal; deny/expiry fail safely; tokens single-use |
| 9 | **Recovery** | 4, 7 | Checkpoint restore, strategies, verification, escalation | Recovery reaches `VERIFIED` only with passing invariants |
| 10 | **Regression** | 7, 9 | Failure → spec generation, replay runner, status transitions | Generated test replays 3× with identical results |
| 11 | **Frontend console** | 3 (API), then per feature | Typed client, workflow list/detail, run timeline, chaos results, decision inbox, recovery view | Every acceptance flow is drivable from the UI; responsive at 380 px |
| 12 | **Acceptance + evidence** | all | End-to-end demo script, acceptance test suite, test report artifact, README polish | `make verify` green from clean checkout; acceptance criteria all mapped to passing tests |

**Critical path:** 0 → 1 → 2 → 3 → 4 → 5 → 7 → 8 → 9 → 10 → 12. Phase 6 (capture) can
run in parallel with 7 after 5. Phase 11 can start once 3 is stable and then track each
backend feature.

## 3. Planned file manifest

```
.
├── Makefile
├── README.md
├── AGENTS.md
├── .gitignore
├── .env.example
├── docs/                              # written in this task
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── DOMAIN_MODEL.md
│   ├── STATE_MACHINES.md
│   ├── SECURITY.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── ACCEPTANCE_CRITERIA.md
│   └── TEST_STRATEGY.md
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── migrations/
│   │   └── versions/
│   ├── flowstate/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── logging.py                 # structured logs + redaction filter
│   │   ├── ids.py                     # IdFactory / SequentialIdFactory
│   │   ├── clock.py                   # Clock / FrozenClock
│   │   ├── random.py                  # RandomSource / seeded
│   │   ├── db/
│   │   │   ├── engine.py
│   │   │   └── models.py
│   │   ├── domain/
│   │   │   ├── enums.py
│   │   │   ├── schemas.py
│   │   │   ├── conditions.py          # precondition/postcondition DSL
│   │   │   ├── policy.py
│   │   │   └── statemachine.py
│   │   ├── repositories/
│   │   │   ├── base.py
│   │   │   ├── memory.py
│   │   │   └── sqlite/
│   │   │       ├── unit_of_work.py
│   │   │       ├── workflows.py
│   │   │       ├── runs.py
│   │   │       ├── events.py
│   │   │       └── decisions.py
│   │   ├── services/
│   │   │   ├── audit.py
│   │   │   ├── workflow_service.py
│   │   │   ├── learning_service.py
│   │   │   ├── execution_engine.py
│   │   │   ├── chaos_service.py
│   │   │   ├── decision_service.py
│   │   │   ├── recovery_service.py
│   │   │   ├── regression_service.py
│   │   │   └── evidence_service.py
│   │   ├── capture/
│   │   │   ├── playwright_recorder.py
│   │   │   ├── selector_heuristics.py
│   │   │   └── demo_app/
│   │   │       ├── main.py
│   │   │       ├── routes.py
│   │   │       ├── state.py
│   │   │       └── templates/
│   │   ├── faults/
│   │   │   ├── registry.py
│   │   │   └── injectors.py
│   │   ├── sandbox/
│   │   │   ├── browser_sandbox.py
│   │   │   ├── supervisor.py
│   │   │   └── policy.py              # origin allow/deny lists
│   │   └── api/
│   │       ├── deps.py
│   │       ├── errors.py
│   │       └── routers/
│   │           ├── health.py
│   │           ├── captures.py
│   │           ├── workflows.py
│   │           ├── runs.py
│   │           ├── scenarios.py
│   │           ├── decisions.py
│   │           ├── recovery.py
│   │           └── regression.py
│   └── tests/
│       ├── conftest.py
│       ├── fixtures/
│       ├── unit/
│       │   ├── test_statemachine_*.py
│       │   ├── test_audit_chain.py
│       │   ├── test_policy.py
│       │   ├── test_conditions.py
│       │   └── test_tokens.py
│       ├── integration/
│       │   ├── test_api_*.py
│       │   ├── test_engine_*.py
│       │   ├── test_chaos_*.py
│       │   ├── test_decisions_*.py
│       │   ├── test_recovery_*.py
│       │   └── test_regression_*.py
│       ├── browser/
│       │   ├── test_capture.py
│       │   └── test_sandbox_egress.py
│       └── acceptance/
│           └── test_end_to_end.py
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/
│   │   │   ├── client.ts
│   │   │   └── generated.ts           # from OpenAPI
│   │   ├── components/
│   │   │   ├── StatusBadge.tsx
│   │   │   ├── EventTimeline.tsx
│   │   │   ├── EvidencePanel.tsx
│   │   │   ├── EmptyState.tsx
│   │   │   └── Layout.tsx
│   │   ├── features/
│   │   │   ├── workflows/
│   │   │   ├── runs/
│   │   │   ├── chaos/
│   │   │   ├── decisions/
│   │   │   ├── recovery/
│   │   │   └── regression/
│   │   └── styles/
│   └── tests/
└── scripts/
    ├── bootstrap.sh
    ├── dev.sh
    ├── demo_seed.py
    └── verify.sh
```

## 4. Risks

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Python 3.15 lacks wheels for some dependency | ~~Medium~~ **Low** | Blocks setup | **Retired:** dry-run install confirmed `cp315` wheels for SQLAlchemy, Alembic, Playwright, Hypothesis, mypy and ruff. Re-check only if a new dependency is added |
| R2 | PEP 668 rejects global installs | Certain | Setup confusion | `.venv` is the only supported path; `make bootstrap` fails loudly with a clear message |
| R3 | Playwright browser download is large / flaky | Medium | Phase 5–6 blocked | Install early in Phase 0; mark browser tests separately so the suite is useful without them |
| R4 | Browser tests are flaky | High | Erodes trust in the suite | Deterministic clock/RNG, route stubs, retries only at the harness level, no `sleep`; browser tests are excluded from the default gate |
| R5 | Scope creep across five capabilities | High | Nothing finishes | Vertical slice first; each later phase must reuse the engine and add tests, not a new path |
| R6 | Sandbox isolation is weaker than a container | Certain | Security posture | Documented limitation; egress allow-list, demo-only target, no real credentials |
| R7 | SQLite write contention with concurrent runs | Medium | Flaky integration tests | Single-writer engine per process; WAL; tests serialize runs |
| R8 | Selector drift makes capture unreliable | High | Learning demo fails | Ranked selector strategies with role+name first; publish-time validation rejects bad versions |
| R9 | Event schema churn breaks the hash chain | Medium | Audit tests fail | Canonical JSON + versioned payload schema; chain fixtures pin a known-good sequence |
| R10 | "Recovery" becomes a fake success | Medium | Core promise broken | `APPLIED ≠ VERIFIED`; verification event is required by the transition guard and tested |
| R11 | Demo app grows into a second product | Medium | Time lost | Fixed feature set: one onboarding/refund workflow with an approval step and a simulated payment |
| R12 | Frontend chases backend changes | Medium | Rework | Generate the client from OpenAPI; freeze route shapes at the end of Phase 3 |

## 5. Validation commands

These are the exact commands the implementation must satisfy. `make verify` runs the
non-browser gate; `make verify-all` adds the browser suite.

```bash
# ---- bootstrap (once) ----
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e "backend[dev]"
.venv/bin/playwright install chromium
npm --prefix frontend ci

# ---- static ----
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/mypy backend/flowstate
npm --prefix frontend run lint
npm --prefix frontend run typecheck

# ---- backend tests ----
.venv/bin/pytest backend/tests/unit -q
.venv/bin/pytest backend/tests/integration -q
.venv/bin/pytest backend/tests -q -m "not browser"          # default gate
.venv/bin/pytest backend/tests/browser -q -m browser        # needs chromium
.venv/bin/pytest backend/tests/acceptance -q

# ---- frontend tests ----
npm --prefix frontend run test
npm --prefix frontend run build

# ---- runtime smoke ----
(cd backend && ../.venv/bin/uvicorn flowstate.main:app --port 8000 &) ; sleep 2
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz

# ---- demo ----
.venv/bin/python scripts/demo_seed.py
.venv/bin/pytest backend/tests/acceptance/test_end_to_end.py -q

# ---- everything ----
make verify          # ruff + mypy + unit + integration + frontend test + build
make verify-all      # make verify + browser + acceptance
```

## 6. Definition of done

- [ ] Every phase's exit criteria met and demonstrated by a test.
- [ ] All acceptance criteria in [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) mapped to a passing test.
- [ ] `make verify` green from a clean checkout.
- [ ] Audit chain verifies for every run created during the acceptance suite.
- [ ] No secret, real payment host or credential anywhere in the repo.
- [ ] README quick start works on a machine that has never seen the project.
- [ ] Alan artifacts created: plan, architecture summary, test report with evidence.
