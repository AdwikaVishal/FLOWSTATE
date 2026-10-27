# AGENTS.md — FLOWSTATE AI

Guidance for agents and humans working in this repository. Scope: the entire repo.

## Environment facts (verified, do not re-litigate)

| Fact | Value |
| --- | --- |
| OS | macOS (darwin), zsh |
| Python | `/opt/homebrew/bin/python3` → **3.15.0**; system `/usr/bin/python3` is 3.9 (too old — do not use) |
| pip | **PEP 668 externally-managed** — `pip install` outside a venv fails. Always use `.venv` |
| Node | v24.21.0 / npm 11.20.0 |
| Docker | **not installed** — the sandbox is process-level, not container-level |
| gh | installed and authenticated as `AdwikaVishal` (scopes: gist, read:org, repo, workflow) |
| Network | pypi and npm reachable |
| Playwright browsers | not yet installed (`playwright install chromium` required) |

## Repository layout

- `backend/` — FastAPI service, domain model, execution engine, capture, faults, sandbox. Python package `flowstate`.
- `frontend/` — React + TypeScript (Vite) developer console.
- `docs/` — PRD, architecture, domain model, state machines, security, implementation plan, acceptance criteria, test strategy.
- `scripts/` — dev helpers, demo seeding, validation entry points.

## Commands

```bash
# one-time
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/python -m pip install -e "backend[dev]"

# run
.venv/bin/uvicorn flowstate.main:app --reload --port 8000   # workdir: backend
npm --prefix frontend run dev

# test
.venv/bin/pytest backend/tests -q                           # all backend tests
.venv/bin/pytest backend/tests/unit -q                      # fast unit only
npm --prefix frontend run test

# verify everything
make verify
```

## Non-negotiable rules

1. **All execution goes through the backend.** The frontend and any LLM never drive Playwright, the filesystem, or the network directly.
2. **The language model has no authority.** LLM output is advisory input to a service that validates against `ActionPolicy` and the state machines. It cannot mint authorization tokens, skip preconditions, or mark a run verified.
3. **Never perform real financial transactions.** Payment-shaped actions resolve to the sandbox demo app only. `ActionPolicy` blocks any outbound call to a non-allow-listed origin.
4. **Ambiguous external outcomes are `UNKNOWN`, never `SUCCEEDED`.** A confirming probe must resolve them.
5. **Never claim success without verification.** Terminal success requires a passed postcondition/invariant check.
6. **Every state transition appends to the audit chain** (`ExecutionEvent`, hash-linked). No silent mutations.
7. **No secrets in code, logs or fixtures.** Config comes from environment variables; logs run through a redaction filter.
8. **A decision is a re-check, not an override.** Approval must re-validate the acting role, evidence freshness, the evidence hash, declared resume conditions and node preconditions. Never resume just because a request was approved.
9. **Blocker evidence must be persisted facts.** Evidence rows resolve a real state path or audit event and record source, record ref, observation time and validation. Never put model-generated prose in an evidence field.
10. **Decisions carry an acting role.** The console sends `X-Flowstate-Role` (`OPERATOR`, `REVIEWER`, `FINANCE_APPROVER`, `ADMIN`). `ADMIN` covers all; agent identities are always rejected for decision actions.

## Conventions

- Python 3.15, full type hints, `from __future__ import annotations`, Pydantic v2 models at boundaries.
- Domain state changes only via the transition tables in `flowstate/domain/statemachine.py`.
- Repositories are accessed through the `Repository`/`UnitOfWork` ports so SQLite can be swapped for PostgreSQL.
- Tests are deterministic: injectable clock and seeded RNG, no `time.sleep`, no live network except explicitly marked `@pytest.mark.browser`.