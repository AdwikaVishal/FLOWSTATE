import type { ExecutionEvent } from '../../api/types'

type TimelineEvent = ExecutionEvent & { summary?: string }
import { eventLabel, formatDate, formatValue, shortHash } from '../../lib/format'
import { Icon, type IconName } from '../Icon'
import { Badge } from '../ui/Badge'
import { EmptyState } from '../ui/States'

function eventStyle(type: string): { tone: string; icon: IconName } {
  if (type.includes('FAILED') || type === 'ACTION_UNKNOWN' || type === 'RUN_TIMED_OUT') {
    return { tone: 'danger', icon: 'alert' }
  }
  if (type.includes('DECISION') || type.includes('AUTHORIZATION')) {
    return { tone: 'warning', icon: 'lock' }
  }
  if (type.includes('FAULT')) return { tone: 'danger', icon: 'zap' }
  if (type.includes('RECOVERY')) return { tone: 'info', icon: 'rotate' }
  if (type.includes('VERIFIED') || type.includes('SUCCEEDED') || type.includes('CONFIRMED')) {
    return { tone: 'success', icon: 'check' }
  }
  if (type.includes('INVARIANT')) return { tone: 'info', icon: 'shield' }
  if (type.includes('CHECKPOINT')) return { tone: 'neutral', icon: 'database' }
  return { tone: 'neutral', icon: 'circleDot' }
}

export function EventTimeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return (
      <EmptyState
        icon="list"
        title="No events recorded"
        description="This run has not emitted an audit event yet."
        compact
      />
    )
  }

  return (
    <ol className="timeline" aria-label="Execution event timeline">
      {events.map((event) => {
        const style = eventStyle(event.type)
        const payload = Object.keys(event.payload).length > 0
        return (
          <li key={event.id} className={`timeline__item timeline__item--${style.tone}`}>
            <span className="timeline__marker">
              <Icon name={style.icon} size={13} />
            </span>
            <div className="timeline__content">
              <div className="timeline__head">
                <span className="timeline__seq mono">#{event.seq}</span>
                <span className="timeline__type">{event.summary ?? eventLabel(event.type)}</span>
                {event.node_key ? (
                  <Badge tone="neutral" mono>{event.node_key}</Badge>
                ) : event.action_ordinal !== null ? (
                  <Badge tone="neutral" mono>node {event.action_ordinal}</Badge>
                ) : null}
                <span className="timeline__spacer" />
                <span className="timeline__actor">
                  {event.actor} · {event.actor_id}
                </span>
                <time className="timeline__time" dateTime={event.recorded_at} title={formatDate(event.recorded_at)}>
                  {formatDate(event.recorded_at)}
                </time>
              </div>
              {payload ? (
                <details className="timeline__payload">
                  <summary>
                    <Icon name="chevronRight" size={12} />
                    Payload
                  </summary>
                  <pre className="code-block">{JSON.stringify(event.payload, null, 2)}</pre>
                  <div className="timeline__hash mono" title={event.hash}>
                    <span>prev {shortHash(event.prev_hash)}</span>
                    <span>hash {shortHash(event.hash)}</span>
                  </div>
                </details>
              ) : (
                <div className="timeline__hash mono">
                  <span>hash {shortHash(event.hash)}</span>
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export function JsonBlock({ value, title }: { value: unknown; title?: string }) {
  return (
    <div className="json-block">
      {title ? <div className="json-block__title">{title}</div> : null}
      <pre className="code-block">{formatValue(value)}</pre>
    </div>
  )
}