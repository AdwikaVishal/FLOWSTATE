# FLOWSTATE operations

## MCP tool arguments

| Tool | Arguments |
| --- | --- |
| `list_workflows` | `query`, `origin` |
| `inspect_workflow` | `workflow`, `version_id` |
| `run_workflow` | `workflow`, `inputs`, `version_id`, `confirmation_token`, `idempotency_key` |
| `get_execution_status` | `run_id` |
| `run_chaos_test` | `workflow`, `scenario`, `fault_type`, `confirmation_token`, `idempotency_key` |
| `explain_failure` | `run_id` |
| `list_blockers` | `run_id`, `open_only` |
| `inspect_evidence` | `run_id`, `evidence_id` |
| `request_recovery` | `run_id`, `strategy`, `confirmation_token`, `idempotency_key` |
| `list_regression_tests` | `workflow` |
| `run_regression_tests` | `test_ids`, `confirmation_token`, `idempotency_key` |

## CLI examples

```bash
export FLOWSTATE_API_URL=http://127.0.0.1:8000/api/v1
export FLOWSTATE_API_TOKEN=…

flowstate-alan list-workflows --origin DEMO
flowstate-alan inspect-workflow invoice-approval
flowstate-alan run-workflow invoice-approval --inputs '{}'
# exits 2 and prints details.confirmation_token
flowstate-alan run-workflow invoice-approval --inputs '{}' --confirm <token>
flowstate-alan get-execution-status <run-id>
flowstate-alan explain-failure <run-id>
flowstate-alan list-blockers --run-id <run-id>
flowstate-alan inspect-evidence <run-id>
flowstate-alan run-chaos-test invoice-approval --scenario "Duplicated gateway response"
flowstate-alan run-chaos-test invoice-approval --scenario "Duplicated gateway response" --confirm <token>
flowstate-alan request-recovery <run-id> --strategy RESUME --confirm <token>
flowstate-alan list-regression-tests
flowstate-alan run-regression-tests --confirm <token>
```

Every command prints one JSON envelope. Exit codes: `0` success, `2` confirmation
or a choice/input is required, `1` error.
