import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useDemo, useHealth, useReadiness, useSystem } from '../api/queries'
import { Icon } from '../components/Icon'
import { Badge } from '../components/ui/Badge'
import { Button, buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader, MetricCard } from '../components/ui/Card'
import { CopyButton } from '../components/ui/CopyButton'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { ErrorState, InlineNotice, LoadingState, PageSkeleton } from '../components/ui/States'
import { formatDate, outcomeTone, pluralize, statusLabel, uptime } from '../lib/format'

function checkTone(value: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (value === 'pass') return 'success'
  if (value === 'warn') return 'warning'
  if (value === 'fail') return 'danger'
  return 'neutral'
}

export function SettingsPage() {
  const system = useSystem()
  const health = useHealth()
  const readiness = useReadiness()
  const demo = useDemo()
  const queryClient = useQueryClient()
  const [resetOpen, setResetOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const invalidateWorkspace = () => {
    for (const key of ['demo', 'workflows', 'runs', 'overview', 'decisions', 'scenarios', 'audit', 'recoveries', 'regression-tests']) {
      void queryClient.invalidateQueries({ queryKey: [key] })
    }
  }

  const seed = useMutation({
    mutationFn: () => api.seedDemo(),
    onSuccess: (result) => {
      setActionError(null)
      setNotice(
        result.created
          ? `Demonstration workspace seeded with ${pluralize(result.runs.length, 'sample run')} and ${pluralize(result.decisions.length, 'decision')}.`
          : 'The demonstration workspace is already seeded; no duplicate fixtures were created.',
      )
      invalidateWorkspace()
    },
    onError: (error: Error) => {
      setNotice(null)
      setActionError(error.message)
    },
  })

  const reset = useMutation({
    mutationFn: () => api.resetDemo(),
    onSuccess: (result) => {
      setResetOpen(false)
      setActionError(null)
      setNotice(
        `Removed ${pluralize(result.deleted, 'demo workflow')} and ${pluralize(result.runs, 'sample run')}. Live records were not touched.`,
      )
      invalidateWorkspace()
    },
    onError: (error: Error) => {
      setResetOpen(false)
      setNotice(null)
      setActionError(error.message)
    },
  })

  if (system.isPending) return <PageSkeleton />
  if (system.isError) {
    return (
      <div className="stack-lg">
        <PageHeader title="Settings & system health" description="Service configuration and readiness." />
        <ErrorState error={system.error} onRetry={() => void system.refetch()} />
      </div>
    )
  }

  const data = system.data
  const demoInfo = demo.data

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Operate"
        title="Settings & system health"
        description="Runtime configuration from the backend, health probes, and the demonstration workspace controls."
        meta={
          <div className="page-meta">
            <span><Icon name="clock" size={13} /> Server time {formatDate(data.server_time)}</span>
            <span className="mono">{data.app} {data.version}</span>
            <Badge tone="info" mono>{data.environment}</Badge>
          </div>
        }
        actions={
          <Button
            variant="secondary"
            icon="refresh"
            loading={system.isFetching || readiness.isFetching}
            onClick={() => {
              void system.refetch()
              void health.refetch()
              void readiness.refetch()
              void demo.refetch()
            }}
          >
            Re-run health checks
          </Button>
        }
      />

      {notice ? <InlineNotice tone="success" title="Workspace updated">{notice}</InlineNotice> : null}
      {actionError ? <InlineNotice tone="danger" title="Workspace action failed">{actionError}</InlineNotice> : null}

      <div className="grid grid--metrics">
        <MetricCard
          label="Service"
          value={health.data?.status === 'ok' ? 'Online' : health.isError ? 'Offline' : 'Checking'}
          hint={health.data ? `${health.data.service} ${health.data.version}` : 'Liveness probe'}
          icon="activity"
          tone={health.isError ? 'danger' : 'success'}
        />
        <MetricCard
          label="Database"
          value={data.database.healthy ? 'Healthy' : 'Degraded'}
          hint={`Schema v${data.database.schema_version}`}
          icon="database"
          tone={data.database.healthy ? 'success' : 'danger'}
        />
        <MetricCard
          label="Readiness"
          value={readiness.data ? statusLabel(readiness.data.status) : '—'}
          hint={readiness.data ? `${Object.values(readiness.data.checks).filter((value) => value === 'pass').length}/${Object.keys(readiness.data.checks).length} checks pass` : 'Readiness probe'}
          icon="check"
          tone={readiness.data?.status === 'ready' ? 'success' : 'warning'}
        />
        <MetricCard
          label="Uptime"
          value={uptime(data.uptime_seconds)}
          hint="Since process start"
          icon="clock"
        />
      </div>

      <div className="grid grid--2">
        <Card>
          <CardHeader title="Health checks" subtitle="Reported by the backend, not inferred by the console." icon="shield" />
          <CardBody className="stack-md">
            <div className="health-row">
              <div className="health-row__label">
                <Icon name="activity" size={16} />
                <span>Liveness</span>
              </div>
              {health.isPending ? (
                <LoadingState rows={1} label="Checking liveness" />
              ) : health.isError ? (
                <Badge tone="danger" icon="alert">Unreachable</Badge>
              ) : (
                <div className="row-actions">
                  <Badge tone="success" icon="check">Pass</Badge>
                  <span className="fine-print mono">{formatDate(health.data?.time)}</span>
                </div>
              )}
            </div>
            <div className="health-row">
              <div className="health-row__label">
                <Icon name="database" size={16} />
                <span>Readiness</span>
              </div>
              {readiness.isPending ? (
                <LoadingState rows={1} label="Checking readiness" />
              ) : readiness.isError ? (
                <Badge tone="danger" icon="alert">Unreachable</Badge>
              ) : (
                <Badge tone={checkTone(readiness.data?.status === 'ready' ? 'pass' : 'warn')}>
                  {statusLabel(readiness.data?.status ?? 'unknown')}
                </Badge>
              )}
            </div>
            {readiness.data ? (
              <ul className="check-list">
                {Object.entries(readiness.data.checks).map(([name, value]) => (
                  <li key={name}>
                    <span className="mono">{name}</span>
                    <Badge tone={checkTone(value)}>{statusLabel(value)}</Badge>
                  </li>
                ))}
              </ul>
            ) : null}
            <p className="fine-print">
              Liveness answers “is the process up”. Readiness answers “can it serve a request” and
              includes the database and evidence store.
            </p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Runtime configuration" subtitle="Secrets are never returned by the API." icon="settings" />
          <CardBody>
            <dl className="kv">
              <div><dt>Application</dt><dd>{data.app}</dd></div>
              <div><dt>Version</dt><dd className="mono">{data.version}</dd></div>
              <div><dt>Environment</dt><dd>{data.environment}</dd></div>
              <div><dt>API prefix</dt><dd className="mono">{data.api_prefix}</dd></div>
              <div>
                <dt>Auth mode</dt>
                <dd>
                  <Badge tone={data.settings.auth_mode === 'bearer' ? 'success' : 'warning'}>
                    {statusLabel(data.settings.auth_mode)}
                  </Badge>
                </dd>
              </div>
              <div><dt>Decision TTL</dt><dd>{data.settings.decision_ttl_seconds}s</dd></div>
              <div><dt>Action budget</dt><dd>{data.settings.run_action_budget}</dd></div>
              <div><dt>Log level</dt><dd className="mono">{data.settings.log_level}</dd></div>
              <div><dt>Log format</dt><dd>{data.settings.log_json ? 'JSON lines' : 'Human readable'}</dd></div>
            </dl>
            <div className="divider" />
            <h3 className="subheading">Allowed frontend origins</h3>
            <ul className="cors-list">
              {data.settings.cors_origins.map((origin) => (
                <li key={origin}><span className="mono">{origin}</span><Badge tone="success" icon="check">allowed</Badge></li>
              ))}
            </ul>
            <div className="fine-print">
              Override with <span className="mono">FLOWSTATE_CORS_ORIGINS</span> (comma separated). Requests
              from other origins are rejected by the browser.
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Database"
            subtitle="SQLite with WAL; the repository port keeps PostgreSQL possible."
            icon="database"
            action={<Badge tone={data.database.healthy ? 'success' : 'danger'}>{data.database.healthy ? 'healthy' : 'degraded'}</Badge>}
          />
          <CardBody>
            <dl className="kv">
              <div>
                <dt>URL</dt>
                <dd className="mono mono-wrap">
                  {data.database.url}
                  <CopyButton value={data.database.url} label="Copy database URL" />
                </dd>
              </div>
              <div><dt>Schema version</dt><dd className="mono">{data.database.schema_version}</dd></div>
              <div><dt>Initialisation</dt><dd>Explicit, versioned, idempotent</dd></div>
            </dl>
            <p className="fine-print">
              The schema is created on startup and stamped with a version. No migration or table
              creation runs silently on read.
            </p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Execution boundary"
            subtitle="What actually performs actions"
            icon="terminal"
            action={<Badge tone="info" mono>{data.driver.name}</Badge>}
          />
          <CardBody>
            <dl className="kv">
              <div><dt>Driver</dt><dd className="mono">{data.driver.name}</dd></div>
              <div><dt>Kind</dt><dd>{data.driver.kind}</dd></div>
              <div><dt>Browser</dt><dd>{data.driver.browser ? 'Yes' : 'No'}</dd></div>
            </dl>
            {data.driver.note ? (
              <InlineNotice tone="warning" title="Build limitation">
                {data.driver.note}
              </InlineNotice>
            ) : null}
            <p className="fine-print">
              The console never drives Playwright, the filesystem or the network. Every action and
              verdict comes back from the backend.
            </p>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Demonstration workspace"
          subtitle="Clearly labelled fixtures, separate from persisted live execution data."
          icon="flask"
          action={<Badge tone={demoInfo?.seeded ? 'accent' : 'neutral'}>{demoInfo?.seeded ? 'seeded' : 'not seeded'}</Badge>}
        />
        <CardBody className="stack-md">
          <div className="demo-settings">
            <dl className="kv">
              <div><dt>Batch</dt><dd className="mono">{demoInfo?.batch ?? '—'}</dd></div>
              <div><dt>Seeded</dt><dd>{demoInfo?.seeded ? 'Yes' : 'No'}</dd></div>
              <div><dt>Seeding enabled</dt><dd>{demoInfo?.enabled ? 'Yes' : 'No'}</dd></div>
              <div>
                <dt>Workflow</dt>
                <dd>
                  {demoInfo?.workflow_id ? (
                    <Link to={`/workflows/${demoInfo.workflow_id}`} className="text-link">Invoice Approval</Link>
                  ) : '—'}
                </dd>
              </div>
              <div><dt>Origin</dt><dd><Badge tone="accent" icon="flask">DEMO</Badge></dd></div>
            </dl>
            <div className="stack-sm">
              <p className="fine-print">
                Seed creates one invoice-approval workflow, five historical sample runs, an open decision
                and four fault scenarios. Reset removes only rows tagged DEMO; live runs and workflows are
                left untouched.
              </p>
              <div className="row-actions">
                <Button
                  variant="secondary"
                  icon="plus"
                  loading={seed.isPending}
                  disabled={!demoInfo?.enabled}
                  onClick={() => seed.mutate()}
                >
                  {demoInfo?.seeded ? 'Re-check seed' : 'Seed workspace'}
                </Button>
                <Button
                  variant="danger"
                  icon="close"
                  disabled={!demoInfo?.seeded || reset.isPending}
                  onClick={() => setResetOpen(true)}
                >
                  Reset demo
                </Button>
                <Link to="/workflows" className={buttonClass('ghost', 'md')}>
                  <Icon name="workflow" size={15} />
                  <span>Open Workflow Studio</span>
                </Link>
              </div>
            </div>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="About this build" icon="info" />
        <CardBody className="stack-sm">
          <p className="body-text">
            FLOWSTATE executes workflows in a deterministic in-process sandbox and records every
            transition in a hash-linked audit chain. Playwright capture and browser execution are not
            wired up in this build, so no run claims to be a browser execution. The console is a client:
            it renders backend state and never computes a verdict.
          </p>
          <div className="row-actions">
            <Badge tone="neutral" mono>backend-only execution</Badge>
            <Badge tone="neutral" mono>problem+json errors</Badge>
            <Badge tone="neutral" mono>api v1</Badge>
            <Badge tone={outcomeTone('READY')} mono>{data.settings.auth_mode}</Badge>
          </div>
        </CardBody>
      </Card>

      <Modal
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        title="Remove the demonstration workspace?"
        description="Only rows tagged DEMO are deleted. Live workflows and runs are preserved."
        footer={
          <>
            <Button variant="ghost" onClick={() => setResetOpen(false)}>Cancel</Button>
            <Button variant="danger" icon="close" loading={reset.isPending} onClick={() => reset.mutate()}>
              Reset demo data
            </Button>
          </>
        }
      >
        <div className="stack-md">
          <InlineNotice tone="warning" title="This cannot be undone">
            The seeded Invoice Approval workflow, its five sample runs, the open decision batching
            and the demo scenarios will be removed. You can seed them again afterwards.
          </InlineNotice>
          <dl className="kv">
            <div><dt>Batch</dt><dd className="mono">{demoInfo?.batch ?? '—'}</dd></div>
            <div><dt>Workflow</dt><dd>{demoInfo?.workflow_id ? 'Invoice Approval' : 'None'}</dd></div>
          </dl>
        </div>
      </Modal>
    </div>
  )
}