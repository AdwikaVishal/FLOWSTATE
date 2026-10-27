import type { ReactNode } from 'react'

export interface TabItem {
  id: string
  label: string
  count?: number
}

interface TabsProps {
  items: TabItem[]
  active: string
  onChange: (id: string) => void
  ariaLabel: string
  children?: ReactNode
}

export function Tabs({ items, active, onChange, ariaLabel, children }: TabsProps) {
  return (
    <div className="tabs">
      <div className="tabs__list" role="tablist" aria-label={ariaLabel}>
        {items.map((item) => (
          <button
            key={item.id}
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={active === item.id}
            aria-controls={`panel-${item.id}`}
            className={`tabs__tab${active === item.id ? ' is-active' : ''}`}
            onClick={() => onChange(item.id)}
            type="button"
          >
            {item.label}
            {item.count !== undefined ? <span className="tabs__count">{item.count}</span> : null}
          </button>
        ))}
      </div>
      {children ? <div className="tabs__body">{children}</div> : null}
    </div>
  )
}

export function TabPanel({
  id,
  active,
  children,
}: {
  id: string
  active: string
  children: ReactNode
}) {
  if (id !== active) return null
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`}>
      {children}
    </div>
  )
}