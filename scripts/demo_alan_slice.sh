#!/usr/bin/env bash
# Drive the invoice-processing slice through the real Alan adapter.
#
# Discovery uses the read-only REST API; every consequential action goes through
# the Alan CLI, which returns CONFIRMATION_REQUIRED first and applies the change
# only when repeated with the token after explicit approval.
#
# Usage: scripts/demo_alan_slice.sh [base-url]
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000/api/v1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
export PYTHONPATH="$ROOT/backend"

alan() {
  "$PY" -m flowstate.alan.cli --base-url "$BASE_URL" --compact "$@"
}

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# Preview a mutating call, read the token, then repeat it with --confirm.
confirm() {
  local label="$1"; shift
  step "$label — preview"
  set +e
  local preview
  preview=$(alan "$@")
  local code=$?
  set -e
  echo "$preview"
  if [ "$code" -ne 2 ]; then
    echo "expected a confirmation preview (exit 2), got exit $code" >&2
    exit 1
  fi
  local token
  token=$(printf '%s' "$preview" | "$PY" -c \
    'import json,sys; print(json.load(sys.stdin)["details"]["confirmation_token"])')
  step "$label — confirmed"
  alan "$@" --confirm "$token"
}

step "Workflows known to Alan"
alan list-workflows

step "Inspect the invoice-processing workflow"
alan inspect-workflow invoice-processing

step "Evaluate the invoice-total mismatch scenario"
confirm "run-chaos-test" run-chaos-test invoice-processing --scenario "Invoice total mismatch"

FAULT_RUN=$(alan list-blockers --all \
  | "$PY" -c '
import json, sys
data = json.load(sys.stdin)["data"]["blockers"]
print(next(b["run_id"] for b in data if b["kind"] == "CONFLICTING_EVIDENCE"))
')
step "Blockers raised on the fault run ($FAULT_RUN)"
alan list-blockers --run-id "$FAULT_RUN"

step "Why the run is blocked"
alan explain-failure "$FAULT_RUN"

step "Recoverable run of the slice (read-only lookup)"
RECOVERABLE=$(curl -fsS "$BASE_URL/runs?limit=200" | "$PY" -c '
import json, sys
runs = json.load(sys.stdin)
print(next(r["id"] for r in runs if r.get("failure_code") == "TIMEOUT_BEFORE_EXECUTION"))
')
echo "recoverable run: $RECOVERABLE"

step "Request recovery"
confirm "request-recovery" request-recovery "$RECOVERABLE" --strategy RESUME

step "Regression tests known to Alan"
alan list-regression-tests --workflow vendor-payout

TEST_ID=$(alan list-regression-tests --workflow vendor-payout | "$PY" -c '
import json, sys
tests = json.load(sys.stdin)["data"]["tests"]
print(tests[0]["id"] if tests else "")
')
if [ -n "$TEST_ID" ]; then
  step "Replay the regression test"
  confirm "run-regression-tests" run-regression-tests --test-id "$TEST_ID"
fi

cat <<'NOTE'

Next, in the console:
  /slice      live stage status for the whole slice
  /decisions  approve the variance as a finance approver
  /recovery   inspect the contract checks and replay the regression test

The adapter cannot approve human decisions. Approval always goes through the
console's human endpoint with an acting role.
NOTE
