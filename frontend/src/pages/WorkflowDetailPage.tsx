import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useVersion, useWorkflow } from '../api/queries'
import type { VersionSummary } from '../api/types'
import { ActionTable } from '../components/domain/ActionTable'
import { JsonBlock } from '../components/domain/EventTimeline'
import { Icon } from '../components/Icon'
import { Badge, OriginBadge, StatusBadge } from '../components/ui/Badge'
import { Button, buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader, MetricCard } from '../components/ui/Card'
import { CopyButton } from '../components/ui/CopyButton'
import { FormField, TextInput } from '../components/ui/Fields'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, InlineNotice, LoadingState, PageSkeleton } from '../components/ui/States'
import { TabPanel, Tabs } from '../components/ui/Tabs'
import { formatDate, formatDateOnly, relativeTime, pluralize, shortHash, statusLabel } from '../lib/format'

function VersionRow({
  version,
  active,
  onSelect,
}: {
  version: VersionSummary
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      className={`version-row${active ? ' is-active' : ''}`}
      onClick={onSelect}
      aria-current={active ? 'true' : undefined}
    >
      <span className="version-row__number mono">v{version.version}</span>
      <span className="version-row__body">
        <span className="version-row__top">
          <StatusBadge status={version.status} />
          <span className="version-row__hash mono">{shortHash(version.content_hash)}</span>
        </span>
        <span className="version-row__meta">
          {pluralize(version.action_count, 'action')} · {pluralize(version.invariant_count, 'invariant')}
        </span>
        <span className="version-row__date">{formatDate(version.created_at)}</span>
      </span>
      <Icon name="chevronRight" size={14} />
    </button>
  )
}

