SHELL := /bin/zsh
VENV ?= .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: help bootstrap run-backend run-frontend run-alan-mcp test test-backend test-frontend test-browser \
	test-alan lint-backend typecheck-backend typecheck build verify verify-all verify-ui alan-demo seed-demo reset-demo clean

help:
	@echo "FLOWSTATE AI"
	@echo "  make bootstrap     create .venv, install backend/frontend deps, install Chromium"
	@echo "  make run-backend   FastAPI on http://127.0.0.1:8000 (docs at /docs)"
	@echo "  make run-frontend  Vite console on http://localhost:5173"
	@echo "  make run-alan-mcp  MCP connector on http://127.0.0.1:8765/mcp"
	@echo "  make test          backend + frontend unit/integration tests"
	@echo "  make test-browser  real Chromium capture/replay tests (needs Chromium)"
	@echo "  make test-alan     typed Alan adapter, MCP and CLI tests"
	@echo "  make lint-backend  ruff check + format check on the backend"
	@echo "  make typecheck-backend  mypy over flowstate"
	@echo "  make verify        tests, typecheck and production build (no browser)"
	@echo "  make verify-all    verify + real Chromium capture suite + console journeys"
	@echo "  make verify-ui     Playwright console journeys against a real backend"
	@echo "  make alan-demo     run the typed Alan adapter demo against a running backend"
	@echo "  make seed-demo     seed the labelled demo workspace on a running backend"
	@echo "  make reset-demo    remove only DEMO-tagged rows on a running backend"

bootstrap:
	@test -d $(VENV) || /opt/homebrew/bin/python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e "backend[dev,alan]"
	npm --prefix frontend ci
	$(VENV)/bin/playwright install chromium

run-backend:
	cd backend && ../$(VENV)/bin/uvicorn flowstate.main:app --reload --port 8000

run-frontend:
	npm --prefix frontend run dev

run-alan-mcp:
	$(PYTHON) -m flowstate.alan.mcp_server --transport streamable-http

alan-demo:
	PYTHONPATH=backend $(PYTHON) scripts/alan_demo.py

test: test-backend test-frontend

test-backend:
	PYTHONPATH=backend $(PYTEST) backend/tests -q

test-frontend:
	npm --prefix frontend run test

test-browser:
	PYTHONPATH=backend $(PYTEST) backend/tests -m browser -q

test-alan:
	PYTHONPATH=backend $(PYTEST) backend/tests -q -k "alan or idempotency"

lint-backend:
	$(VENV)/bin/ruff check backend
	$(VENV)/bin/ruff format --check backend

typecheck-backend:
	$(VENV)/bin/mypy backend/flowstate

typecheck:
	npm --prefix frontend run typecheck

build:
	npm --prefix frontend run build

verify: lint-backend typecheck-backend test-backend typecheck test-frontend build
	@echo "verify: all checks passed"

verify-all: verify test-browser verify-ui
	@echo "verify-all: console and browser checks passed"

verify-ui:
	npm --prefix frontend run test:e2e

seed-demo:
	curl -fsS -X POST http://127.0.0.1:8000/api/v1/demo/seed

reset-demo:
	curl -fsS -X DELETE http://127.0.0.1:8000/api/v1/demo/reset

clean:
	rm -rf .flowstate frontend/dist frontend/test-results frontend/playwright-report \
		backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache