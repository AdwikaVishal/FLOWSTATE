import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, evidenceUrl, faultTypes } from '../api/client'
import { useRuns, useScenarios, useVersion, useWorkflows } from '../api/queries'
import type {
  ChaosEvaluation,
  ChaosReport,
  ChaosRunOutcome,
  FaultType,
  Scenario,
} from '../api/types'
import { RunTable } from '../components/domain/RunTable'
import { Icon } from '../components/Icon'
import { Badge, OriginBadge, StatusBadge, VerdictBadge } from '../components/ui/Badge'
import { Button, buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader, MetricCard } from '../components/ui/Card'
import { FormField, Select, TextArea, TextInput } from '../components/ui/Fields'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import {
  EmptyState,
  ErrorState,
  InlineNotice,
  LoadingState,
  PageSkeleton,
} from '../components/ui/States'
import { faultLabel, formatDate, formatDuration, pluralize, statusLabel } from '../lib/format'

const faultDescriptions: Record<FaultType, string> = {
  TIMEOUT:
    'Apply the effect, lose the response, then reconcile with a read-only probe.',
  TIMEOUT_BEFORE_EXECUTION:
    'Time out before the operation executes. No effect may be applied; a retry is only allowed when the side-effect class permits it.',
  RESPONSE_LOST:
    'Apply the effect, lose the response, and require a probe to confirm what happened.',
  HTTP_500: 'Return a server error after the request was accepted.',
  MALFORMED_RESPONSE:
    'Commit the effect but return a response the engine cannot trust; reconcile before continuing.',
  MISSING_FIELD:
    'Commit the effect but omit a field the action requires. The action must fail, not accept a partial response.',
  DUPLICATE_RESPONSE:
    'Deliver the request twice so the effect is applied twice. The duplicate-effect invariant must catch it.',
  LATENCY: 'Succeed after injected delay without changing the outcome.',
  PARTIAL_COMMIT:
    'Commit half of the effect, then acknowledge. Postconditions may pass while a cross-action invariant fails.',
  STALE_READ:
    'Apply the effect but serve the confirming probe from a stale replica, so the outcome stays unknown.',
  POSTCONDITION_FAILURE:
    'Acknowledge success but leave the declared postcondition state false.',
  NETWORK_DROP:
    'Drop the request before it reaches the operation. No effect is applied and the run must be recoverable, not successful.',
  RATE_LIMITED:
    'Reject the request with a rate limit before the effect. Irreversible actions must not be retried automatically.',
  TRANSIENT_ERROR:
    'Fail the dependency before the effect; a larger retry budget may succeed.',
  CONTRADICTORY_TOTALS:
    'Apply the operation but make the invoice and purchase-order totals disagree. The policy invariant must catch it.',
  DELAYED_APPROVAL:
    'Make the human approval arrive after the decision TTL. Approval must be rejected and the run must not resume.',
  SELECTOR_DRIFT:
    'Reload the controlled demo with changed ids, labels and accessible names. Selector resolution must fail safely.',
}

const evaluationLabels: Record<ChaosEvaluation, string> = {
  FAULT_DETECTED: 'Fault detected by an invariant',
  NO_FAULT_EFFECT: 'Fault had no observable effect',
  SAFE_FAILURE_WITHOUT_VIOLATION: 'Failed or blocked safely without an invariant violation',
  BASELINE_NOT_VERIFIED: 'Baseline did not verify',
  INCONCLUSIVE: 'Inconclusive — not a pass',
}

function evaluationTone(
  evaluation: ChaosEvaluation,
): 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  if (evaluation === 'FAULT_DETECTED') return 'info'
  if (evaluation === 'SAFE_FAILURE_WITHOUT_VIOLATION') return 'success'
  if (evaluation === 'BASELINE_NOT_VERIFIED') return 'danger'
  if (evaluation === 'INCONCLUSIVE') return 'warning'
  return 'neutral'
}

