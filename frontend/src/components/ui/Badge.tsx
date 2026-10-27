import type { ReactNode } from 'react'
import { Icon, type IconName } from '../Icon'
import { outcomeTone, statusLabel } from '../../lib/format'
import type { Origin } from '../../api/types'

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent'

interface BadgeProps {
  tone?: BadgeTone
  children: ReactNode
  icon?: IconName
  dot?: boolean
  title?: string
  mono?: boolean
}

export function Badge({ tone = 'neutral', children, icon, dot, title, mono }: BadgeProps) {
  return (
    <span className={`badge badge--${tone}${mono ? ' badge--mono' : ''}`} title={title}>
      {dot ? <span className="badge__dot" aria-hidden="true" /> : null}
      {icon ? <Icon name={icon} size={12} /> : null}
      {children}
    </span>
  )
}

const statusIcons: Record<string, IconName> = {
  success: 'check',
  danger: 'x',
  warning: 'clock',
  info: 'circleDot',
  neutral: 'circleDot',
  accent: 'circleDot',
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const tone = outcomeTone(status)
  return (
    <Badge tone={tone} icon={statusIcons[tone]} title={`Status: ${status}`}>
      {label ?? statusLabel(status)}
    </Badge>
  )
}

export function VerdictBadge({ verdict }: { verdict: string }) {
  const tone = outcomeTone(verdict)
  return (
    <Badge tone={tone} icon={statusIcons[tone]} title={`Verdict: ${verdict}`}>
      {statusLabel(verdict)}
    </Badge>
  )
}

export function OriginBadge({ origin, demo }: { origin: Origin; demo?: boolean }) {
  if (origin === 'DEMO' || demo) {
    return (
      <Badge
        tone="accent"
        icon="flask"
        title="Seeded demonstration fixture — not live execution data"
      >
        Demo
      </Badge>
    )
  }
  return (
    <Badge tone="success" icon="activity" title="Produced by this application">
      Live
    </Badge>
  )
}