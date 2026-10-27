import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  useAudit,
  useCaptures,
  useDecisions,
  useRecoveries,
  useRegressionTests,
  useRuns,
  useScenarios,
  useVersion,
  useWorkflows,
} from '../api/queries'
import type { BadgeTone } from '../components/ui/Badge'
import { Badge } from '../components/ui/Badge'
import { buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader } from '../components/ui/Card'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import { Icon, type IconName } from '../components/Icon'
import { outcomeTone, relativeTime, shortId, statusLabel } from '../lib/format'

const SLICE_SLUG = 'invoice-processing'
const REQUIRED_SCENARIOS = [
  'PO API timeout',
  'Invoice total mismatch',
  'Lost response after payment executes',
  'Approval never arrives',
]

const openDecisionStatuses = new Set(['REQUESTED', 'NOTIFIED', 'DEFERRED', 'INFO_PROVIDED'])

interface Stage {
  id: string
  title: string
  icon: IconName
  state: string
  tone: BadgeTone
  detail: string
  link: string
  linkLabel: string
}

function StageCard({ stage, index }: { stage: Stage; index: number }) {
  return (
    <article className="slice-stage">
      <div className="slice-stage__index" aria-hidden="true">{index}</div>
      <div className="slice-stage__body">
        <div className="slice-stage__head">
          <span className="slice-stage__icon"><Icon name={stage.icon} size={16} /></span>
          <h3>{stage.title}</h3>
          <Badge tone={stage.tone} icon={stage.tone === 'success' ? 'check' : 'circleDot'}>
            {stage.state}
          </Badge>
        </div>
        <p className="slice-stage__detail">{stage.detail}</p>
        <Link to={stage.link} className="text-link">
          {stage.linkLabel}
        </Link>
      </div>
    </article>
  )
}