function parseObject(text: string, label: string): Record<string, unknown> {
  if (!text.trim()) return {}
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error(`${label} is not valid JSON.`)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object.`)
  }
  return parsed as Record<string, unknown>
}

function driverBadge(driver: string) {
  const browser = driver.includes('playwright')
  return (
    <Badge tone={browser ? 'info' : 'neutral'} icon={browser ? 'external' : 'terminal'}>
      {browser ? 'Chromium replay' : 'Simulated sandbox'}
    </Badge>
  )
}

function OutcomeRow({ label, outcome }: { label: string; outcome: ChaosRunOutcome }) {
  const violated = outcome.invariant_verdicts.filter((item) => item.verdict === 'VIOLATED').length
  return (
    <tr>
      <td>
        <strong>{label}</strong>
      </td>
      <td>
        <StatusBadge status={outcome.status} />
      </td>
      <td>
        <VerdictBadge verdict={outcome.verdict} />
      </td>
      <td>{driverBadge(outcome.driver)}</td>
      <td>{formatDuration(outcome.started_at, outcome.ended_at)}</td>
      <td>
        {violated > 0 ? (
          <Badge tone="danger">{pluralize(violated, 'violation')}</Badge>
        ) : outcome.reconciled_actions > 0 ? (
          <Badge tone="info">{pluralize(outcome.reconciled_actions, 'probe reconciliation')}</Badge>
        ) : (
          <span className="muted">0</span>
        )}
      </td>
    </tr>
  )
}

function ChaosReportModal({
  report,
  onClose,
  onSaveRegression,
  saving,
  savedMessage,
}: {
  report: ChaosReport | null
  onClose: () => void
  onSaveRegression: () => void
  saving: boolean
  savedMessage: string | null
}) {
  if (!report) return null
  const failure = report.failure_action
  const screenshots = (failure?.evidence ?? []).filter((item) => item.mime_type.startsWith('image/'))
  return (
    <Modal
      open={Boolean(report)}
      onClose={onClose}
      size="lg"
      title={`Chaos report · ${report.scenario_name}`}
      description="Baseline and fault-injected runs use the same workflow version, seed and input fixtures. Every outcome below is derived from persisted verdicts and events."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            icon="file"
            loading={saving}
            disabled={Boolean(report.regression_test_id)}
            onClick={onSaveRegression}
          >
            {report.regression_test_id ? 'Regression test saved' : 'Save reproducible regression test'}
          </Button>
        </>
      }
    >
      <div className="stack-md">
        <div className="chaos-report__meta">
          <div>
            <span className="label">Fault</span>
            <Badge tone="warning">{faultLabel(report.fault_type)}</Badge>
          </div>
          <div>
            <span className="label">Target action</span>
            <span className="mono">ordinal {report.target_ordinal}</span>
          </div>
          <div>
            <span className="label">Seed</span>
            <span className="mono">{report.seed}</span>
          </div>
          <div>
            <span className="label">Evaluation</span>
            <Badge tone={evaluationTone(report.evaluation)}>{evaluationLabels[report.evaluation]}</Badge>
          </div>
          <div>
            <span className="label">Failed safely</span>
            <Badge tone={report.failed_safely ? 'success' : 'danger'} icon={report.failed_safely ? 'check' : 'alert'}>
              {report.failed_safely ? 'Yes' : 'No'}
            </Badge>
          </div>
        </div>

        {!report.baseline_verified ? (
          <InlineNotice tone="danger" title="Baseline did not verify">
            This comparison is not evidence of reliability until the baseline run passes its
            own invariants. Inspect the baseline timeline before trusting the fault outcome.
          </InlineNotice>
        ) : null}

        <div className="table-wrap">
          <table className="data-table">
            <caption className="visually-hidden">Baseline and fault-injected outcomes</caption>
            <thead>
              <tr>
                <th>Outcome</th>
                <th>Status</th>
                <th>Verdict</th>
                <th>Driver</th>
                <th>Duration</th>
                <th>Invariants</th>
              </tr>
            </thead>
            <tbody>
              <OutcomeRow label="Baseline" outcome={report.baseline} />
              <OutcomeRow label="Fault injected" outcome={report.fault} />
            </tbody>
          </table>
        </div>

        {report.first_violated_invariant ? (
          <InlineNotice tone="danger" title="First violated invariant">
            <p>
              <strong>{report.first_violated_invariant.invariant_name}</strong> —{' '}
              {report.first_violated_invariant.detail}
            </p>
            <p className="muted">
              Severity {report.first_violated_invariant.severity}. Verdict is evaluated in the
              version's declared invariant order.
            </p>
          </InlineNotice>
        ) : (
          <InlineNotice tone={report.evaluation === 'NO_FAULT_EFFECT' ? 'info' : 'success'} title="No invariant violated">
            <p>
              {report.evaluation === 'NO_FAULT_EFFECT'
                ? 'The workflow still verified. For latency-only faults this can be expected; it is not a proof that every fault family is handled.'
                : 'The run failed or blocked without claiming success. Inspect the failure action for the classified reason.'}
            </p>
          </InlineNotice>
        )}

        {failure ? (
          <div className="chaos-failure">
            <div>
              <span className="label">Action associated with the failure</span>
              <strong>
                {failure.ordinal !== null ? `#${failure.ordinal} · ` : ''}
                {failure.label}
              </strong>
              <p className="muted">
                {failure.error_code ?? report.fault.failure_code ?? 'No error code'} ·{' '}
                {failure.error_message ?? report.fault.failure_message ?? 'No failure message'}
              </p>
              {failure.node_key ? <span className="mono muted">{failure.node_key}</span> : null}
            </div>
            {screenshots.length > 0 ? (
              <div>
                <span className="label">Evidence</span>
                <div className="evidence-grid">
                  {screenshots.map((item, index) => (
                    <a
                      key={item.id}
                      href={evidenceUrl(report.fault.run_id, item.id)}
                      target="_blank"
                      rel="noreferrer"
                      className="evidence-thumb"
                    >
                      <img src={evidenceUrl(report.fault.run_id, item.id)} alt={`Fault evidence ${index + 1}`} />
                      <span>{item.kind}</span>
                    </a>
                  ))}
                </div>
              </div>
            ) : (
              <p className="fine-print">No screenshot evidence was produced for this failure.</p>
            )}
          </div>
        ) : null}

        <details className="capture-action__details">
          <summary>
            <Icon name="chevronRight" size={12} /> Ordered fault-run events ({report.fault.event_count})
          </summary>
          <ol className="selector-list">
            {report.fault.events.map((event) => (
              <li key={event.seq}>
                <span className="mono">#{event.seq}</span>
                <Badge tone="neutral" mono>{event.type}</Badge>
                {event.node_key ? <span className="mono muted">{event.node_key}</span> : null}
                <span className="muted">{event.recorded_at}</span>
              </li>
            ))}
          </ol>
        </details>

        <div className="chaos-report__grid">
          <div>
            <span className="label">Recovery</span>
            <p>
              {report.recovery.attempted
                ? report.recovery.verified
                  ? 'Attempted and verified.'
                  : `Attempted; status ${statusLabel(report.recovery.status ?? 'UNKNOWN')}.`
                : 'Not attempted.'}
            </p>
            {report.recovery.detail ? <p className="muted">{report.recovery.detail}</p> : null}
          </div>
          <div>
            <span className="label">Configuration recorded</span>
            <pre className="code-block code-block--sm">
              {JSON.stringify(
                {
                  seed: report.seed,
                  parameters: report.parameters,
                  input_fixtures: report.input_fixtures,
                },
                null,
                2,
              )}
            </pre>
          </div>
        </div>

        {report.regression_test_id ? (
          <InlineNotice tone="success" title="Reproducible regression test saved">
            <p>
              Test <span className="mono">{report.regression_test_id.slice(0, 12)}</span> pins the
              version, scenario and seed. Replay it from Recovery &amp; Audit.
            </p>
          </InlineNotice>
        ) : savedMessage ? (
          <InlineNotice tone="success" title="Regression test saved">
            <p>{savedMessage}</p>
          </InlineNotice>
        ) : null}

        <div className="row-actions">
          <Link to={`/runs/${report.fault.run_id}`} className={buttonClass('secondary', 'sm')}>
            <Icon name="activity" size={14} />
            <span>Open fault timeline</span>
          </Link>
          <Link to={`/runs/${report.baseline.run_id}`} className={buttonClass('ghost', 'sm')}>
            <Icon name="activity" size={14} />
            <span>Open baseline timeline</span>
          </Link>
        </div>
      </div>
    </Modal>
  )
}

