import { Link } from 'react-router-dom'
import type { RunSummary } from '../../api/types'
import { formatDuration, relativeTime, shortId, statusLabel } from '../../lib/format'
import { Icon } from '../Icon'
import { Badge, OriginBadge, StatusBadge, VerdictBadge } from '../ui/Badge'
import { DataTable, type Column } from '../ui/DataTable'
import { EmptyState } from '../ui/States'

function triggerLabel(trigger: string): string {
  if (trigger === 'DEMO') return 'Demo fixture'
  return statusLabel(trigger)
}

export function RunTable({
  runs,
  emptyAction,
  showWorkflow = true,
}: {
  runs: RunSummary[]
  emptyAction?: React.ReactNode
  showWorkflow?: boolean
}) {
  const columns: Column<RunSummary>[] = [
    {
      key: 'run',
      header: 'Execution',
      render: (run) => (
        <div className="cell-primary">
          <Link to={`/runs/${run.id}`} className="mono link">
            {shortId(run.id, 10)}
          </Link>
          <span className="cell-secondary">
            {showWorkflow ? run.workflow_name : `v${run.version ?? '—'}`} · {triggerLabel(run.trigger)}
          </span>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (run) => <StatusBadge status={run.status} />,
    },
    {
      key: 'verdict',
      header: 'Verdict',
      render: (run) => <VerdictBadge verdict={run.verdict} />,
    },
    {
      key: 'origin',
      header: 'Origin',
      render: (run) => <OriginBadge origin={run.origin} demo={run.demo} />,
    },
    {
      key: 'version',
      header: 'Version',
      render: (run) => (
        <span className="mono cell-muted">
          {run.version !== null ? `v${run.version}` : '—'}
          {run.failure_ordinal !== null ? <span className="cell-failure"> · fail @{run.failure_ordinal}</span> : null}
        </span>
      ),
    },
    {
      key: 'events',
      header: 'Events',
      align: 'right',
      render: (run) => <span className="mono cell-muted">{run.event_count}</span>,
    },
    {
      key: 'duration',
      header: 'Duration',
      align: 'right',
      render: (run) => (
        <span className="cell-muted" title={`Started ${run.started_at ?? '—'}`}>
          {formatDuration(run.started_at, run.ended_at)}
        </span>
      ),
    },
    {
      key: 'created',
      header: 'Created',
      align: 'right',
      render: (run) => (
        <span className="cell-muted" title={new Date(run.created_at).toLocaleString()}>
          {relativeTime(run.created_at)}
        </span>
      ),
    },
    {
      key: 'open',
      header: '',
      align: 'right',
      render: (run) => (
        <Link to={`/runs/${run.id}`} className="icon-btn icon-btn--sm" aria-label={`Open run ${run.id}`}>
          <Icon name="chevronRight" size={15} />
        </Link>
      ),
    },
  ]

  return (
    <DataTable
      columns={columns}
      rows={runs}
      getRowKey={(run) => run.id}
      caption="Execution runs"
      empty={
        <EmptyState
          icon="activity"
          title="No executions yet"
          description="Start a published workflow to create a run. Every transition will appear here with a verified audit chain."
          action={emptyAction}
        />
      }
    />
  )
}

export function TriggerBadge({ trigger }: { trigger: string }) {
  if (trigger === 'DEMO') return <Badge tone="accent">Demo fixture</Badge>
  if (trigger === 'CHAOS') return <Badge tone="warning" icon="zap">Chaos</Badge>
  if (trigger === 'REGRESSION') return <Badge tone="info" icon="rotate">Regression</Badge>
  if (trigger === 'SCHEDULED') return <Badge tone="info" icon="clock">Scheduled</Badge>
  return <Badge tone="neutral" icon="play">Manual</Badge>
}