export function InvoiceSlicePage() {
  const workflows = useWorkflows()
  const runs = useRuns({})
  const scenarios = useScenarios()
  const decisions = useDecisions(false)
  const recoveries = useRecoveries()
  const regressions = useRegressionTests()
  const captures = useCaptures()
  const audit = useAudit()

  const workflow = useMemo(
    () => (workflows.data ?? []).find((item) => item.slug === SLICE_SLUG) ?? null,
    [workflows.data],
  )
  const version = useVersion(workflow?.id ?? '', workflow?.current_version_id ?? null)

  const sliceRuns = useMemo(
    () => (runs.data ?? []).filter((run) => run.workflow_id === workflow?.id),
    [runs.data, workflow?.id],
  )
  const runIds = useMemo(() => new Set(sliceRuns.map((run) => run.id)), [sliceRuns])
  const sliceScenarios = useMemo(
    () => (scenarios.data ?? []).filter((item) => item.workflow_id === workflow?.id),
    [scenarios.data, workflow?.id],
  )
  const sliceDecisions = useMemo(
    () =>
      (decisions.data ?? []).filter(
        (item) => item.workflow_id === workflow?.id && openDecisionStatuses.has(item.status),
      ),
    [decisions.data, workflow?.id],
  )
  const sliceRecoveries = useMemo(
    () => (recoveries.data ?? []).filter((item) => runIds.has(item.run_id)),
    [recoveries.data, runIds],
  )
  const sliceRegressions = useMemo(
    () => (regressions.data ?? []).filter((item) => item.spec?.workflow_id === workflow?.id),
    [regressions.data, workflow?.id],
  )

  const latestVerified = useMemo(() => {
    const rows = sliceRuns
      .filter((run) => run.verdict === 'VERIFIED')
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    return rows[0] ?? null
  }, [sliceRuns])

  const latestRecovery = useMemo(() => {
    const rows = [...sliceRecoveries].sort(
      (a, b) => Date.parse(b.started_at) - Date.parse(a.started_at),
    )
    return rows[0] ?? null
  }, [sliceRecoveries])

  const scenarioRuns = useMemo(() => {
    const byScenario = new Map<string, (typeof sliceRuns)[number]>()
    for (const run of sliceRuns) {
      if (!run.fault_scenario_id) continue
      const existing = byScenario.get(run.fault_scenario_id)
      if (!existing || Date.parse(run.created_at) > Date.parse(existing.created_at)) {
        byScenario.set(run.fault_scenario_id, run)
      }
    }
    return byScenario
  }, [sliceRuns])

  const latestCapture = useMemo(() => {
    const rows = [...(captures.data ?? [])].sort(
      (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
    )
    return rows[0] ?? null
  }, [captures.data])

  const chaosTested = REQUIRED_SCENARIOS.filter((name) => {
    const scenario = sliceScenarios.find((item) => item.name === name)
    return scenario ? scenarioRuns.has(scenario.id) : false
  }).length

  const firstDecision = sliceDecisions[0] ?? null
  const firstRegression = sliceRegressions[0] ?? null
  const stages: Stage[] = []

  stages.push({
    id: 'learn',
    title: 'Learn the workflow',
    icon: 'play',
    state: latestCapture ? statusLabel(latestCapture.status) : 'Not recorded yet',
    tone: latestCapture
      ? latestCapture.status === 'SAVED'
        ? 'success'
        : 'info'
      : 'neutral',
    detail: latestCapture
      ? `Latest capture “${latestCapture.label}” · ${latestCapture.event_count} recorded interactions. Recording happens on the controlled demo site.`
      : 'Record the supported invoice workflow on the demo site, review the generated graph and save a version.',
    link: '/learning',
    linkLabel: latestCapture ? 'Open Learning Studio' : 'Record the workflow',
  })

  stages.push({
    id: 'normal',
    title: 'Normal execution',
    icon: 'activity',
    state: latestVerified ? 'Verified' : 'No verified run',
    tone: latestVerified ? 'success' : 'neutral',
    detail: latestVerified
      ? `Run ${shortId(latestVerified.id, 10)} finished ${statusLabel(latestVerified.status)} with ${latestVerified.event_count} recorded events.`
      : 'Start the published processing workflow and inspect the ordered timeline, inputs, outputs and final state.',
    link: latestVerified ? `/runs/${latestVerified.id}` : '/runs?new=1',
    linkLabel: latestVerified ? 'Inspect the verified run' : 'Start an execution',
  })

  stages.push({
    id: 'chaos',
    title: 'Chaos execution',
    icon: 'zap',
    state: `${chaosTested}/${REQUIRED_SCENARIOS.length} scenarios run`,
    tone: chaosTested === REQUIRED_SCENARIOS.length ? 'success' : chaosTested > 0 ? 'info' : 'neutral',
    detail: 'PO timeout, invoice-total mismatch, a lost response after the operation executed, and a missing approval.',
    link: '/chaos',
    linkLabel: 'Open Chaos Lab',
  })

  stages.push({
    id: 'decision',
    title: 'Human bottleneck',
    icon: 'inbox',
    state: firstDecision ? `${sliceDecisions.length} open` : 'No open decision',
    tone: firstDecision ? 'warning' : 'success',
    detail: firstDecision
      ? `${firstDecision.kind ?? 'Blocker'} · ${statusLabel(firstDecision.required_role ?? 'ADMIN')} must decide.`
      : 'When a genuine discrepancy blocks the run, the inbox asks the smallest question and ships the evidence packet.',
    link: firstDecision ? `/decisions?decision=${firstDecision.id}` : '/decisions',
    linkLabel: firstDecision ? 'Review the decision' : 'Open the Decision Inbox',
  })

  stages.push({
    id: 'recovery',
    title: 'Verified recovery',
    icon: 'rotate',
    state: latestRecovery ? statusLabel(latestRecovery.status) : 'No recovery attempt',
    tone: latestRecovery ? (outcomeTone(latestRecovery.status) as BadgeTone) : 'neutral',
    detail: latestRecovery
      ? `${latestRecovery.strategy} · ${latestRecovery.verification?.checks?.length ?? 0} contract checks · attempt ${shortId(latestRecovery.id, 8)}.`
      : 'A recoverable failure restores a verified checkpoint, applies the declared strategy and re-checks the contract.',
    link: latestRecovery ? `/runs/${latestRecovery.run_id}` : '/recovery',
    linkLabel: latestRecovery ? 'Inspect the recovery' : 'Open Recovery & Audit',
  })

  stages.push({
    id: 'regression',
    title: 'Regression testing',
    icon: 'file',
    state: sliceRegressions.length > 0 ? `${sliceRegressions.length} test(s)` : 'No regression test',
    tone:
      sliceRegressions.length > 0
        ? sliceRegressions.some((item) => item.last_result === 'PASSED')
          ? 'success'
          : 'warning'
        : 'neutral',
    detail: firstRegression
      ? `${firstRegression.name} · last result ${firstRegression.last_result ?? 'not replayed'}.`
      : 'Turn a confirmed failure into a structured replay so it cannot recur silently.',
    link: '/recovery',
    linkLabel: 'Open regression tests',
  })

  stages.push({
    id: 'audit',
    title: 'Audit trail',
    icon: 'shield',
    state: audit.data ? `${audit.data.valid}/${audit.data.checked} chains valid` : 'Not checked',
    tone: audit.data ? (audit.data.broken > 0 ? 'danger' : 'success') : 'neutral',
    detail: audit.data
      ? audit.data.broken > 0
        ? `${audit.data.broken} chain(s) failed recomputation.`
        : 'Every recent run recomputes to its recorded head hash.'
      : 'Recompute the hash chain for recent runs.',
    link: '/recovery',
    linkLabel: 'Verify chains',
  })

  const loading =
    workflows.isPending ||
    runs.isPending ||
    scenarios.isPending ||
    decisions.isPending ||
    recoveries.isPending ||
    regressions.isPending
  const error =
    workflows.error ?? runs.error ?? scenarios.error ?? decisions.error ?? recoveries.error

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Demonstrate"
        title="Invoice processing slice"
        description="One workflow across every module. Every value below is read from the backend; nothing here is a scripted status."
        actions={
          <>
            {workflow ? (
              <Link to={`/workflows/${workflow.id}`} className={buttonClass('secondary', 'md')}>
                <Icon name="workflow" size={15} />
                <span>Open workflow</span>
              </Link>
            ) : null}
            <Link to="/runs?new=1" className={buttonClass('primary', 'md')}>
              <Icon name="activity" size={15} />
              <span>Run the workflow</span>
            </Link>
          </>
        }
      />

      {error ? (
        <ErrorState error={error as Error} onRetry={() => void workflows.refetch()} />
      ) : loading ? (
        <LoadingState rows={6} label="Loading the slice" />
      ) : !workflow ? (
        <Card>
          <CardBody>
            <EmptyState
              icon="workflow"
              title="The slice workflow is not seeded"
              description="Seed the demonstration workspace from Settings, then reload this page."
              action={
                <Link to="/settings" className={buttonClass('primary', 'sm')}>
                  Open Settings
                </Link>
              }
            />
          </CardBody>
        </Card>
      ) : (
        <>
          <div className="grid grid--metrics">
            <Card className="slice-summary">
              <CardBody>
                <span className="fine-print">Workflow</span>
                <strong>{workflow.name}</strong>
                <span className="fine-print mono">{workflow.slug}</span>
              </CardBody>
            </Card>
            <Card className="slice-summary">
              <CardBody>
                <span className="fine-print">Version</span>
                <strong>v{workflow.latest_version ?? '—'}</strong>
                <Badge tone={outcomeTone(workflow.latest_status ?? '')}>
                  {statusLabel(workflow.latest_status ?? '—')}
                </Badge>
              </CardBody>
            </Card>
            <Card className="slice-summary">
              <CardBody>
                <span className="fine-print">Runs</span>
                <strong>{sliceRuns.length}</strong>
                <span className="fine-print">
                  {sliceRuns.filter((run) => run.verdict === 'VERIFIED').length} verified
                </span>
              </CardBody>
            </Card>
            <Card className="slice-summary">
              <CardBody>
                <span className="fine-print">Open decisions</span>
                <strong>{sliceDecisions.length}</strong>
                <span className="fine-print">for this workflow</span>
              </CardBody>
            </Card>
          </div>

          <div className="slice-layout">
            <div className="slice-stages">
              {stages.map((stage, index) => (
                <StageCard key={stage.id} stage={stage} index={index + 1} />
              ))}
            </div>

            <aside className="stack-md">
              <Card>
                <CardHeader
                  title="Required chaos scenarios"
                  subtitle="Run each one from the Chaos Lab. Status is the latest real execution."
                  icon="zap"
                />
                <CardBody>
                  <ul className="scenario-checklist">
                    {REQUIRED_SCENARIOS.map((name) => {
                      const scenario = sliceScenarios.find((item) => item.name === name)
                      const run = scenario ? scenarioRuns.get(scenario.id) : undefined
                      return (
                        <li key={name}>
                          <span className="scenario-checklist__name">{name}</span>
                          {run ? (
                            <Badge tone={outcomeTone(run.verdict)}>{statusLabel(run.status)}</Badge>
                          ) : (
                            <Badge tone="neutral">Not run yet</Badge>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                  <p className="fine-print">
                    A scenario that has not been executed shows “Not run yet”. The console does not
                    invent a result.
                  </p>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="Workflow graph" subtitle="The published node order" icon="workflow" />
                <CardBody>
                  {version.isPending ? (
                    <LoadingState rows={3} />
                  ) : (version.data?.actions ?? []).length === 0 ? (
                    <p className="fine-print">No published version is available.</p>
                  ) : (
                    <ol className="graph-list">
                      {version.data?.actions.map((action) => (
                        <li key={action.id}>
                          <span className="mono fine-print">{action.ordinal}</span>
                          <span>{action.label}</span>
                          <Badge tone="neutral" mono>{action.node_type ?? action.kind}</Badge>
                        </li>
                      ))}
                    </ol>
                  )}
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="How to follow this demo" icon="list" />
                <CardBody>
                  <ol className="demo-steps">
                    <li>
                      <strong>Learn</strong> — record the invoice flow in the Learning Studio,
                      review the graph and publish a version.
                    </li>
                    <li>
                      <strong>Run</strong> — start the workflow and inspect the ordered timeline and
                      final state.
                    </li>
                    <li>
                      <strong>Break</strong> — run the four chaos scenarios in the Chaos Lab.
                    </li>
                    <li>
                      <strong>Decide</strong> — approve the variance in the Decision Inbox.
                    </li>
                    <li>
                      <strong>Recover</strong> — recover the PO timeout and inspect the contract.
                    </li>
                    <li>
                      <strong>Guard</strong> — create and run a regression test.
                    </li>
                  </ol>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="Recent slice runs" icon="activity" />
                <CardBody>
                  {sliceRuns.length === 0 ? (
                    <p className="fine-print">No run yet.</p>
                  ) : (
                    <ul className="attempt-list">
                      {sliceRuns
                        .slice()
                        .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
                        .slice(0, 5)
                        .map((run) => (
                          <li key={run.id}>
                            <div className="attempt-list__head">
                              <Link to={`/runs/${run.id}`} className="text-link mono">
                                {shortId(run.id, 10)}
                              </Link>
                              <Badge tone={outcomeTone(run.verdict)}>{statusLabel(run.status)}</Badge>
                            </div>
                            <p className="fine-print">
                              {run.trigger} · {relativeTime(run.created_at)}
                              {run.failure_code ? ` · ${run.failure_code}` : ''}
                            </p>
                          </li>
                        ))}
                    </ul>
                  )}
                </CardBody>
              </Card>
            </aside>
          </div>
        </>
      )}
    </div>
  )
}