export function ChaosLabPage() {
  const scenarios = useScenarios()
  const workflows = useWorkflows()
  const allRuns = useRuns({})
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [workflowId, setWorkflowId] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [faultType, setFaultType] = useState<FaultType>('TIMEOUT')
  const [targetOrdinal, setTargetOrdinal] = useState('0')
  const [seed, setSeed] = useState('41')
  const [parametersText, setParametersText] = useState('{}')
  const [inputFixturesText, setInputFixturesText] = useState('{}')
  const [report, setReport] = useState<ChaosReport | null>(null)
  const [reportOpen, setReportOpen] = useState(false)
  const [savedMessage, setSavedMessage] = useState<string | null>(null)

  const runnable = useMemo(
    () => (workflows.data ?? []).filter((workflow) => workflow.current_version_id),
    [workflows.data],
  )
  const selectedWorkflow = runnable.find((workflow) => workflow.id === workflowId) ?? null
  const version = useVersion(workflowId, selectedWorkflow?.current_version_id ?? null)

  useEffect(() => {
    if (!workflowId && runnable.length > 0) setWorkflowId(runnable[0]?.id ?? '')
  }, [runnable, workflowId])

  useEffect(() => {
    if (version.data && version.data.actions.length > 0) {
      setTargetOrdinal(String(version.data.actions[0]?.ordinal ?? 0))
    }
  }, [version.data])

  const evaluate = useMutation({
    mutationFn: (id: string) => api.evaluateScenario(id),
    onSuccess: (result) => {
      setReport(result)
      setReportOpen(true)
      setSavedMessage(null)
      setActionError(null)
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['scenarios'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
      void queryClient.invalidateQueries({ queryKey: ['audit'] })
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const viewReport = useMutation({
    mutationFn: (id: string) => api.scenarioReport(id),
    onSuccess: (result) => {
      setReport(result)
      setReportOpen(true)
      setSavedMessage(null)
      setActionError(null)
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const saveRegression = useMutation({
    mutationFn: () => {
      if (!report) throw new Error('No report is open.')
      return api.createRegression(report.fault.run_id)
    },
    onSuccess: (test) => {
      setReport((current) => (current ? { ...current, regression_test_id: test.id } : current))
      setSavedMessage(`Regression test “${test.name}” now pins this scenario and seed.`)
      void queryClient.invalidateQueries({ queryKey: ['regression-tests'] })
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const createScenario = useMutation({
    mutationFn: () => {
      const parsedSeed = Number.parseInt(seed, 10)
      return api.createScenario({
        workflow_id: workflowId,
        name: name.trim(),
        description: description.trim(),
        target_ordinal: Number.parseInt(targetOrdinal, 10),
        fault_type: faultType,
        parameters: parseObject(parametersText, 'Parameters'),
        input_fixtures: parseObject(inputFixturesText, 'Input fixtures'),
        seed: Number.isNaN(parsedSeed) ? 0 : parsedSeed,
      })
    },
    onSuccess: () => {
      setOpen(false)
      setName('')
      setDescription('')
      setParametersText('{}')
      setInputFixturesText('{}')
      setFormError(null)
      void queryClient.invalidateQueries({ queryKey: ['scenarios'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
    onError: (error: Error) => setFormError(error.message),
  })

  const chaosRuns = (allRuns.data ?? []).filter(
    (run) => run.trigger === 'CHAOS' && run.fault_scenario_id,
  )
  const violations = (allRuns.data ?? []).filter(
    (run) => run.trigger === 'CHAOS' && run.failure_code === 'INVARIANT_VIOLATED',
  ).length

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Build"
        title="Chaos Lab"
        description="Run an authorized baseline and a deterministic fault-injected run with the same seed, version and inputs. Invariants decide whether the workflow failed safely, and every failing scenario can be saved as a reproducible regression test."
        actions={
          <>
            <Button
              variant="ghost"
              icon="refresh"
              loading={scenarios.isFetching}
              onClick={() => {
                void scenarios.refetch()
                void allRuns.refetch()
              }}
            >
              Refresh
            </Button>
            <Button variant="primary" icon="plus" onClick={() => setOpen(true)}>
              New scenario
            </Button>
          </>
        }
      />

      <div className="chaos-principle">
        <div className="chaos-principle__item">
          <span className="chaos-principle__step">1</span>
          <div>
            <strong>Baseline</strong>
            <p>The same workflow runs with its authorizations pre-approved and no fault.</p>
          </div>
        </div>
        <Icon name="chevronRight" size={16} className="chaos-principle__arrow" />
        <div className="chaos-principle__item">
          <span className="chaos-principle__step">2</span>
          <div>
            <strong>Inject</strong>
            <p>One deterministic boundary fault fires at the declared action.</p>
          </div>
        </div>
        <Icon name="chevronRight" size={16} className="chaos-principle__arrow" />
        <div className="chaos-principle__item">
          <span className="chaos-principle__step">3</span>
          <div>
            <strong>Evaluate</strong>
            <p>Invariants, first violation, recovery and regression are reported.</p>
          </div>
        </div>
      </div>

      <div className="grid grid--metrics">
        <MetricCard label="Scenarios" value={scenarios.data?.length ?? '—'} hint="Ready to evaluate" icon="zap" />
        <MetricCard label="Fault runs" value={chaosRuns.length} hint="Excluding baselines" icon="activity" to="/runs" />
        <MetricCard
          label="Invariant violations"
          value={violations}
          hint="Detected by checks, not by luck"
          icon="alert"
          tone={violations > 0 ? 'danger' : 'default'}
        />
        <MetricCard label="Fault types" value={faultTypes.length} hint="Boundary injectors available" icon="flask" />
      </div>

      {scenarios.isError ? (
        <ErrorState error={scenarios.error} onRetry={() => void scenarios.refetch()} />
      ) : scenarios.isPending ? (
        <PageSkeleton />
      ) : scenarios.data?.length === 0 ? (
        <Card>
          <CardBody>
            <EmptyState
              icon="zap"
              title="No chaos scenarios"
              description="Declare a scenario against a published workflow to compare a verified baseline with a controlled failure."
              action={
                <Button variant="primary" icon="plus" onClick={() => setOpen(true)}>
                  New scenario
                </Button>
              }
            />
          </CardBody>
        </Card>
      ) : (
        <div className="scenario-grid">
          {scenarios.data?.map((scenario: Scenario) => (
            <Card className="scenario-card" key={scenario.id}>
              <CardBody className="stack-md">
                <div className="scenario-card__top">
                  <span className="scenario-card__icon">
                    <Icon name="zap" size={18} />
                  </span>
                  <div className="scenario-card__badges">
                    <Badge tone="warning">{faultLabel(scenario.fault_type)}</Badge>
                    <OriginBadge origin={scenario.origin} />
                  </div>
                </div>
                <div>
                  <h2 className="scenario-card__title">{scenario.name}</h2>
                  <p className="scenario-card__workflow">
                    {scenario.workflow_name} · action {scenario.target_ordinal}
                  </p>
                </div>
                <p className="scenario-card__description">{scenario.description}</p>
                <dl className="kv kv--inline">
                  <div><dt>Seed</dt><dd className="mono">{scenario.seed}</dd></div>
                  <div>
                    <dt>Status</dt>
                    <dd>
                      <Badge tone={scenario.status === 'COMPLETED' ? 'success' : 'neutral'}>
                        {statusLabel(scenario.status)}
                      </Badge>
                    </dd>
                  </div>
                  <div><dt>Created</dt><dd>{formatDate(scenario.created_at)}</dd></div>
                </dl>
                <div className="scenario-card__footer">
                  <span className="fine-print">{faultDescriptions[scenario.fault_type]}</span>
                  <div className="row-actions">
                    {scenario.last_run_id ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        icon="activity"
                        loading={viewReport.isPending && viewReport.variables === scenario.id}
                        onClick={() => viewReport.mutate(scenario.id)}
                      >
                        Last report
                      </Button>
                    ) : null}
                    <Button
                      variant="secondary"
                      icon="play"
                      loading={evaluate.isPending && evaluate.variables === scenario.id}
                      onClick={() => evaluate.mutate(scenario.id)}
                    >
                      Evaluate scenario
                    </Button>
                  </div>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {actionError ? (
        <InlineNotice tone="danger" title="Scenario could not run">
          <p>{actionError}</p>
        </InlineNotice>
      ) : null}

      <Card>
        <CardHeader
          title="Recent chaos executions"
          subtitle="Fault-injected runs only. Baselines are linked from their report."
          icon="activity"
          action={<span className="fine-print">{pluralize(chaosRuns.length, 'run')}</span>}
        />
        <CardBody className="card__body--flush">
          <RunTable
            runs={chaosRuns.slice(0, 10)}
            emptyAction={<span className="fine-print">Evaluate a scenario to create the first chaos execution.</span>}
          />
        </CardBody>
      </Card>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="New fault scenario"
        description="Faults are injected at the execution boundary, never in the target application. Browser actions accept the SELECTOR_DRIFT fault; gated decisions accept DELAYED_APPROVAL."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              icon="zap"
              loading={createScenario.isPending}
              disabled={name.trim().length < 3 || !workflowId || !targetOrdinal}
              onClick={() => createScenario.mutate()}
            >
              Create scenario
            </Button>
          </>
        }
      >
        <div className="stack-md">
          {formError ? <InlineNotice tone="danger" title="Scenario not created">{formError}</InlineNotice> : null}
          <FormField label="Workflow" htmlFor="scenario-workflow" required>
            <Select id="scenario-workflow" value={workflowId} onChange={(event) => setWorkflowId(event.target.value)}>
              {runnable.length === 0 ? <option value="">No published workflows</option> : null}
              {runnable.map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name} · v{workflow.latest_version}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Name" htmlFor="scenario-name" required>
            <TextInput
              id="scenario-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Gateway timeout after acceptance"
              maxLength={200}
            />
          </FormField>
          <FormField label="Description" htmlFor="scenario-description" hint="What failure are you proving the workflow survives?">
            <TextArea
              id="scenario-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={2}
              placeholder="The gateway accepts the request but the response is lost. The probe must resolve the outcome."
            />
          </FormField>
          <div className="form-grid">
            <FormField label="Fault type" htmlFor="scenario-fault">
              <Select id="scenario-fault" value={faultType} onChange={(event) => setFaultType(event.target.value as FaultType)}>
                {faultTypes.map((item) => <option key={item} value={item}>{faultLabel(item)}</option>)}
              </Select>
            </FormField>
            <FormField label="Target action" htmlFor="scenario-target">
              <Select id="scenario-target" value={targetOrdinal} onChange={(event) => setTargetOrdinal(event.target.value)}>
                {version.data?.actions.map((action) => (
                  <option key={action.id} value={action.ordinal}>
                    {action.ordinal} · {action.label}
                  </option>
                )) ?? <option value="0">Loading actions…</option>}
              </Select>
            </FormField>
          </div>
          <FormField
            label="Parameters (JSON)"
            htmlFor="scenario-parameters"
            hint="Fault-specific values, for example {&quot;failures&quot;: 1} or {&quot;po_amount_cents&quot;: 180000}."
          >
            <TextArea
              id="scenario-parameters"
              rows={3}
              value={parametersText}
              onChange={(event) => setParametersText(event.target.value)}
            />
          </FormField>
          <FormField
            label="Input fixtures (JSON)"
            htmlFor="scenario-fixtures"
            hint="Declared workflow inputs used by both the baseline and fault run."
          >
            <TextArea
              id="scenario-fixtures"
              rows={3}
              value={inputFixturesText}
              onChange={(event) => setInputFixturesText(event.target.value)}
            />
          </FormField>
          <FormField label="Seed" htmlFor="scenario-seed" hint="The same scenario and seed reproduce the same injection and verdicts.">
            <TextInput id="scenario-seed" type="number" value={seed} onChange={(event) => setSeed(event.target.value)} />
          </FormField>
          <InlineNotice tone="info" title={faultLabel(faultType)}>
            {faultDescriptions[faultType]}
          </InlineNotice>
          {workflows.isPending || version.isFetching ? <LoadingState rows={1} label="Loading actions" /> : null}
        </div>
      </Modal>

      <ChaosReportModal
        report={reportOpen ? report : null}
        onClose={() => setReportOpen(false)}
        onSaveRegression={() => saveRegression.mutate()}
        saving={saveRegression.isPending}
        savedMessage={savedMessage}
      />
    </div>
  )
}
