import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, evidenceUrl } from '../api/client'
import {
  useAudit,
  useFailureModes,
  useRecoveries,
  useRegressionTests,
  useRuns,
} from '../api/queries'
import type {
  AuditOverview,
  ChainVerification,
  FailureMode,
  Recovery,
  RegressionCompare,
  RegressionTest,
} from '../api/types'
import { Icon } from '../components/Icon'
import { Badge } from '../components/ui/Badge'
import { Button, buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader, MetricCard } from '../components/ui/Card'
import { CopyButton } from '../components/ui/CopyButton'
import { DataTable, type Column } from '../components/ui/DataTable'
import { FormField, TextArea, TextInput } from '../components/ui/Fields'
import { PageHeader } from '../components/ui/PageHeader'
import {
  EmptyState,
  ErrorState,
  InlineNotice,
  LoadingState,
  PageSkeleton,
} from '../components/ui/States'
import {
  formatDate,
  outcomeTone,
  pluralize,
  relativeTime,
  shortHash,
  shortId,
  statusLabel,
} from '../lib/format'

const recoverableStatuses = new Set(['AWAITING_RECOVERY', 'FAILED', 'TIMED_OUT'])

const failureTone = {
  RESOLVED: 'success',
  STILL_FAILING: 'danger',
  UNTESTED: 'neutral',
} as const

function Checks({ checks }: { checks: { check: string; ok: boolean; detail: string }[] }) {
  if (checks.length === 0) return null
  return (
    <ul className="check-list">
      {checks.map((check, index) => (
        <li key={`${check.check}-${index}`} className={check.ok ? 'is-ok' : 'is-fail'}>
          <Icon name={check.ok ? 'check' : 'x'} size={13} />
          <div>
            <span className="check-list__name mono">{check.check}</span>
            <span className="fine-print">{check.detail}</span>
          </div>
        </li>
      ))}
    </ul>
  )
}

function RecoveryCard({ attempt }: { attempt: Recovery }) {
  const checks = attempt.verification.checks ?? []
  const probe = attempt.verification.probe as
    | { effect_present?: boolean; mechanism?: string; detail?: string }
    | null
    | undefined

  return (
    <article className="recovery-card">
      <div className="recovery-card__head">
        <div>
          <div className="row-actions">
            <Badge tone={outcomeTone(attempt.status)}>{statusLabel(attempt.status)}</Badge>
            <Badge tone="neutral" mono>{attempt.strategy}</Badge>
            {attempt.failure_code ? (
              <Badge tone="danger" mono>{attempt.failure_code}</Badge>
            ) : null}
            <span className="fine-print">{relativeTime(attempt.started_at)}</span>
          </div>
          <p className="recovery-card__detail">{attempt.detail}</p>
        </div>
        <Link to={`/runs/${attempt.run_id}`} className="text-link mono">
          run {shortId(attempt.run_id, 10)}
        </Link>
      </div>

      <div className="recovery-card__grid">
        <dl className="kv kv--inline">
          <div>
            <dt>Action</dt>
            <dd>
              ordinal {attempt.action_ordinal ?? '—'}
              {attempt.node_key ? <span className="fine-print mono"> {attempt.node_key}</span> : null}
            </dd>
          </div>
          <div>
            <dt>Checkpoint</dt>
            <dd className="mono">{attempt.checkpoint_id ? shortId(attempt.checkpoint_id, 10) : '—'}</dd>
          </div>
          <div>
            <dt>Before</dt>
            <dd className="mono" title={attempt.before_state_hash ?? ''}>
              {attempt.before_state_hash ? shortHash(attempt.before_state_hash) : '—'}
            </dd>
          </div>
          <div>
            <dt>After</dt>
            <dd className="mono" title={attempt.after_state_hash ?? ''}>
              {attempt.after_state_hash ? shortHash(attempt.after_state_hash) : '—'}
            </dd>
          </div>
        </dl>

        {probe ? (
          <div className="recovery-probe">
            <Badge tone={probe.effect_present ? 'warning' : 'success'} icon="search">
              {probe.effect_present ? 'effect present — not retried' : 'effect absent — idempotent retry'}
            </Badge>
            <span className="fine-print">{probe.mechanism}{probe.detail ? ` · ${probe.detail}` : ''}</span>
          </div>
        ) : null}
      </div>

      <Checks checks={checks} />

      {attempt.manual_note ? (
        <InlineNotice tone="warning" title="Manual note">
          {attempt.manual_note}
        </InlineNotice>
      ) : null}

      <div className="row-actions recovery-card__footer">
        <span className="fine-print">{pluralize(attempt.steps.length, 'step')}</span>
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
        <CopyButton value={attempt.id} label="Copy recovery id" />
      </div>
    </article>
  )
}

