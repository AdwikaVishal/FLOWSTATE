import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, evidenceUrl, recoveryStrategies } from '../api/client'
import { useDecisions, useRegressionTests, useRun, useRunEventStream, useRunRecoveries, useRuns, useTimeline } from '../api/queries'
import type { RecoveryStrategy } from '../api/types'
import { ActionTable } from '../components/domain/ActionTable'
import { EventTimeline } from '../components/domain/EventTimeline'
import { NodeStateTable } from '../components/domain/NodeStateTable'
import { RunTable } from '../components/domain/RunTable'
import { VerdictList } from '../components/domain/VerdictList'
import { Icon } from '../components/Icon'
import { Badge, OriginBadge, StatusBadge, VerdictBadge } from '../components/ui/Badge'
import { Button, buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader, MetricCard } from '../components/ui/Card'
import { CopyButton } from '../components/ui/CopyButton'
import { FormField, Select } from '../components/ui/Fields'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, InlineNotice, LoadingState, PageSkeleton } from '../components/ui/States'
import { TabPanel, Tabs } from '../components/ui/Tabs'
import {
  formatDate,
  formatDuration,
  outcomeTone,
  pluralize,
  relativeTime,
  shortHash,
  shortId,
  statusLabel,
} from '../lib/format'

const recoverable = new Set(['AWAITING_RECOVERY', 'FAILED', 'TIMED_OUT'])

