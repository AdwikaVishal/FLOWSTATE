import type { NodeState } from '../../api/types'
import { formatDate, statusLabel } from '../../lib/format'
import { Icon } from '../Icon'
import { Badge } from '../ui/Badge'
import { DataTable, type Column } from '../ui/DataTable'
import { EmptyState } from '../ui/States'

function nodeIcon(nodeType: string): 'play' | 'check' | 'activity' | 'shield' | 'gitBranch' | 'lock' | 'database' | 'rotate' {
  switch (nodeType) {
    case 'START':
      return 'play'
    case 'END':
      return 'check'
    case 'VALIDATION':
      return 'shield'
    case 'BRANCH':
      return 'gitBranch'
    case 'HUMAN_DECISION':
      return 'lock'
    case 'CHECKPOINT':
      return 'database'
    case 'RECOVERY_HANDLER':
      return 'rotate'
    default:
      return 'activity'
  }
}

export function NodeStateTable({ nodes }: { nodes: NodeState[] }) {
  const columns: Column<NodeState>[] = [
    {
      key: 'node',
      header: 'Node',
      render: (node) => (
        <div className="cell-primary">
          <span className="row-actions">
            <Icon name={nodeIcon(node.node_type)} size={13} />
            <strong>{node.label}</strong>
          </span>
          <span className="cell-secondary mono">
            {node.node_key}
            {node.depends_on.length > 0 ? ` ← ${node.depends_on.join(', ')}` : ''}
          </span>
        </div>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      render: (node) => <Badge tone="neutral">{statusLabel(node.node_type)}</Badge>,
    },
    {
      key: 'status',
      header: 'Status',
      render: (node) => <Badge tone={nodeTone(node.status)}>{statusLabel(node.status)}</Badge>,
    },
    {
      key: 'attempts',
      header: 'Attempts',
      align: 'right',
      render: (node) => (
        <span className="mono cell-muted">
          {node.attempt}/{node.max_attempts}
          {node.reconciled ? <span title="Reconciled after an ambiguous outcome"> ·⟳</span> : null}
        </span>
      ),
    },
    {
      key: 'duration',
      header: 'Duration',
      align: 'right',
      render: (node) => (
        <span className="mono cell-muted">
          {node.duration_ms !== null ? `${node.duration_ms} ms` : '—'}
        </span>
      ),
    },
    {
      key: 'error',
      header: 'Error',
      render: (node) =>
        node.error_code ? (
          <div className="cell-primary">
            <span className="cell-failure mono">{node.error_class ?? node.error_code}</span>
            <span className="cell-secondary">{node.error_message}</span>
          </div>
        ) : (
          <span className="cell-muted">—</span>
        ),
    },
    {
      key: 'checkpoint',
      header: 'Checkpoint',
      render: (node) => (
        <span className="mono cell-muted">{node.checkpoint_id ? node.checkpoint_id.slice(0, 8) : '—'}</span>
      ),
    },
    {
      key: 'finished',
      header: 'Finished',
      align: 'right',
      render: (node) => (
        <span className="cell-muted">{node.ended_at ? formatDate(node.ended_at) : '—'}</span>
      ),
    },
  ]

  return (
    <DataTable
      columns={columns}
      rows={nodes}
      getRowKey={(node) => node.node_key}
      caption="Node states for this run"
      empty={
        <EmptyState
          icon="layers"
          title="No node states"
          description="This run has not started scheduling nodes."
          compact
        />
      }
    />
  )
}

function nodeTone(status: NodeState['status']): 'success' | 'danger' | 'warning' | 'info' | 'neutral' {
  switch (status) {
    case 'PASSED':
    case 'RECOVERED':
      return 'success'
    case 'FAILED':
      return 'danger'
    case 'BLOCKED':
      return 'warning'
    case 'SKIPPED':
    case 'CANCELLED':
      return 'neutral'
    case 'RUNNING':
      return 'info'
    default:
      return 'neutral'
  }
}
