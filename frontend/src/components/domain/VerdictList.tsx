import type { Verdict } from '../../api/types'
import { outcomeTone, statusLabel } from '../../lib/format'
import { Icon } from '../Icon'
import { Badge } from '../ui/Badge'
import { EmptyState } from '../ui/States'

export function VerdictList({ verdicts }: { verdicts: Verdict[] }) {
  if (verdicts.length === 0) {
    return (
      <EmptyState
        icon="shield"
        title="No invariant verdicts"
        description="A verdict is recorded only when the engine reaches verification. Failed or paused runs show their failure code instead."
        compact
      />
    )
  }

  return (
    <ul className="verdict-list">
      {verdicts.map((verdict) => {
        const tone = outcomeTone(verdict.verdict)
        return (
          <li key={verdict.invariant_id} className={`verdict verdict--${tone}`}>
            <span className="verdict__icon">
              <Icon name={tone === 'success' ? 'check' : tone === 'danger' ? 'x' : 'alert'} size={16} />
            </span>
            <div className="verdict__body">
              <div className="verdict__head">
                <strong>{verdict.invariant_name}</strong>
                <Badge tone={tone}>{statusLabel(verdict.verdict)}</Badge>
                <Badge tone={verdict.severity === 'CRITICAL' ? 'danger' : 'warning'}>{verdict.severity}</Badge>
              </div>
              <p className="verdict__detail">{verdict.detail}</p>
              {Object.keys(verdict.observed).length > 0 ? (
                <pre className="code-block code-block--sm">{JSON.stringify(verdict.observed, null, 2)}</pre>
              ) : null}
            </div>
          </li>
        )
      })}
    </ul>
  )
}