function FailureModeRow({ mode }: { mode: FailureMode }) {
  return (
    <article className="failure-mode">
      <div className="failure-mode__head">
        <div>
          <div className="row-actions">
            <Badge tone={failureTone[mode.status]} icon={mode.status === 'RESOLVED' ? 'check' : 'alert'}>
              {mode.status.replace('_', ' ')}
            </Badge>
            <strong>{mode.failure_mode}</strong>
            {mode.scenario_fault_type ? (
              <Badge tone="neutral" mono>{mode.scenario_fault_type}</Badge>
            ) : null}
          </div>
          <p className="fine-print">
            {mode.workflow_name} · detected in v{mode.detected_in_version ?? '—'} · current v
            {mode.current_version ?? '—'}
          </p>
        </div>
        <Link to={`/workflows/${mode.workflow_id}`} className="text-link">
          Open workflow
        </Link>
      </div>
      <div className="failure-mode__versions">
        {mode.reproduces_in.length > 0 ? (
          <span className="fine-print">
            reproduces in v{mode.reproduces_in.join(', v')}
          </span>
        ) : null}
        {mode.resolved_in.length > 0 ? (
          <Badge tone="success">resolved in v{mode.resolved_in.join(', v')}</Badge>
        ) : null}
        {mode.status === 'UNTESTED' ? (
          <span className="fine-print">run the regression suite to compare versions</span>
        ) : null}
      </div>
    </article>
  )
}

