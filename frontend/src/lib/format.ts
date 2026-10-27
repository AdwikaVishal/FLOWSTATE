export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

export function formatDateOnly(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: '2-digit',
    year: 'numeric',
  }).format(new Date(value))
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return '—'
  const target = new Date(value).getTime()
  if (Number.isNaN(target)) return '—'
  const deltaSeconds = Math.round((target - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31_536_000],
    ['month', 2_592_000],
    ['week', 604_800],
    ['day', 86_400],
    ['hour', 3_600],
    ['minute', 60],
  ]
  for (const [unit, seconds] of units) {
    if (Math.abs(deltaSeconds) >= seconds) {
      return formatter.format(Math.round(deltaSeconds / seconds), unit)
    }
  }
  return 'just now'
}

export function formatDuration(
  startedAt: string | null | undefined,
  endedAt: string | null | undefined,
): string {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = endedAt ? new Date(endedAt).getTime() : Date.now()
  const ms = Math.max(0, end - start)
  if (ms < 1000) return `${ms} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

export function uptime(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
}

export function shortId(value: string | null | undefined, length = 8): string {
  if (!value) return '—'
  return value.length <= length ? value : value.slice(0, length)
}

export function shortHash(value: string | null | undefined): string {
  if (!value) return '—'
  return `${value.slice(0, 10)}…${value.slice(-4)}`
}

export function statusLabel(value: string): string {
  return value
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function eventLabel(value: string): string {
  return value
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function faultLabel(value: string): string {
  const labels: Record<string, string> = {
    TIMEOUT: 'Response timeout',
    TIMEOUT_BEFORE_EXECUTION: 'Timeout before execution',
    RESPONSE_LOST: 'Response lost',
    HTTP_500: 'HTTP 500',
    MALFORMED_RESPONSE: 'Malformed response',
    MISSING_FIELD: 'Missing required field',
    DUPLICATE_RESPONSE: 'Duplicate request',
    LATENCY: 'Added latency',
    PARTIAL_COMMIT: 'Partial commit',
    STALE_READ: 'Stale read',
    NETWORK_DROP: 'Network drop',
    RATE_LIMITED: 'Rate limited',
    TRANSIENT_ERROR: 'Transient dependency error',
    CONTRADICTORY_TOTALS: 'Contradictory totals',
    DELAYED_APPROVAL: 'Delayed approval',
    SELECTOR_DRIFT: 'Changed browser selector',
    POSTCONDITION_FAILURE: 'Postcondition failure',
  }
  return labels[value] ?? statusLabel(value)
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

export function pluralize(count: number, singular: string, plural?: string): string {
  return `${count} ${count === 1 ? singular : (plural ?? `${singular}s`)}`
}

export function formatValue(value: unknown): string {
  if (value === null) return 'null'
  if (value === undefined) return 'undefined'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value, null, 2)
}

export function outcomeTone(value: string): 'success' | 'danger' | 'warning' | 'info' | 'neutral' {
  if (['SUCCEEDED', 'VERIFIED', 'SATISFIED', 'APPLIED', 'AUTHORIZED', 'PUBLISHED', 'READY', 'COMPLETED', 'PASS'].includes(value)) {
    return 'success'
  }
  if (['FAILED', 'VIOLATED', 'DENIED', 'EXPIRED', 'BROKEN', 'FAIL', 'ESCALATED'].includes(value)) {
    return 'danger'
  }
  if (['AWAITING_DECISION', 'AWAITING_RECOVERY', 'UNKNOWN', 'INCONCLUSIVE', 'RUNNING', 'INJECTING', 'OBSERVING', 'VERIFYING', 'APPLYING', 'DEGRADED', 'WARN', 'DEFERRED'].includes(value)) {
    return 'warning'
  }
  if (['PENDING', 'DRAFT', 'PLANNED'].includes(value)) return 'neutral'
  return 'info'
}

export function apiOriginLabel(): string {
  return (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'same-origin proxy'
}