import type { ActionDefinition, ExecutionEvent } from '../../api/types'
import { statusLabel } from '../../lib/format'
import { Badge } from '../ui/Badge'
import { DataTable, type Column } from '../ui/DataTable'
import { EmptyState } from '../ui/States'

function actionState(action: ActionDefinition, events: ExecutionEvent[]): string | null {
  const related = events.filter((event) => event.action_ordinal === action.ordinal)
  if (related.length === 0) return null
  const last = related[related.length - 1]
  if (!last) return null
  if (last.type === 'ACTION_FAILED') return 'FAILED'
  if (last.type === 'ACTION_CONFIRMED') return 'CONFIRMED'
  if (last.type === 'ACTION_UNKNOWN') return 'UNKNOWN'
  if (last.type === 'ACTION_SUCCEEDED') return 'SUCCEEDED'
  if (last.type === 'POSTCONDITION_FAILED' || last.type === 'PRECONDITION_FAILED') return 'FAILED'
  if (last.type === 'ACTION_STARTED') return 'STARTED'
  if (last.type === 'DECISION_REQUESTED') return 'BLOCKED'
  return null
}

const stateTone: Record<string, 'success' | 'danger' | 'warning' | 'info' | 'neutral'> = {
  SUCCEEDED: 'success',
  CONFIRMED: 'success',
  FAILED: 'danger',
  UNKNOWN: 'warning',
  STARTED: 'info',
  BLOCKED: 'warning',
}

export function ActionTable({
  actions,
  events,
  emptyDescription,
}: {
  actions: ActionDefinition[]
  events?: ExecutionEvent[]
  emptyDescription?: string
}) {
  const columns: Column<ActionDefinition>[] = [
    {
      key: 'ordinal',
      header: '#',
      width: '52px',
      render: (action) => <span className="mono cell-muted">{String(action.ordinal).padStart(2, '0')}</span>,
    },
    {
      key: 'action',
      header: 'Action',
      render: (action) => (
        <div className="cell-primary">
          <span>{action.label}</span>
          <span className="cell-secondary mono">
            {action.kind.toLowerCase()} · {action.timeout_ms} ms
          </span>
        </div>
      ),
    },
    {
      key: 'effect',
      header: 'Side effect',
      render: (action) => (
        <Badge
          tone={
            action.side_effect_class === 'IRREVERSIBLE'
              ? 'danger'
              : action.side_effect_class === 'COMPENSABLE'
                ? 'warning'
                : action.side_effect_class === 'IDEMPOTENT'
                  ? 'info'
                  : 'neutral'
          }
        >
          {statusLabel(action.side_effect_class)}
        </Badge>
      ),
    },
    {
      key: 'checks',
      header: 'Checks',
      render: (action) => (
        <span className="cell-muted">
          {action.preconditions.length} pre · {action.postconditions.length} post
        </span>
      ),
    },
    {
      key: 'auth',
      header: 'Authorization',
      render: (action) =>
        action.requires_authorization ? (
          <Badge tone="warning" icon="lock">Human required</Badge>
        ) : (
          <span className="cell-muted">Policy allows</span>
        ),
    },
    ...(events
      ? [
          {
            key: 'state',
            header: 'Observed',
            render: (action: ActionDefinition) => {
              const state = actionState(action, events)
              return state ? (
                <Badge tone={stateTone[state] ?? 'neutral'}>{statusLabel(state)}</Badge>
              ) : (
                <span className="cell-muted">Not reached</span>
              )
            },
          } satisfies Column<ActionDefinition>,
        ]
      : []),
  ]

  return (
    <DataTable
      columns={columns}
      rows={actions}
      getRowKey={(action) => action.id}
      caption="Workflow actions"
      empty={
        <EmptyState
          icon="layers"
          title="No actions in this version"
          description={emptyDescription ?? 'This version contains no executable actions yet.'}
          compact
        />
      }
    />
  )
}