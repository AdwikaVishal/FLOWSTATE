import { Link } from 'react-router-dom'
import { useDemo, useOverview } from '../api/queries'
import { DemoBanner } from '../components/domain/DemoBanner'
import { RunTable } from '../components/domain/RunTable'
import { Icon } from '../components/Icon'
import { Badge, OriginBadge } from '../components/ui/Badge'
import { buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader, MetricCard } from '../components/ui/Card'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, PageSkeleton } from '../components/ui/States'
import { formatDate, formatPercent, pluralize, statusLabel } from '../lib/format'

const statusOrder = [
  'SUCCEEDED',
  'FAILED',
  'AWAITING_DECISION',
  'AWAITING_RECOVERY',
  'RUNNING',
  'VERIFYING',
  'TIMED_OUT',
  'PENDING',
]

const statusColors: Record<string, string> = {
  SUCCEEDED: 'var(--success)',
  FAILED: 'var(--danger)',
  AWAITING_DECISION: 'var(--warning)',
  AWAITING_RECOVERY: 'var(--warning)',
  RUNNING: 'var(--info)',
  VERIFYING: 'var(--info)',
  TIMED_OUT: 'var(--danger)',
  PENDING: 'var(--text-faint)',
}

function RunDistribution({ counts, total }: { counts: Record<string, number>; total: number }) {
  if (total === 0) {
    return (
      <EmptyState
        icon="activity"
        title="No run history"
        description="Status distribution appears once a workflow has executed."
        compact
      />
    )
  }
  const entries = statusOrder
    .map((status) => ({ status, count: counts[status] ?? 0 }))
    .filter((entry) => entry.count > 0)
  const other = Object.entries(counts)
    .filter(([status]) => !statusOrder.includes(status))
    .map(([status, count]) => ({ status, count }))

  return (
    <div className="distribution">
      <div className="distribution__bar" role="img" aria-label="Runs by status">
        {[...entries, ...other].map((entry) => (
          <span
            key={entry.status}
            className="distribution__segment"
            style={{
              width: `${(entry.count / total) * 100}%`,
              background: statusColors[entry.status] ?? 'var(--accent)',
            }}
            title={`${statusLabel(entry.status)}: ${entry.count}`}
          />
        ))}
      </div>
      <ul className="distribution__legend">
        {[...entries, ...other].map((entry) => (
          <li key={entry.status}>
            <span
              className="distribution__swatch"
              style={{ background: statusColors[entry.status] ?? 'var(--accent)' }}
            />
            <span>{statusLabel(entry.status)}</span>
            <strong>{entry.count}</strong>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function OverviewPage() {
  const overview = useOverview()
  const demo = useDemo()

  if (overview.isPending) return <PageSkeleton />
  if (overview.isError) {
    return (
      <div className="stack-lg">
        <PageHeader title="Operations overview" description="Live view of workflows, runs and audit integrity." />
        <ErrorState
          error={overview.error}
          title="The overview could not load"
          onRetry={() => void overview.refetch()}
        />
      </div>
    )
  }

  const data = overview.data
  const attention = data.counts.open_decisions + data.counts.invariant_violations
  const failedRuns = data.recent_runs.filter((run) => run.status === 'FAILED')
  const pausedRuns = data.recent_runs.filter((run) => run.status === 'AWAITING_DECISION')

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Control plane"
        title="Operations overview"
        description="What is running, what is verified, and what needs a human. Every number is read from the backend audit store."
        meta={
          <div className="page-meta">
            <span>
              <Icon name="clock" size={13} /> Updated {formatDate(data.generated_at)}
            </span>
            <span>
              <Icon name="database" size={13} /> {data.database_healthy ? 'Database reachable' : 'Database degraded'}
            </span>
            <OriginBadge origin="LIVE" />
          </div>
        }
        actions={
          <>
            <button
              className={buttonClass('ghost', 'md')}
              onClick={() => void overview.refetch()}
              disabled={overview.isFetching}
            >
              <Icon name="refresh" size={15} />
              <span>{overview.isFetching ? 'Refreshing…' : 'Refresh'}</span>
            </button>
            <Link to="/runs?new=1" className={buttonClass('primary', 'md')}>
              <Icon name="play" size={15} />
              <span>Run a workflow</span>
            </Link>
          </>
        }
      />

      <DemoBanner />

      <div className="grid grid--metrics">
        <MetricCard
          label="Executions"
          value={data.counts.runs}
          hint={`${data.counts.demo_runs} demo fixtures · ${data.counts.live_runs} live`}
          icon="activity"
          to="/runs"
        />
        <MetricCard
          label="Verified success"
          value={formatPercent(data.terminal.success_rate)}
          hint={`${data.terminal.verified} of ${pluralize(data.terminal.total, 'terminal run')}`}
          icon="shield"
          tone={data.terminal.success_rate === 1 ? 'success' : 'default'}
          to="/recovery"
        />
        <MetricCard
          label="Open decisions"
          value={data.counts.open_decisions}
          hint={data.counts.open_decisions > 0 ? 'Waiting for a human' : 'Nothing blocked'}
          icon="lock"
          tone={data.counts.open_decisions > 0 ? 'warning' : 'default'}
          to="/decisions"
        />
        <MetricCard
          label="Invariant violations"
          value={data.counts.invariant_violations}
          hint={data.counts.invariant_violations > 0 ? 'Across stored verdicts' : 'No recorded violations'}
          icon="alert"
          tone={data.counts.invariant_violations > 0 ? 'danger' : 'default'}
          to="/chaos"
        />
        <MetricCard
          label="Workflows"
          value={data.counts.workflows}
          hint={`${data.counts.published_versions} published versions`}
          icon="workflow"
          to="/workflows"
        />
        <MetricCard
          label="Audit integrity"
          value={`${data.audit.valid}/${data.audit.checked}`}
          hint={data.audit.broken > 0 ? `${data.audit.broken} broken chains` : 'Chains recomputed and valid'}
          icon="shield"
          tone={data.audit.broken > 0 ? 'danger' : 'success'}
          to="/recovery"
        />
      </div>

      <div className="grid grid--main-rail">
        <Card>
          <CardHeader
            title="Recent executions"
            subtitle="Newest runs first. Demo fixtures are labelled and never mixed with live counts."
            action={
              <Link to="/runs" className="text-link">
                All executions
                <Icon name="arrowRight" size={13} />
              </Link>
            }
            icon="activity"
          />
          <CardBody className="card__body--flush">
            <RunTable
              runs={data.recent_runs}
              emptyAction={
                <Link to="/runs?new=1" className={buttonClass('primary', 'sm')}>
                  <Icon name="play" size={14} />
                  <span>Start the first run</span>
                </Link>
              }
            />
          </CardBody>
        </Card>

        <div className="stack-md">
          <Card>
            <CardHeader
              title="Needs attention"
              subtitle={attention > 0 ? 'Ordered by operational impact.' : 'The queue is clear.'}
              icon="alert"
            />
            <CardBody className="stack-sm">
              {attention === 0 && failedRuns.length === 0 && pausedRuns.length === 0 ? (
                <div className="clear-state">
                  <span className="clear-state__icon">
                    <Icon name="check" size={18} />
                  </span>
                  <div>
                    <strong>No open incidents</strong>
                    <p>No blocked decisions, failed runs in the recent window, or invariant violations.</p>
                  </div>
                </div>
              ) : null}

              {data.counts.open_decisions > 0 ? (
                <Link to="/decisions" className="attention-row">
                  <span className="attention-row__icon attention-row__icon--warning">
                    <Icon name="lock" size={15} />
                  </span>
                  <span className="attention-row__text">
                    <strong>{pluralize(data.counts.open_decisions, 'decision')} waiting</strong>
                    <small>A run is paused until a human authorizes it.</small>
                  </span>
                  <Icon name="chevronRight" size={14} />
                </Link>
              ) : null}

              {data.counts.invariant_violations > 0 ? (
                <Link to="/chaos" className="attention-row">
                  <span className="attention-row__icon attention-row__icon--danger">
                    <Icon name="alert" size={15} />
                  </span>
                  <span className="attention-row__text">
                    <strong>{pluralize(data.counts.invariant_violations, 'invariant violation')}</strong>
                    <small>Inspect the chaos scenario and its evidence.</small>
                  </span>
                  <Icon name="chevronRight" size={14} />
                </Link>
              ) : null}

              {failedRuns.slice(0, 2).map((run) => (
                <Link to={`/runs/${run.id}`} className="attention-row" key={run.id}>
                  <span className="attention-row__icon attention-row__icon--danger">
                    <Icon name="x" size={15} />
                  </span>
                  <span className="attention-row__text">
                    <strong>{run.failure_code ?? 'Run failed'}</strong>
                    <small>{run.workflow_name} · {run.failure_message?.slice(0, 80)}</small>
                  </span>
                  <Icon name="chevronRight" size={14} />
                </Link>
              ))}

              {pausedRuns.slice(0, 2).map((run) => (
                <Link to={`/runs/${run.id}`} className="attention-row" key={run.id}>
                  <span className="attention-row__icon attention-row__icon--warning">
                    <Icon name="pause" size={15} />
                  </span>
                  <span className="attention-row__text">
                    <strong>Execution paused</strong>
                    <small>{run.workflow_name} is awaiting a decision.</small>
                  </span>
                  <Icon name="chevronRight" size={14} />
                </Link>
              ))}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Runs by status" subtitle="Current audit store" icon="columns" />
            <CardBody>
              <RunDistribution counts={data.runs_by_status} total={data.counts.runs} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Execution boundary"
              subtitle="Where actions actually run"
              action={<Badge tone="info" mono>{data.driver.name}</Badge>}
              icon="terminal"
            />
            <CardBody className="stack-sm">
              <dl className="kv">
                <div>
                  <dt>Driver</dt>
                  <dd className="mono">{data.driver.name}</dd>
                </div>
                <div>
                  <dt>Kind</dt>
                  <dd>{data.driver.kind}</dd>
                </div>
                <div>
                  <dt>Browser</dt>
                  <dd>{data.driver.browser ? 'Yes' : 'No'}</dd>
                </div>
              </dl>
              {data.driver.note ? <p className="fine-print">{data.driver.note}</p> : null}
              <Link to="/settings" className="text-link">
                Inspect system health
                <Icon name="arrowRight" size={13} />
              </Link>
            </CardBody>
          </Card>

          {!demo.data?.seeded ? (
            <Card>
              <CardHeader title="Demonstration workspace" subtitle="No fixtures loaded" icon="flask" />
              <CardBody>
                <p className="fine-print">
                  Seed the labelled invoice-approval workspace from Settings to explore the console
                  without executing a live workflow.
                </p>
                <Link to="/settings" className={buttonClass('secondary', 'sm')}>
                  Manage fixtures
                </Link>
              </CardBody>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  )
}