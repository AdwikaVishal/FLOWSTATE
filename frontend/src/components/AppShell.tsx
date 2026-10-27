import { useEffect, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { ACTOR_ROLES, getActorRole, setActorRole } from '../api/client'
import { useDemo, useHealth } from '../api/queries'
import type { ActorRole } from '../api/types'
import { Icon, type IconName } from './Icon'
import { Badge } from './ui/Badge'
import { Spinner } from './ui/Spinner'

interface NavItem {
  to: string
  label: string
  icon: IconName
  end?: boolean
}

interface NavGroup {
  label: string
  items: NavItem[]
}

const navGroups: NavGroup[] = [
  {
    label: 'Monitor',
    items: [
      { to: '/', label: 'Overview', icon: 'overview', end: true },
      { to: '/slice', label: 'Invoice slice', icon: 'layers' },
      { to: '/runs', label: 'Execution Inspector', icon: 'activity' },
    ],
  },
  {
    label: 'Build',
    items: [
      { to: '/workflows', label: 'Workflow Studio', icon: 'workflow' },
      { to: '/learning', label: 'Learning Studio', icon: 'play' },
      { to: '/chaos', label: 'Chaos Lab', icon: 'zap' },
    ],
  },
  {
    label: 'Operate',
    items: [
      { to: '/decisions', label: 'Decision Inbox', icon: 'inbox' },
      { to: '/recovery', label: 'Recovery & Audit', icon: 'shield' },
    ],
  },
]

const pageTitles: Record<string, string> = {
  '/': 'Overview',
  '/slice': 'Invoice processing slice',
  '/workflows': 'Workflow Studio',
  '/learning': 'Learning Studio',
  '/runs': 'Execution Inspector',
  '/chaos': 'Chaos Lab',
  '/decisions': 'Decision Inbox',
  '/recovery': 'Recovery & Audit',
  '/settings': 'Settings & system health',
}

function pageTitle(pathname: string): string {
  if (pageTitles[pathname]) return pageTitles[pathname]
  if (pathname.startsWith('/workflows/')) return 'Workflow detail'
  if (pathname.startsWith('/runs/')) return 'Execution Inspector'
  return 'FLOWSTATE Console'
}

const roleLabels: Record<ActorRole, string> = {
  OPERATOR: 'Operator',
  REVIEWER: 'Reviewer',
  FINANCE_APPROVER: 'Finance approver',
  ADMIN: 'Admin',
}

function RolePicker() {
  const queryClient = useQueryClient()
  const [role, setRole] = useState<ActorRole>(getActorRole)

  return (
    <label className="role-picker">
      <span className="role-picker__label">Acting role</span>
      <select
        className="role-picker__select"
        value={role}
        onChange={(event) => {
          const next = event.target.value as ActorRole
          setRole(next)
          setActorRole(next)
          void queryClient.invalidateQueries()
        }}
      >
        {ACTOR_ROLES.map((item) => (
          <option key={item} value={item}>
            {roleLabels[item]}
          </option>
        ))}
      </select>
    </label>
  )
}

function SystemPill() {
  const health = useHealth()
  if (health.isPending) {
    return (
      <span className="system-pill system-pill--pending">
        <Spinner size={12} />
        Connecting
      </span>
    )
  }
  if (health.isError) {
    return (
      <Link to="/settings" className="system-pill system-pill--down" title="API unreachable">
        <span className="status-dot status-dot--danger" aria-hidden="true" />
        API offline
      </Link>
    )
  }
  return (
    <Link
      to="/settings"
      className="system-pill system-pill--up"
      title={`Connected to ${health.data?.service} ${health.data?.version}`}
    >
      <span className="status-dot status-dot--success" aria-hidden="true" />
      API online
      <span className="system-pill__env">{health.data?.environment}</span>
    </Link>
  )
}

export function AppShell({ children }: { children?: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const health = useHealth()
  const demo = useDemo()

  useEffect(() => {
    setMobileOpen(false)
  }, [location.pathname])

  useEffect(() => {
    document.title = `${pageTitle(location.pathname)} · FLOWSTATE AI`
  }, [location.pathname])

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>

      <aside className={`sidebar${mobileOpen ? ' is-open' : ''}`} aria-label="Primary navigation">
        <div className="sidebar__brand">
          <Link to="/" className="brand" aria-label="FLOWSTATE AI home">
            <span className="brand__mark" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span className="brand__text">
              <strong>FLOWSTATE</strong>
              <small>reliability console</small>
            </span>
          </Link>
          <button
            className="icon-btn sidebar__close"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          >
            <Icon name="close" size={18} />
          </button>
        </div>

        <div className="sidebar__workspace">
          <span className="workspace-avatar">DW</span>
          <span className="workspace-text">
            <strong>Developer workspace</strong>
            <small>Single tenant · local</small>
          </span>
          {demo.data?.seeded ? <Badge tone="accent">Demo</Badge> : null}
        </div>

        <nav className="sidebar__nav">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-group__label">{group.label}</div>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
                >
                  <Icon name={item.icon} size={17} />
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar__footer">
          <NavLink
            to="/settings"
            className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
          >
            <Icon name="settings" size={17} />
            <span>Settings & health</span>
          </NavLink>
          <div className="sidebar__meta">
            <span className="mono">v{health.data?.version ?? '—'}</span>
            <span>backend-only execution</span>
          </div>
        </div>
      </aside>

      {mobileOpen ? (
        <button
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}

      <div className="main-area">
        <header className="topbar">
          <button
            className="icon-btn topbar__menu"
            aria-label="Open navigation"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen(true)}
          >
            <Icon name="menu" size={19} />
          </button>
          <div className="topbar__crumb">
            <span className="topbar__section">Console</span>
            <Icon name="chevronRight" size={12} />
            <span className="topbar__page">{pageTitle(location.pathname)}</span>
          </div>
          <div className="topbar__right">
            <RolePicker />
            <span className="topbar__divider" aria-hidden="true" />
            <SystemPill />
            <span className="topbar__divider" aria-hidden="true" />
            <span className="topbar__actor">
              <span className="actor-avatar">AV</span>
              <span className="topbar__actor-name">Adwika Vishal</span>
            </span>
          </div>
        </header>
        <main id="main-content" className="content">
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  )
}