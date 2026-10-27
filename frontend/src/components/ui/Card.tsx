import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Icon, type IconName } from '../Icon'

interface CardProps {
  children: ReactNode
  className?: string
  as?: 'section' | 'div' | 'article'
}

export function Card({ children, className, as: Tag = 'section' }: CardProps) {
  return <Tag className={['card', className].filter(Boolean).join(' ')}>{children}</Tag>
}

interface CardHeaderProps {
  title: ReactNode
  subtitle?: ReactNode
  action?: ReactNode
  icon?: IconName
}

export function CardHeader({ title, subtitle, action, icon }: CardHeaderProps) {
  return (
    <div className="card__header">
      <div className="card__heading">
        {icon ? (
          <span className="card__icon">
            <Icon name={icon} size={16} />
          </span>
        ) : null}
        <div>
          <h2 className="card__title">{title}</h2>
          {subtitle ? <p className="card__subtitle">{subtitle}</p> : null}
        </div>
      </div>
      {action ? <div className="card__action">{action}</div> : null}
    </div>
  )
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={['card__body', className].filter(Boolean).join(' ')}>{children}</div>
}

export function CardFooter({ children }: { children: ReactNode }) {
  return <div className="card__footer">{children}</div>
}

interface MetricCardProps {
  label: string
  value: ReactNode
  hint?: ReactNode
  icon?: IconName
  tone?: 'default' | 'success' | 'warning' | 'danger'
  to?: string
  footer?: ReactNode
}

export function MetricCard({ label, value, hint, icon, tone = 'default', to, footer }: MetricCardProps) {
  const content = (
    <>
      <div className="metric__top">
        <span className="metric__label">{label}</span>
        {icon ? (
          <span className={`metric__icon metric__icon--${tone}`}>
            <Icon name={icon} size={16} />
          </span>
        ) : null}
      </div>
      <div className="metric__value">{value}</div>
      {hint ? <div className="metric__hint">{hint}</div> : null}
      {footer ? <div className="metric__footer">{footer}</div> : null}
    </>
  )
  if (to) {
    return (
      <Link to={to} className={`metric metric--${tone} metric--link`}>
        {content}
        <Icon name="chevronRight" size={14} className="metric__chevron" />
      </Link>
    )
  }
  return <div className={`metric metric--${tone}`}>{content}</div>
}

interface SectionHeadingProps {
  title: string
  description?: string
  action?: ReactNode
}

export function SectionHeading({ title, description, action }: SectionHeadingProps) {
  return (
    <div className="section-heading">
      <div>
        <h2 className="section-heading__title">{title}</h2>
        {description ? <p className="section-heading__description">{description}</p> : null}
      </div>
      {action ? <div className="section-heading__action">{action}</div> : null}
    </div>
  )
}