function RegressionCard({ test }: { test: RegressionTest }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(test.name)
  const [notes, setNotes] = useState(test.notes)
  const [expected, setExpected] = useState(test.spec.expected.description)
  const [compare, setCompare] = useState<RegressionCompare | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['regression-tests'] })
    void queryClient.invalidateQueries({ queryKey: ['feedback', 'failure-modes'] })
    void queryClient.invalidateQueries({ queryKey: ['runs'] })
  }

  const save = useMutation({
    mutationFn: () =>
      api.updateRegression(test.id, {
        name: name.trim(),
        notes,
        expected_description: expected,
      }),
    onSuccess: () => {
      setNotice('Review saved. Activate the test when you are ready for it to run.')
      setError(null)
      refresh()
    },
    onError: (err: Error) => setError(err.message),
  })

  const activate = useMutation({
    mutationFn: () => api.activateRegression(test.id),
    onSuccess: () => {
      setNotice('Regression test activated.')
      setError(null)
      refresh()
    },
    onError: (err: Error) => setError(err.message),
  })

  const replay = useMutation({
    mutationFn: () => api.replayRegression(test.id),
    onSuccess: (result) => {
      setNotice(`Pinned replay finished: ${result.last_result ?? 'no result'}.`)
      setError(null)
      refresh()
    },
    onError: (err: Error) => setError(err.message),
  })

  const runCompare = useMutation({
    mutationFn: () => api.compareRegression(test.id),
    onSuccess: (result) => {
      setCompare(result)
      setNotice(
        `Compared across ${pluralize(result.results.length, 'version')}. Resolved in v${result.resolved_in.join(', v') || '—'}.`,
      )
      setError(null)
      refresh()
    },
    onError: (err: Error) => setError(err.message),
  })

  const spec = test.spec
  const dirty =
    name !== test.name || notes !== test.notes || expected !== spec.expected.description

  return (
    <article className="regression-card">
      <div className="regression-card__head">
        <div>
          <div className="row-actions">
            <Badge tone={outcomeTone(test.status)}>{statusLabel(test.status)}</Badge>
            <Badge tone="neutral" mono>v{spec.version ?? '—'}</Badge>
            {spec.scenario ? (
              <Badge tone="neutral" mono>{spec.scenario.fault_type}</Badge>
            ) : (
              <Badge tone="neutral">no scenario</Badge>
            )}
            <span className="fine-print">template v{spec.template_version}</span>
          </div>
          <h3 className="regression-card__title">{test.name}</h3>
          <p className="fine-print">
            {spec.workflow_name} · source run{' '}
            <Link to={`/runs/${test.source_run_id}`} className="text-link mono">
              {shortId(test.source_run_id, 8)}
            </Link>
          </p>
        </div>
        <div className="row-actions">
          <Button
            size="sm"
            icon="layers"
            loading={runCompare.isPending}
            onClick={() => runCompare.mutate()}
          >
            Compare versions
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon="rotate"
            loading={replay.isPending}
            onClick={() => replay.mutate()}
          >
            Replay
          </Button>
          {test.status !== 'ACTIVE' ? (
            <Button
              size="sm"
              variant="secondary"
              icon="check"
              loading={activate.isPending}
              onClick={() => activate.mutate()}
            >
              Activate
            </Button>
          ) : null}
        </div>
      </div>

      {error ? <InlineNotice tone="danger" title="Action failed">{error}</InlineNotice> : null}
      {notice ? <InlineNotice tone="success" title="Regression">{notice}</InlineNotice> : null}

      <div className="regression-card__grid">
        <div>
          <h4 className="subheading">Recorded failure</h4>
          <dl className="kv kv--inline">
            <div>
              <dt>Invariant</dt>
              <dd>{spec.expected.invariant_name ?? '—'}</dd>
            </div>
            <div>
              <dt>Observed</dt>
              <dd className="mono">{spec.observed.failure_code ?? '—'}</dd>
            </div>
            <div>
              <dt>Expected</dt>
              <dd>{spec.expected.description}</dd>
            </div>
            {spec.scenario ? (
              <div>
                <dt>Scenario</dt>
                <dd className="mono">
                  {spec.scenario.fault_type} @ ordinal {spec.scenario.target_ordinal} · seed{' '}
                  {spec.scenario.seed}
                </dd>
              </div>
            ) : null}
          </dl>
        </div>

        <div className="stack-sm">
          <h4 className="subheading">Review before saving</h4>
          <FormField label="Test name" htmlFor={`name-${test.id}`}>
            <TextInput
              id={`name-${test.id}`}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </FormField>
          <FormField label="Expected behaviour" htmlFor={`expected-${test.id}`}>
            <TextInput
              id={`expected-${test.id}`}
              value={expected}
              onChange={(event) => setExpected(event.target.value)}
            />
          </FormField>
          <FormField
            label="Reviewer notes"
            htmlFor={`notes-${test.id}`}
            hint={test.reviewed_by ? `Last reviewed by ${test.reviewed_by}` : 'Not yet reviewed'}
          >
            <TextArea
              id={`notes-${test.id}`}
              rows={2}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </FormField>
          <div className="row-actions">
            <Button
              size="sm"
              variant="primary"
              icon="check"
              disabled={!dirty && Boolean(test.reviewed_at)}
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              Save review
            </Button>
            <span className="fine-print">
              Saving records the review; it does not change the workflow.
            </span>
          </div>
        </div>
      </div>

      {compare ? (
        <div className="regression-matrix">
          <h4 className="subheading">Version comparison</h4>
          <div className="matrix">
            {compare.results.map((result) => (
              <div key={result.id} className={`matrix__cell matrix__cell--${result.result.toLowerCase()}`}>
                <strong>v{result.version}</strong>
                <Badge tone={result.result === 'PASSED' ? 'success' : 'danger'}>
                  {result.result}
                </Badge>
                <Link to={`/runs/${result.run_id}`} className="text-link mono fine-print">
                  {shortId(result.run_id, 8)}
                </Link>
                {result.violated_invariant ? (
                  <span className="fine-print">{result.violated_invariant}</span>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </article>
  )
}

export function RecoveryAuditPage() {
  const audit = useAudit()
  const recoveries = useRecoveries()
  const runs = useRuns({})
  const regressions = useRegressionTests()
  const failureModes = useFailureModes()
  const [verifications, setVerifications] = useState<Record<string, ChainVerification>>({})
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const recoverableRuns = useMemo(
    () => (runs.data ?? []).filter((run) => recoverableStatuses.has(run.status)),
    [runs.data],
  )
  const verifiedRecoveries = (recoveries.data ?? []).filter(
    (item) => item.status === 'VERIFIED',
  ).length
  const partialRecoveries = (recoveries.data ?? []).filter(
    (item) => item.status === 'PARTIAL',
  ).length
  const manualRecoveries = (recoveries.data ?? []).filter(
    (item) => item.status === 'MANUAL_INTERVENTION',
  ).length
  const resolvedModes = (failureModes.data ?? []).filter(
    (mode) => mode.status === 'RESOLVED',
  ).length

  const verify = useMutation({
    mutationFn: (runId: string) => api.verifyAudit(runId),
    onSuccess: (result) => {
      setVerifications((current) => ({ ...current, [result.run_id]: result }))
      setActionError(null)
      setNotice(
        result.valid
          ? `Run ${shortId(result.run_id, 8)} chain is valid across ${pluralize(result.event_count, 'event')}.`
          : `Run ${shortId(result.run_id, 8)} chain is broken at event #${result.broken_at_seq ?? '—'}.`,
      )
    },
    onError: (error: Error) => setActionError(error.message),
  })

  const rows = audit.data?.runs ?? []
  const auditColumns: Column<AuditOverview['runs'][number]>[] = [
    {
      key: 'run',
      header: 'Run',
      render: (row) => (
        <div className="cell-primary">
          <Link to={`/runs/${row.run_id}`} className="mono link">
            {shortId(row.run_id, 10)}
          </Link>
          <span className="cell-secondary">{row.workflow_name}</span>
        </div>
      ),
    },
    {
      key: 'events',
      header: 'Events',
      align: 'right',
      render: (row) => <span className="mono">{row.events}</span>,
    },
    {
      key: 'chain',
      header: 'Chain',
      render: (row) => {
        const result = verifications[row.run_id]
        return (
          <div className="cell-primary">
            {result ? (
              <Badge tone={result.valid ? 'success' : 'danger'} icon={result.valid ? 'check' : 'alert'}>
                {result.valid ? 'Recomputed · valid' : `Broken at ${result.broken_at_seq ?? '—'}`}
              </Badge>
            ) : (
              <Badge tone={row.valid ? 'success' : 'danger'} icon={row.valid ? 'check' : 'alert'}>
                {row.valid ? 'Valid' : 'Broken'}
              </Badge>
            )}
            <span className="cell-secondary mono" title={row.head_hash}>
              {shortHash(row.head_hash)}
            </span>
          </div>
        )
      },
    },
    {
      key: 'verify',
      header: '',
      align: 'right',
      render: (row) => (
        <Button
          size="sm"
          variant="ghost"
          icon="shield"
          loading={verify.isPending && verify.variables === row.run_id}
          onClick={() => verify.mutate(row.run_id)}
        >
          Verify
        </Button>
      ),
    },
  ]

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Operate"
        title="Recovery & Audit"
        description="Verified recovery, honest partial outcomes, and a feedback loop that proves whether later versions fix the failures we already found."
        actions={
          <>
            <Button
              variant="ghost"
              icon="refresh"
              loading={audit.isFetching}
              onClick={() => {
                setNotice(null)
                for (const query of [audit, recoveries, runs, regressions, failureModes]) {
                  void query.refetch()
                }
              }}
            >
              Recompute all chains
            </Button>
            {recoverableRuns.length > 0 ? (
              <Link to={`/runs/${recoverableRuns[0]?.id}`} className={buttonClass('primary', 'md')}>
                <Icon name="rotate" size={15} />
                <span>Recover a run</span>
              </Link>
            ) : null}
          </>
        }
      />

      {notice ? <InlineNotice tone="success" title="Audit result">{notice}</InlineNotice> : null}
      {actionError ? <InlineNotice tone="danger" title="Action failed">{actionError}</InlineNotice> : null}

      <div className="grid grid--metrics">
        <MetricCard label="Chains checked" value={audit.data?.checked ?? '—'} hint="Most recent runs" icon="shield" />
        <MetricCard
          label="Valid chains"
          value={audit.data?.valid ?? '—'}
          hint={audit.data?.broken ? `${audit.data.broken} broken` : 'No tampering detected'}
          icon="check"
          tone={audit.data?.broken ? 'danger' : 'success'}
        />
        <MetricCard
          label="Recovery verified"
          value={`${verifiedRecoveries}/${recoveries.data?.length ?? 0}`}
          hint={`${partialRecoveries} partial · ${manualRecoveries} manual`}
          icon="rotate"
          tone={partialRecoveries + manualRecoveries > 0 ? 'warning' : 'default'}
        />
        <MetricCard
          label="Failure modes resolved"
          value={resolvedModes}
          hint={`${failureModes.data?.length ?? 0} tracked across versions`}
          icon="layers"
          tone={resolvedModes > 0 ? 'success' : 'default'}
        />
      </div>

      <Card>
        <CardHeader
          title="Failure modes"
          subtitle="Detected failures and whether a later published version resolves them. Read-only: the console never rewrites a workflow."
          icon="layers"
          action={
            <Badge tone="neutral" mono>
              feedback loop
            </Badge>
          }
        />
        <CardBody>
          {failureModes.isError ? (
            <ErrorState error={failureModes.error} onRetry={() => void failureModes.refetch()} />
          ) : failureModes.isPending ? (
            <LoadingState rows={3} label="Loading failure modes" />
          ) : (failureModes.data ?? []).length === 0 ? (
            <EmptyState
              icon="layers"
              title="No failure modes tracked"
              description="Create a regression test from a failed run to start comparing versions."
              compact
            />
          ) : (
            <div className="failure-mode-list">
              {failureModes.data?.map((mode) => (
                <FailureModeRow key={mode.test_id} mode={mode} />
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      {audit.isError ? (
        <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
      ) : audit.isPending ? (
        <PageSkeleton />
      ) : (
        <Card>
          <CardHeader
            title="Audit chain integrity"
            subtitle="Verification re-hashes each event in sequence. It proves stored events match their hashes, not that the run was correct."
            icon="shield"
            action={<span className="fine-print">Checked {audit.data ? formatDate(audit.data.checked_at) : '—'}</span>}
          />
          <CardBody className="card__body--flush">
            <DataTable
              columns={auditColumns}
              rows={rows}
              getRowKey={(row) => row.run_id}
              caption="Audit chain status by run"
              empty={
                <EmptyState
                  icon="shield"
                  title="No runs to audit"
                  description="Chains appear here as soon as the engine records a run."
                  action={
                    <Link to="/runs?new=1" className={buttonClass('primary', 'sm')}>
                      Start a run
                    </Link>
                  }
                />
              }
            />
          </CardBody>
        </Card>
      )}

      <div className="grid grid--main-rail">
        <Card>
          <CardHeader
            title="Recovery attempts"
            subtitle="A recovery is only verified when the contract and the resumed run both pass."
            icon="rotate"
            action={<Badge tone="neutral">{pluralize(recoveries.data?.length ?? 0, 'attempt')}</Badge>}
          />
          <CardBody>
            {recoveries.isError ? (
              <ErrorState error={recoveries.error} onRetry={() => void recoveries.refetch()} />
            ) : recoveries.isPending ? (
              <LoadingState rows={4} label="Loading recoveries" />
            ) : (recoveries.data ?? []).length === 0 ? (
              <EmptyState
                icon="rotate"
                title="No recovery attempts"
                description="Recoverable failures appear in the run inspector with a declared strategy."
                compact
              />
            ) : (
              <div className="recovery-list">
                {recoveries.data?.map((attempt) => (
                  <RecoveryCard key={attempt.id} attempt={attempt} />
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        <div className="stack-md">
          <Card>
            <CardHeader
              title="Recoverable runs"
              subtitle="Failed, timed out or awaiting recovery."
              icon="alert"
              action={<Badge tone={recoverableRuns.length > 0 ? 'warning' : 'success'}>{recoverableRuns.length}</Badge>}
            />
            <CardBody>
              {runs.isPending ? (
                <LoadingState rows={2} />
              ) : recoverableRuns.length === 0 ? (
                <p className="fine-print">
                  No run is currently recoverable. Failed runs with a recovery policy appear here.
                </p>
              ) : (
                <ul className="attempt-list">
                  {recoverableRuns.slice(0, 6).map((run) => (
                    <li key={run.id}>
                      <div className="attempt-list__head">
                        <Link to={`/runs/${run.id}`} className="text-link mono">
                          {shortId(run.id, 10)}
                        </Link>
                        <Badge tone="danger">{run.failure_code ?? statusLabel(run.status)}</Badge>
                      </div>
                      <p className="fine-print">
                        {run.workflow_name} · ordinal {run.failure_ordinal ?? run.current_ordinal} ·{' '}
                        {relativeTime(run.created_at)}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="How recovery is verified" subtitle="Three checks, in order" icon="shield" />
            <CardBody>
              <ul className="condition-list">
                <li>
                  <Icon name="check" size={13} />
                  <span>Checkpoint hash matches the stored state</span>
                </li>
                <li>
                  <Icon name="check" size={13} />
                  <span>Recovery contract holds on the restored state</span>
                </li>
                <li>
                  <Icon name="check" size={13} />
                  <span>Resumed run passes its invariants</span>
                </li>
              </ul>
              <p className="fine-print">
                A partial compensation is reported as <strong>Partial</strong> and dependent actions do
                not resume.
              </p>
            </CardBody>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader
          title="Regression tests"
          subtitle="Structured, reviewable reproductions. Compare the same failure across published versions."
          icon="file"
          action={<Badge tone="neutral">{pluralize(regressions.data?.length ?? 0, 'test')}</Badge>}
        />
        <CardBody>
          {regressions.isError ? (
            <ErrorState error={regressions.error} onRetry={() => void regressions.refetch()} />
          ) : regressions.isPending ? (
            <LoadingState rows={3} label="Loading regression tests" />
          ) : (regressions.data ?? []).length === 0 ? (
            <EmptyState
              icon="file"
              title="No regression tests"
              description="Open a failed execution and create a regression test, or run a chaos scenario that violates an invariant."
              compact
            />
          ) : (
            <div className="regression-list">
              {regressions.data?.map((test) => (
                <RegressionCard key={test.id} test={test} />
              ))}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  )
}