export function WorkflowDetailPage() {
  const { workflowId = '' } = useParams()
  const workflow = useWorkflow(workflowId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState('actions')
  const [runOpen, setRunOpen] = useState(false)
  const [seed, setSeed] = useState('7')
  const [runInputs, setRunInputs] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const selectedSummary = useMemo(
    () => workflow.data?.versions.find((version) => version.id === selectedId) ?? null,
    [selectedId, workflow.data],
  )
  const version = useVersion(workflowId, selectedId)
  const versionActions = version.data?.actions ?? []
  const browserWorkflow = versionActions.some((action) => action.channel === 'BROWSER')
  const inputSchema = (version.data?.input_schema ?? {}) as {
    properties?: Record<string, unknown>
    required?: string[]
  }
  const inputKeys = Object.keys(inputSchema.properties ?? {})
  const requiredInputs = new Set(inputSchema.required ?? [])
  const missingRequiredInput = [...requiredInputs].some(
    (key) => !(runInputs[key] ?? '').trim(),
  )

  useEffect(() => {
    if (!runOpen) return
    const properties = inputSchema.properties ?? {}
    setRunInputs((current) => {
      const next: Record<string, string> = {}
      for (const key of Object.keys(properties)) next[key] = current[key] ?? ''
      return next
    })
  }, [runOpen, version.data])

  useEffect(() => {
    setSelectedId(null)
  }, [workflowId])

  useEffect(() => {
    if (!workflow.data || selectedId) return
    const preferred =
      workflow.data.versions.find((item) => item.id === workflow.data?.current_version_id) ??
      workflow.data.versions[0]
    setSelectedId(preferred?.id ?? null)
  }, [workflow.data, selectedId])

  const publish = useMutation({
    mutationFn: () => api.publishVersion(workflowId, selectedId as string),
    onSuccess: (published) => {
      setNotice(`Version v${published.version} published. Its action set is now immutable.`)
      setActionError(null)
      void queryClient.invalidateQueries({ queryKey: ['workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['workflows', workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['workflows', workflowId, 'versions', selectedId] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
    onError: (error: Error) => {
      setActionError(error.message)
      setNotice(null)
    },
  })

  const fork = useMutation({
    mutationFn: () => {
      if (!version.data) throw new Error('Version data is not loaded.')
      return api.createVersion(workflowId, {
        actions: version.data.actions.map((action) => ({
          ordinal: action.ordinal,
          kind: action.kind,
          label: action.label,
          target: action.target,
          input: action.input,
          preconditions: action.preconditions,
          postconditions: action.postconditions,
          side_effect_class: action.side_effect_class,
          requires_authorization: action.requires_authorization,
          timeout_ms: action.timeout_ms,
        })),
        invariants: version.data.invariants.map((invariant) => ({
          name: invariant.name,
          description: invariant.description,
          kind: invariant.kind,
          severity: invariant.severity,
          expression: invariant.expression,
        })),
        publish: false,
      })
    },
    onSuccess: (draft) => {
      setSelectedId(draft.id)
      setTab('actions')
      setNotice(`Draft v${draft.version} created from v${version.data?.version ?? '—'}. Publish it when ready.`)
      setActionError(null)
      void queryClient.invalidateQueries({ queryKey: ['workflows', workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['workflows'] })
    },
    onError: (error: Error) => {
      setActionError(error.message)
      setNotice(null)
    },
  })

  const createRun = useMutation({
    mutationFn: () => {
      if (!workflow.data) throw new Error('Workflow not loaded.')
      const publishedId =
        selectedSummary?.status === 'PUBLISHED'
          ? selectedSummary.id
          : workflow.data.current_version_id ?? undefined
      const inputs: Record<string, string> = {}
      for (const key of inputKeys) {
        const value = (runInputs[key] ?? '').trim()
        if (value) inputs[key] = value
      }
      return api.createRun({
        workflow_id: workflow.data.id,
        version_id: publishedId,
        seed: Number.parseInt(seed, 10) || 0,
        inputs,
      })
    },
    onSuccess: (run) => {
      setRunOpen(false)
      setNotice(null)
      setActionError(null)
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
      void queryClient.invalidateQueries({ queryKey: ['decisions'] })
      navigate(`/runs/${run.id}`)
    },
    onError: (error: Error) => setActionError(error.message),
  })

  if (workflow.isPending) return <PageSkeleton />
  if (workflow.isError) {
    return (
      <div className="stack-lg">
        <PageHeader title="Workflow" description="Workflow detail and version history." />
        <ErrorState error={workflow.error} onRetry={() => void workflow.refetch()} />
        <Link to="/workflows" className={buttonClass('secondary', 'md')}>
          <Icon name="arrowLeft" size={15} />
          <span>Back to Workflow Studio</span>
        </Link>
      </div>
    )
  }

  const data = workflow.data
  const canPublish = selectedSummary?.status === 'DRAFT' && (version.data?.actions.length ?? 0) > 0
  const canRun = Boolean(data.current_version_id)
  const canFork = (version.data?.actions.length ?? 0) > 0

  return (
    <div className="stack-lg">
      <div className="back-row">
        <Link to="/workflows" className="text-link">
          <Icon name="arrowLeft" size={14} />
          Workflow Studio
        </Link>
      </div>

      <PageHeader
        eyebrow={`Workflow · ${data.slug}`}
        title={data.name}
        description={data.description || 'No description provided.'}
        meta={
          <div className="page-meta">
            <OriginBadge origin={data.origin} />
            {data.latest_status ? (
              <Badge tone={data.latest_status === 'PUBLISHED' ? 'success' : 'neutral'}>
                Latest v{data.latest_version} · {statusLabel(data.latest_status)}
              </Badge>
            ) : null}
            <span><Icon name="layers" size={13} /> {pluralize(data.version_count, 'version')}</span>
            <span><Icon name="activity" size={13} /> {pluralize(data.run_count, 'run')}</span>
            <span><Icon name="clock" size={13} /> Created {formatDateOnly(data.created_at)}</span>
            <span className="mono">owner: {data.owner}</span>
          </div>
        }
        actions={
          <>
            <Button
              variant="secondary"
              icon="gitBranch"
              onClick={() => fork.mutate()}
              loading={fork.isPending}
              disabled={!canFork || version.isPending}
              title={canFork ? 'Copy this version into a new draft' : 'This version has no actions to fork'}
            >
              Fork as draft
            </Button>
            {selectedSummary?.status === 'DRAFT' ? (
              <Button
                variant="secondary"
                icon="check"
                loading={publish.isPending}
                disabled={!canPublish}
                onClick={() => publish.mutate()}
              >
                Publish v{selectedSummary.version}
              </Button>
            ) : null}
            <Button
              variant="primary"
              icon="play"
              disabled={!canRun}
              title={canRun ? 'Run the current published version' : 'Publish a version first'}
              onClick={() => setRunOpen(true)}
            >
              Run workflow
            </Button>
          </>
        }
      />

      {notice ? <InlineNotice tone="success" title="Change applied">{notice}</InlineNotice> : null}
      {actionError ? <InlineNotice tone="danger" title="Action failed">{actionError}</InlineNotice> : null}

      <div className="grid grid--metrics">
        <MetricCard label="Versions" value={data.version_count} hint={`Latest v${data.latest_version ?? '—'}`} icon="layers" />
        <MetricCard label="Executions" value={data.run_count} hint="All time, demo and live" icon="activity" to={`/runs?workflow=${data.id}`} />
        <MetricCard
          label="Selected version"
          value={selectedSummary ? `v${selectedSummary.version}` : '—'}
          hint={selectedSummary ? statusLabel(selectedSummary.status) : 'No version'}
          icon="file"
        />
        <MetricCard
          label="Content hash"
          value={<span className="mono metric__value--small">{selectedSummary ? shortHash(selectedSummary.content_hash) : '—'}</span>}
          hint="SHA-256 over the action set"
          icon="key"
        />
      </div>

      <div className="grid grid--main-rail">
        <Card>
          <CardHeader
            title={selectedSummary ? `Version v${selectedSummary.version}` : 'Version'}
            subtitle={
              selectedSummary
                ? `${statusLabel(selectedSummary.status)} · ${pluralize(selectedSummary.action_count, 'action')} · ${pluralize(selectedSummary.invariant_count, 'invariant')}`
                : 'Select a version from the history.'
            }
            action={
              selectedSummary ? (
                <div className="row-actions">
                  <CopyButton value={selectedSummary.content_hash} label="Copy content hash" />
                  <StatusBadge status={selectedSummary.status} />
                </div>
              ) : null
            }
            icon="file"
          />
          <CardBody className="stack-md">
            {!selectedSummary ? (
              <EmptyState
                icon="layers"
                title="No versions"
                description="This workflow has no versions."
                compact
              />
            ) : version.isPending ? (
              <LoadingState label="Loading version" rows={4} />
            ) : version.isError ? (
              <ErrorState error={version.error} onRetry={() => void version.refetch()} />
            ) : version.data ? (
              <>
                <dl className="kv kv--inline">
                  <div><dt>Created</dt><dd>{formatDate(selectedSummary.created_at)}</dd></div>
                  <div><dt>Published</dt><dd>{selectedSummary.published_at ? formatDate(selectedSummary.published_at) : 'Not published'}</dd></div>
                  <div><dt>Origin</dt><dd><OriginBadge origin={selectedSummary.origin} /></dd></div>
                  <div>
                    <dt>Content hash</dt>
                    <dd className="mono" title={selectedSummary.content_hash}>{shortHash(selectedSummary.content_hash)}</dd>
                  </div>
                </dl>

                <Tabs
                  ariaLabel="Version detail"
                  active={tab}
                  onChange={setTab}
                  items={[
                    { id: 'actions', label: 'Actions', count: version.data.actions.length },
                    { id: 'invariants', label: 'Invariants', count: version.data.invariants.length },
                    { id: 'definition', label: 'Definition' },
                  ]}
                >
                  <TabPanel id="actions" active={tab}>
                    <ActionTable
                      actions={version.data.actions}
                      emptyDescription="A version needs at least one action before it can be published."
                    />
                  </TabPanel>
                  <TabPanel id="invariants" active={tab}>
                    {version.data.invariants.length === 0 ? (
                      <EmptyState
                        icon="shield"
                        title="No invariants attached"
                        description="Terminal success will be INCONCLUSIVE without at least one invariant."
                        compact
                      />
                    ) : (
                      <ul className="invariant-list">
                        {version.data.invariants.map((invariant) => (
                          <li key={invariant.id} className="invariant">
                            <div className="invariant__head">
                              <strong>{invariant.name}</strong>
                              <Badge tone={invariant.severity === 'CRITICAL' ? 'danger' : 'warning'}>
                                {invariant.severity}
                              </Badge>
                              <Badge tone="neutral">{statusLabel(invariant.kind)}</Badge>
                            </div>
                            {invariant.description ? <p>{invariant.description}</p> : null}
                            <JsonBlock value={invariant.expression} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </TabPanel>
                  <TabPanel id="definition" active={tab}>
                    <JsonBlock
                      title="Action definitions"
                      value={version.data.actions.map((action) => ({
                        ordinal: action.ordinal,
                        kind: action.kind,
                        label: action.label,
                        target: action.target,
                        input: action.input,
                        preconditions: action.preconditions,
                        postconditions: action.postconditions,
                        side_effect_class: action.side_effect_class,
                        requires_authorization: action.requires_authorization,
                      }))}
                    />
                  </TabPanel>
                </Tabs>
              </>
            ) : null}
          </CardBody>
        </Card>

        <div className="stack-md">
          <Card>
            <CardHeader
              title="Version history"
              subtitle="Immutable published versions; drafts can be edited."
              icon="gitBranch"
            />
            <CardBody className="card__body--flush">
              {data.versions.length === 0 ? (
                <EmptyState icon="layers" title="No versions" description="No version history exists." compact />
              ) : (
                <div className="version-list">
                  {data.versions.map((item) => (
                    <VersionRow
                      key={item.id}
                      version={item}
                      active={item.id === selectedId}
                      onSelect={() => {
                        setSelectedId(item.id)
                        setTab('actions')
                      }}
                    />
                  ))}
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Publishing rules" icon="shield" />
            <CardBody className="stack-sm">
              <ul className="rule-list">
                <li>Only a published version can execute.</li>
                <li>Publishing hashes the full action and invariant set.</li>
                <li>Editing creates a new draft; history is never rewritten.</li>
                <li>At least one action is required to publish.</li>
              </ul>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Recent activity" icon="activity" />
            <CardBody className="stack-sm">
              {data.run_count === 0 ? (
                <p className="fine-print">This workflow has not been executed yet.</p>
              ) : (
                <p className="fine-print">
                  {pluralize(data.run_count, 'run')} recorded. Inspect them in the Execution Inspector.
                </p>
              )}
              <Link to={`/runs?workflow=${data.id}`} className="text-link">
                Filter runs for this workflow
                <Icon name="arrowRight" size={13} />
              </Link>
              <div className="fine-print">Last workflow update {relativeTime(data.created_at)}</div>
            </CardBody>
          </Card>
        </div>
      </div>

      <Modal
        open={runOpen}
        onClose={() => setRunOpen(false)}
        title={`Run ${data.name}`}
        description={
          browserWorkflow
            ? "Executes the current published version through the Playwright driver against the controlled /demo-app. The run is recorded as LIVE activity with a hash-linked audit chain."
            : "Executes the current published version in the backend sandbox. The run is recorded as LIVE activity with a hash-linked audit chain."
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setRunOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              icon="play"
              loading={createRun.isPending}
              disabled={missingRequiredInput}
              onClick={() => createRun.mutate()}
            >
              Start execution
            </Button>
          </>
        }
      >
        <div className="stack-md">
          {actionError ? <InlineNotice tone="danger" title="Run could not start">{actionError}</InlineNotice> : null}
          <dl className="kv">
            <div><dt>Workflow</dt><dd>{data.name}</dd></div>
            <div>
              <dt>Version</dt>
              <dd>{selectedSummary?.status === 'PUBLISHED' ? `v${selectedSummary.version}` : `v${data.latest_version ?? '—'} (current published)`}</dd>
            </div>
            <div><dt>Origin</dt><dd><OriginBadge origin="LIVE" /></dd></div>
            <div>
              <dt>Driver</dt>
              <dd className="mono">{browserWorkflow ? 'playwright-v1' : 'simulated-v1'}</dd>
            </div>
          </dl>
          {browserWorkflow ? (
            <InlineNotice tone="info" title="Controlled browser run">
              Chromium drives the controlled /demo-app only. Missing or changed selectors
              fail the run safely; every step still needs a passing postcondition.
            </InlineNotice>
          ) : null}
          {inputKeys.map((key) => (
            <FormField
              key={key}
              label={statusLabel(key)}
              htmlFor={`run-input-${key}`}
              required={requiredInputs.has(key)}
              hint={`Declared input · inputs.${key}`}
            >
              <TextInput
                id={`run-input-${key}`}
                value={runInputs[key] ?? ''}
                onChange={(event) =>
                  setRunInputs((current) => ({ ...current, [key]: event.target.value }))
                }
              />
            </FormField>
          ))}
          <FormField label="Random seed" htmlFor="run-seed" hint="Same seed and version produce the same run path.">
            <TextInput
              id="run-seed"
              type="number"
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </FormField>
        </div>
      </Modal>
    </div>
  )
}