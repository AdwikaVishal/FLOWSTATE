import type { ReactNode } from 'react'
import { ApiError } from '../../api/client'
import { Icon, type IconName } from '../Icon'
import { Button } from './Button'

export function Skeleton({ width = '100%', height = 14 }: { width?: string; height?: number }) {
  return <span className="skeleton" style={{ width, height }} aria-hidden="true" />
}

export function LoadingState({ label = 'Loading', rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="loading-state" role="status" aria-label={label}>
      <div className="loading-state__lines">
        {Array.from({ length: rows }, (_, index) => (
          <Skeleton key={index} width={index === rows - 1 ? '62%' : '100%'} />
        ))}
      </div>
      <span className="visually-hidden">{label}</span>
    </div>
  )
}

interface EmptyStateProps {
  icon?: IconName
  title: string
  description: string
  action?: ReactNode
  compact?: boolean
}

export function EmptyState({ icon = 'inbox', title, description, action, compact }: EmptyStateProps) {
  return (
    <div className={`empty-state${compact ? ' empty-state--compact' : ''}`}>
      <span className="empty-state__icon">
        <Icon name={icon} size={compact ? 20 : 26} />
      </span>
      <h3 className="empty-state__title">{title}</h3>
      <p className="empty-state__description">{description}</p>
      {action ? <div className="empty-state__action">{action}</div> : null}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  title = 'This view could not load',
}: {
  error: unknown
  onRetry?: () => void
  title?: string
}) {
  const message =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message
        : 'An unexpected error occurred.'
  const code = error instanceof ApiError ? error.code : null
  const requestId = error instanceof ApiError ? error.requestId : null

  return (
    <div className="error-state" role="alert">
      <span className="error-state__icon">
        <Icon name="alert" size={22} />
      </span>
      <div className="error-state__content">
        <h3 className="error-state__title">{title}</h3>
        <p className="error-state__message">{message}</p>
        <div className="error-state__meta">
          {code ? <span className="mono">code: {code}</span> : null}
          {requestId ? <span className="mono">request: {requestId}</span> : null}
        </div>
      </div>
      {onRetry ? (
        <Button size="sm" icon="refresh" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  )
}

export function InlineNotice({
  tone = 'info',
  title,
  children,
  icon,
}: {
  tone?: 'info' | 'success' | 'warning' | 'danger'
  title?: string
  children: ReactNode
  icon?: IconName
}) {
  const defaultIcon: IconName =
    tone === 'success' ? 'check' : tone === 'danger' ? 'alert' : tone === 'warning' ? 'alert' : 'info'
  return (
    <div className={`notice notice--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <span className="notice__icon">
        <Icon name={icon ?? defaultIcon} size={16} />
      </span>
      <div className="notice__body">
        {title ? <strong className="notice__title">{title}</strong> : null}
        <div className="notice__text">{children}</div>
      </div>
    </div>
  )
}

export function PageSkeleton() {
  return (
    <div className="stack-lg" aria-busy="true" aria-label="Loading page">
      <div className="stack-sm">
        <Skeleton width="180px" height={12} />
        <Skeleton width="360px" height={32} />
        <Skeleton width="520px" height={14} />
      </div>
      <div className="grid grid--metrics">
        {Array.from({ length: 4 }, (_, index) => (
          <div className="metric" key={index}>
            <Skeleton width="60%" height={12} />
            <div style={{ height: 12 }} />
            <Skeleton width="42%" height={28} />
          </div>
        ))}
      </div>
      <div className="card">
        <div className="card__body stack-sm">
          <Skeleton width="30%" height={16} />
          <LoadingState rows={5} />
        </div>
      </div>
    </div>
  )
}