export function RunDetailPage() {
  const { runId = '' } = useParams()
  const run = useRun(runId)
  const timeline = useTimeline(runId)
  const liveConnection = useRunEventStream(
    runId,
    run.data?.events.at(-1)?.seq ?? -1,
    Boolean(run.data),
  )
  const decisions = useDecisions(false)
  const recoveries = useRunRecoveries(runId)
  const regressions = useRegressionTests()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState('timeline')
  const [recoverOpen, setRecoverOpen] = useState(false)
  const [strategy, setStrategy] = useState<RecoveryStrategy>('RESUME')
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const decision = useMemo(
    () => decisions.data?.find((item) => item.run_id === runId) ?? null,
    [decisions.data, runId],
  )
  const relatedRegressions = useMemo(
    () => (regressions.data ?? []).filter((test) => test.source_run_id === runId),
    [regressions.data, runId],
  )
  const plan = useQuery({
    queryKey: ['runs', runId, 'recovery-plan'] as const,
    queryFn: ({ signal }) => api.recoveryPlan(runId, signal),
    enabled: Boolean(runId),
  })
  const allowedStrategies: RecoveryStrategy[] =
    plan.data?.supported && plan.data.allowed_strategies.length > 0
      ? (plan.data.allowed_strategies as RecoveryStrategy[])
      : recoveryStrategies

  const recover = useMutation({
    mutationFn: () => api.recoverRun(runId, strategy),
    onSuccess: (attempt) => {
      setRecoverOpen(false)
      setActionError(null)
      setNotice(
        attempt.status === 'VERIFIED'
          ? `Recovery ${shortId(attempt.id, 8)} restored a checkpoint and the run verified.`
          : `Recovery ${shortId(attempt.id, 8)} finished as ${statusLabel(attempt.status)}. The run was not declared verified.`,
      )
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['runs', runId] })
      void queryClient.invalidateQueries({ queryKey: ['runs', runId, 'recoveries'] })
      void queryClient.invalidateQueries({ queryKey: ['recoveries'] })
      void queryClient.invalidateQueries({ queryKey: ['audit'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const cancelRun = useMutation({
    mutationFn: () => api.cancelRun(runId, 'Cancelled from the console'),
    onSuccess: () => {
      setNotice('Run cancelled at a safe node boundary. Pending and blocked nodes were cancelled, not retried.')
      setActionError(null)
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['runs', runId] })
      void queryClient.invalidateQueries({ queryKey: ['decisions'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const createRegression = useMutation({
    mutationFn: () => api.createRegression(runId),
    onSuccess: (test) => {
      setNotice(`Regression test “${test.name}” created from this run.`)
      setActionError(null)
      void queryClient.invalidateQueries({ queryKey: ['regression-tests'] })
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const verifyChain = useMutation({
    mutationFn: () => api.verifyRun(runId),
    onSuccess: (result) => {
      setNotice(
        result.valid
          ? `Chain verified: ${pluralize(result.event_count, 'event')} recomputed from the genesis hash.`
          : `Chain verification failed at event #${result.broken_at_seq ?? '—'}.`,
      )
      setActionError(null)
      setTab('audit')
    },
    onError: (error: Error) => setActionError(error.message),
  })

  if (run.isPending) return <PageSkeleton />
  if (run.isError) {
    return (
      <div className="stack-lg">
        <PageHeader title="Execution" description="Run inspector." />
        <ErrorState error={run.error} onRetry={() => void run.refetch()} />
        <Link to="/runs" className={buttonClass('secondary', 'md')}>
          <Icon name="arrowLeft" size={15} />
          <span>Back to Execution Inspector</span>
        </Link>
      </div>
    )
  }

  const data = run.data
  const canRecover = recoverable.has(data.status)
  const terminal = ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(data.status)
  const canCancel = !terminal

  return (
    <div className="stack-lg">
      <div className="back-row">
        <Link to="/runs" className="text-link">
          <Icon name="arrowLeft" size={14} />
          Execution Inspector
        </Link>
      </div>

      <PageHeader
        eyebrow={`${data.workflow_name} · v${data.version ?? '—'}`}
        title={`Execution ${shortId(data.id, 12)}`}
        description={
          data.failure_message ??
          (data.status === 'AWAITING_DECISION'
            ? 'This execution is paused at an authorization-gated action. A human must decide before it continues.'
            : 'A single execution record with its ordered audit chain and verification verdict.')
        }
        meta={
          <div className="page-meta">
            <StatusBadge status={data.status} />
            <VerdictBadge verdict={data.verdict} />
            <Badge
              tone={liveConnection === 'connected' ? 'success' : liveConnection === 'error' ? 'danger' : 'warning'}
            >
              Live updates: {liveConnection}
            </Badge>
            <OriginBadge origin={data.origin} demo={data.demo} />
            <Badge tone={data.trigger === 'CHAOS' ? 'warning' : 'neutral'} icon={data.trigger === 'CHAOS' ? 'zap' : 'play'}>
              {statusLabel(data.trigger)}
            </Badge>
            <span><Icon name="clock" size={13} /> {formatDate(data.created_at)} ({relativeTime(data.created_at)})</span>
            <span className="mono">seed {data.seed}</span>
            <span className="mono" title={data.id}>{data.id}</span>
            <CopyButton value={data.id} label="Copy run id" />
          </div>
        }
        actions={
          <>
            <Link to={`/workflows/${data.workflow_id}`} className={buttonClass('secondary', 'md')}>
              <Icon name="workflow" size={15} />
              <span>Open workflow</span>
            </Link>
            <Button
              variant="secondary"
              icon="shield"
              loading={verifyChain.isPending}
              onClick={() => verifyChain.mutate()}
            >
              Verify chain
            </Button>
            {data.verdict === 'FAILED' ? (
              <Button
                variant="secondary"
                icon="file"
                loading={createRegression.isPending}
                onClick={() => createRegression.mutate()}
              >
                Create regression
              </Button>
            ) : null}
            {canCancel ? (
              <Button
                variant="danger"
                icon="ban"
                loading={cancelRun.isPending}
                onClick={() => cancelRun.mutate()}
              >
                Cancel run
              </Button>
            ) : null}
            {canRecover ? (
              <Button variant="primary" icon="rotate" onClick={() => setRecoverOpen(true)}>
                Recover run
              </Button>
            ) : null}
          </>
        }
      />

      {data.demo ? (
        <InlineNotice tone="info" title="Seeded demonstration record" icon="flask">
          This execution is a fixture loaded with the demonstration workspace. It is tagged
          DEMO and excluded from live activity counts.
        </InlineNotice>
      ) : null}
      {notice ? <InlineNotice tone="success" title="Backend result">{notice}</InlineNotice> : null}
      {actionError ? <InlineNotice tone="danger" title="Action failed">{actionError}</InlineNotice> : null}
      {data.failure_code ? (
        <InlineNotice tone="danger" title={`Run failed: ${data.failure_code}`}>
          {data.failure_message ?? 'No failure detail was recorded.'}
          {data.failure_ordinal !== null ? ` Failure occurred at action ${data.failure_ordinal}.` : null}
        </InlineNotice>
      ) : null}

      <div className="grid grid--metrics">
        <MetricCard label="Events" value={data.event_count} hint="Append-only audit entries" icon="list" />
        <MetricCard
          label="Duration"
          value={formatDuration(data.started_at, data.ended_at)}
          hint={terminal ? 'Terminal state' : 'Still open'}
          icon="clock"
        />
        <MetricCard
          label="Current ordinal"
          value={data.current_ordinal}
          hint={`${data.actions.length} actions in this version`}
          icon="layers"
        />
        <MetricCard
          label="Chain integrity"
          value={data.chain_valid ? 'Valid' : 'Broken'}
          hint={data.chain_valid ? 'Hash chain recomputed' : 'Inspect immediately'}
          icon="shield"
          tone={data.chain_valid ? 'success' : 'danger'}
        />
      </div>

      <div className="grid grid--main-rail">
        <Card>
          <CardHeader
            title="Run inspector"
            subtitle="The browser never computes a verdict; it renders what the backend recorded."
            action={<span className="mono fine-print">{data.driver}</span>}
            icon="terminal"
          />
          <CardBody>
            <Tabs
              ariaLabel="Run detail"
              active={tab}
              onChange={setTab}
              items={[
                { id: 'timeline', label: 'Timeline', count: data.events.length },
                { id: 'nodes', label: 'Nodes', count: data.node_states.length },
                { id: 'actions', label: 'Graph', count: data.actions.length },
                { id: 'invariants', label: 'Invariants', count: data.verdicts.length },
                { id: 'audit', label: 'Audit chain' },
              ]}
            >
              <TabPanel id="timeline" active={tab}>
                {timeline.data ? (
                  <EventTimeline
                    events={timeline.data.entries.map((entry) => ({
                      id: `entry-${entry.seq}`,
                      seq: entry.seq,
                      type: entry.type,
                      actor: entry.actor,
                      actor_id: entry.actor_id,
                      action_ordinal: entry.action_ordinal,
                      node_key: entry.node_key,
                      payload: entry.payload,
                      prev_hash: '',
                      hash: '',
                      recorded_at: entry.recorded_at,
                      summary: entry.summary,
                    }))}
                  />
                ) : (
                  <EventTimeline events={data.events} />
                )}
              </TabPanel>
              <TabPanel id="nodes" active={tab}>
                <div className="stack-md">
                  <dl className="kv kv--inline">
                    <div><dt>Nodes</dt><dd className="mono">{String(data.graph.nodes ?? data.node_states.length)}</dd></div>
                    <div><dt>Edges</dt><dd className="mono">{String(data.graph.edges ?? '—')}</dd></div>
                    <div><dt>Branches</dt><dd className="mono">{String(data.graph.branches ?? 0)}</dd></div>
                    <div><dt>Start</dt><dd className="mono">{String(data.graph.start ?? '—')}</dd></div>
                    <div><dt>End</dt><dd className="mono">{String(data.graph.end ?? '—')}</dd></div>
                  </dl>
                  <NodeStateTable nodes={data.node_states} />
                </div>
              </TabPanel>
              <TabPanel id="actions" active={tab}>
                <ActionTable actions={data.actions} events={data.events} />
              </TabPanel>
              <TabPanel id="invariants" active={tab}>
                <VerdictList verdicts={data.verdicts} />
              </TabPanel>
              <TabPanel id="audit" active={tab}>
                <div className="stack-md">
                  <dl className="kv kv--inline">
                    <div>
                      <dt>Chain status</dt>
                      <dd>{data.chain_valid ? <Badge tone="success" icon="check">Valid</Badge> : <Badge tone="danger" icon="x">Broken</Badge>}</dd>
                    </div>
                    <div><dt>Events</dt><dd className="mono">{data.event_count}</dd></div>
                    <div>
                      <dt>Head hash</dt>
                      <dd className="mono">
                        {shortHash(data.events.at(-1)?.hash)}
                        <CopyButton value={data.events.at(-1)?.hash ?? ''} label="Copy head hash" />
                      </dd>
                    </div>
                    <div><dt>Verification</dt><dd>{verifyChain.data ? (verifyChain.data.valid ? 'Recomputed · valid' : 'Recomputed · broken') : 'Not run this session'}</dd></div>
                  </dl>
                  {verifyChain.data ? (
                    <div className={`verify-result verify-result--${verifyChain.data.valid ? 'success' : 'danger'}`}>
                      <Icon name={verifyChain.data.valid ? 'check' : 'alert'} size={18} />
                      <div>
                        <strong>{verifyChain.data.valid ? 'Hash chain verified' : 'Hash chain broken'}</strong>
                        <p>
                          {verifyChain.data.valid
                            ? `${pluralize(verifyChain.data.event_count, 'event')} recomputed; head matches.`
                            : `Mismatch at sequence ${verifyChain.data.broken_at_seq}: ${verifyChain.data.reason ?? 'unknown reason'}`}
                        </p>
                        <span className="mono">{verifyChain.data.head_hash}</span>
                      </div>
                    </div>
                  ) : (
                    <EmptyState
                      icon="shield"
                      title="Recompute the chain"
                      description="Verification re-hashes every event in order and compares the head. Nothing is mutated."
                      action={
                        <Button size="sm" icon="shield" loading={verifyChain.isPending} onClick={() => verifyChain.mutate()}>
                          Verify now
                        </Button>
                      }
                      compact
                    />
                  )}
                  <div className="fine-print">
                    The chain is append-only. A successful verification proves the stored event bodies
                    match the recorded hashes; it does not re-execute the workflow.
                  </div>
                </div>
              </TabPanel>
            </Tabs>
          </CardBody>
        </Card>

        <div className="stack-md">
          <Card>
            <CardHeader title="Run facts" icon="info" />
            <CardBody>
              <dl className="kv">
                <div><dt>Workflow</dt><dd><Link to={`/workflows/${data.workflow_id}`} className="text-link">{data.workflow_name}</Link></dd></div>
                <div><dt>Version</dt><dd className="mono">v{data.version ?? '—'}</dd></div>
                <div><dt>Version id</dt><dd className="mono" title={data.workflow_version_id}>{shortId(data.workflow_version_id, 12)}</dd></div>
                <div><dt>Trigger</dt><dd>{statusLabel(data.trigger)}</dd></div>
                <div><dt>Driver</dt><dd className="mono">{data.driver}</dd></div>
                <div><dt>Actor</dt><dd>{data.actor}</dd></div>
                <div><dt>Seed</dt><dd className="mono">{data.seed}</dd></div>
                <div><dt>Inputs</dt><dd className="mono mono-wrap">{Object.keys(data.inputs).length > 0 ? JSON.stringify(data.inputs) : '—'}</dd></div>
                <div><dt>Started</dt><dd>{formatDate(data.started_at)}</dd></div>
                <div><dt>Ended</dt><dd>{formatDate(data.ended_at)}</dd></div>
                <div><dt>Scenario</dt><dd className="mono">{data.fault_scenario_id ? shortId(data.fault_scenario_id, 12) : '—'}</dd></div>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Decision"
              subtitle="The minimum unresolved human choice."
              icon="lock"
              action={
                decision ? (
                  <Link to={`/decisions?decision=${decision.id}`} className="text-link">
                    Open inbox
                    <Icon name="arrowRight" size={13} />
                  </Link>
                ) : null
              }
            />
            <CardBody>
              {decisions.isPending ? (
                <LoadingState rows={2} label="Loading decision" />
              ) : decision ? (
                <div className="stack-sm">
                  <div className="row-actions">
                    <Badge tone={outcomeTone(decision.status)}>{statusLabel(decision.status)}</Badge>
                    <span className="mono fine-print">action {decision.action_ordinal}</span>
                  </div>
                  <p className="decision-question">{decision.question}</p>
                  <p className="fine-print">{decision.policy_citation}</p>
                  {decision.decided_by ? (
                    <p className="fine-print">Decided by {decision.decided_by} {relativeTime(decision.decided_at)}.</p>
                  ) : null}
                </div>
              ) : (
                <p className="fine-print">
                  No human decision is attached to this run. Only authorization-gated actions create one.
                </p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Recovery"
              subtitle="Only an action with an explicit, tested recovery policy can be recovered."
              icon="rotate"
              action={
                canRecover ? (
                  <Button
                    size="sm"
                    icon="rotate"
                    onClick={() => {
                      setStrategy(plan.data?.strategy ?? 'RESUME')
                      setRecoverOpen(true)
                    }}
                  >
                    Recover
                  </Button>
                ) : null
              }
            />
            <CardBody>
              {plan.data ? (
                <div className="recovery-plan">
                  <div className="recovery-plan__head">
                    <Badge tone={plan.data.supported ? 'info' : 'warning'} icon="shield">
                      {plan.data.supported ? 'Policy declared' : 'No automated recovery'}
                    </Badge>
                    <Badge tone="neutral" mono>{plan.data.strategy}</Badge>
                    <Badge tone={plan.data.reversible ? 'success' : 'danger'}>
                      {plan.data.reversible ? 'reversible' : 'irreversible'}
                    </Badge>
                    {plan.data.ambiguous ? <Badge tone="warning">ambiguous outcome</Badge> : null}
                    {plan.data.action_ordinal !== null ? (
                      <span className="fine-print mono">
                        ordinal {plan.data.action_ordinal}
                        {plan.data.node_key ? ` · ${plan.data.node_key}` : ''}
                      </span>
                    ) : null}
                  </div>
                  <p className="fine-print">
                    {plan.data.supported ? plan.data.notes || 'A recovery policy is declared.' : plan.data.reason}
                  </p>
                  {plan.data.contract.length > 0 ? (
                    <ul className="condition-list">
                      {plan.data.contract.map((condition, index) => (
                        <li key={`contract-${index}`}>
                          <Icon name="check" size={13} />
                          <span className="mono">
                            {String(condition.path ?? 'contract')} {String(condition.op ?? '')}{' '}
                            {JSON.stringify(condition.value ?? '')}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {plan.data.idempotency_path ? (
                    <p className="fine-print">
                      Idempotent retry on <span className="mono">{plan.data.idempotency_path}</span>
                    </p>
                  ) : null}
                </div>
              ) : null}
              {recoveries.isPending ? (
                <LoadingState rows={2} label="Loading recovery attempts" />
              ) : recoveries.data && recoveries.data.length > 0 ? (
                <ul className="attempt-list">
                  {recoveries.data.map((attempt) => (
                    <li key={attempt.id}>
                      <div className="attempt-list__head">
                        <Badge tone={outcomeTone(attempt.status)}>{statusLabel(attempt.status)}</Badge>
                        <span className="mono fine-print">{attempt.strategy}</span>
                      </div>
                      <p className="fine-print">
                        {attempt.steps.length} step{attempt.steps.length === 1 ? '' : 's'} · checkpoint{' '}
                        {attempt.checkpoint_id ? shortId(attempt.checkpoint_id, 8) : 'none'} · {relativeTime(attempt.started_at)}
                      </p>
                      {attempt.detail ? <p className="fine-print">{attempt.detail}</p> : null}
                      {(attempt.verification.checks ?? []).length > 0 ? (
                        <ul className="check-list">
                          {(attempt.verification.checks ?? []).map((check, index) => (
                            <li key={`${check.check}-${index}`} className={check.ok ? 'is-ok' : 'is-fail'}>
                              <Icon name={check.ok ? 'check' : 'x'} size={12} />
                              <div>
                                <span className="check-list__name mono">{check.check}</span>
                                <span className="fine-print">{check.detail}</span>
                              </div>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {attempt.evidence_id ? (
                        <a
                          className={buttonClass('ghost', 'sm')}
                          href={evidenceUrl(attempt.run_id, attempt.evidence_id)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <Icon name="file" size={13} />
                          <span>Verification evidence</span>
                        </a>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="fine-print">
                  {canRecover
                    ? 'No recovery has been attempted. A checkpoint restore can resume from the last safe point; verification decides whether it counts.'
                    : 'This run is not in a recoverable state.'}
                </p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Regression tests" icon="rotate" />
            <CardBody>
              {relatedRegressions.length === 0 ? (
                <div className="stack-sm">
                  <p className="fine-print">
                    {data.verdict === 'FAILED'
                      ? 'Generate a deterministic replay from this failure so it cannot recur silently.'
                      : 'Regression tests are generated from confirmed failures.'}
                  </p>
                  {data.verdict === 'FAILED' ? (
                    <Button size="sm" icon="file" loading={createRegression.isPending} onClick={() => createRegression.mutate()}>
                      Create regression
                    </Button>
                  ) : null}
                </div>
              ) : (
                <ul className="attempt-list">
                  {relatedRegressions.map((test) => (
                    <li key={test.id}>
                      <div className="attempt-list__head">
                        <strong>{test.name}</strong>
                        <Badge tone={outcomeTone(test.status)}>{statusLabel(test.status)}</Badge>
                      </div>
                      <p className="fine-print">
                        Last result: {test.last_result ?? 'not replayed'} · created {relativeTime(test.created_at)}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
              <Link to="/recovery" className="text-link">
                Open recovery & audit
                <Icon name="arrowRight" size={13} />
              </Link>
            </CardBody>
          </Card>
        </div>
      </div>

      {data.trigger === 'CHAOS' && data.fault_scenario_id ? (
        <Card>
          <CardHeader title="Related chaos executions" subtitle="Other runs of the same scenario family" icon="zap" />
          <CardBody className="card__body--flush">
            <RelatedChaosRuns scenarioId={data.fault_scenario_id} currentRunId={data.id} />
          </CardBody>
        </Card>
      ) : null}

      <Modal
        open={recoverOpen}
        onClose={() => setRecoverOpen(false)}
        title="Recover execution"
        description="The backend restores the latest usable checkpoint, applies the strategy and re-evaluates invariants. Only a verified result counts."
        footer={
          <>
            <Button variant="ghost" onClick={() => setRecoverOpen(false)}>Cancel</Button>
            <Button variant="primary" icon="rotate" loading={recover.isPending} onClick={() => recover.mutate()}>
              Run recovery
            </Button>
          </>
        }
      >
        <div className="stack-md">
          {actionError ? <InlineNotice tone="danger" title="Recovery failed">{actionError}</InlineNotice> : null}
          <FormField label="Strategy" htmlFor="recovery-strategy">
            <Select
              id="recovery-strategy"
              value={strategy}
              onChange={(event) => setStrategy(event.target.value as RecoveryStrategy)}
            >
              {allowedStrategies.map((item) => (
                <option key={item} value={item}>{statusLabel(item)}</option>
              ))}
            </Select>
          </FormField>
          <InlineNotice tone="warning" title="Verification is required">
            A restored checkpoint does not imply success. The recovery contract, the resume
            preconditions and every invariant are re-checked before the run can continue.
          </InlineNotice>
          {plan.data && !plan.data.supported ? (
            <InlineNotice tone="danger" title="No automated recovery">
              {plan.data.reason} A recovery attempt will be recorded as manual intervention.
            </InlineNotice>
          ) : null}
        </div>
      </Modal>
    </div>
  )
}

function RelatedChaosRuns({ scenarioId, currentRunId }: { scenarioId: string; currentRunId: string }) {
  const query = useRuns({})
  const related = (query.data ?? []).filter(
    (run) => run.fault_scenario_id === scenarioId && run.id !== currentRunId,
  )
  if (query.isPending) return <LoadingState rows={2} label="Loading related runs" />
  if (related.length === 0) {
    return (
      <EmptyState
        icon="zap"
        title="No other runs for this scenario"
        description="Run the scenario from Chaos Lab to compare outcomes under the same seed."
        compact
      />
    )
  }
  return <RunTable runs={related} showWorkflow={false} />
}