import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useRuns, useWorkflows } from '../api/queries'
import { RunTable } from '../components/domain/RunTable'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, CardBody, CardHeader } from '../components/ui/Card'
import { FormField, Select, TextInput } from '../components/ui/Fields'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { ErrorState, InlineNotice, LoadingState } from '../components/ui/States'
import { pluralize } from '../lib/format'

const statuses = [
  'PENDING',
  'RUNNING',
  'AWAITING_DECISION',
  'AWAITING_RECOVERY',
  'VERIFYING',
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
  'TIMED_OUT',
]

export function ExecutionInspectorPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const workflows = useWorkflows()
  const [status, setStatus] = useState(params.get('status') ?? '')
  const [origin, setOrigin] = useState(params.get('origin') ?? '')
  const workflowFilter = params.get('workflow') ?? ''
  const runs = useRuns({
    status: status || undefined,
    origin: origin || undefined,
    workflowId: workflowFilter || undefined,
  })
  const [newOpen, setNewOpen] = useState(params.get('new') === '1')
  const [workflowId, setWorkflowId] = useState('')
  const [seed, setSeed] = useState('7')
  const [formError, setFormError] = useState<string | null>(null)

  const runnable = useMemo(
    () =>
      (workflows.data ?? []).filter(
        (workflow) => workflow.current_version_id && workflow.latest_status === 'PUBLISHED',
      ),
    [workflows.data],
  )

  useEffect(() => {
    if (!workflowId && runnable.length > 0) setWorkflowId(runnable[0]?.id ?? '')
  }, [runnable, workflowId])

  useEffect(() => {
    if (params.get('new') === '1') setNewOpen(true)
  }, [params])

  const createRun = useMutation({
    mutationFn: () => {
      const workflow = runnable.find((item) => item.id === workflowId)
      if (!workflow) throw new Error('Select a workflow with a published version.')
      return api.createRun({
        workflow_id: workflow.id,
        version_id: workflow.current_version_id ?? undefined,
        seed: Number.parseInt(seed, 10) || 0,
        background: true,
      })
    },
    onSuccess: (run) => {
      setNewOpen(false)
      setFormError(null)
      setParams((current) => {
        const next = new URLSearchParams(current)
        next.delete('new')
        return next
      })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
      void queryClient.invalidateQueries({ queryKey: ['decisions'] })
      navigate(`/runs/${run.id}`)
    },
    onError: (error: Error) => setFormError(error.message),
  })

  const updateFilter = (key: string, value: string) => {
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('new')
      if (value) next.set(key, value)
      else next.delete(key)
      return next
    })
  }

  const hasFilters = Boolean(status || origin || workflowFilter)
  const selectedWorkflow = runnable.find((workflow) => workflow.id === workflowId)

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Monitor"
        title="Execution Inspector"
        description="Every run, its status, its verdict and its audit chain. Open a run to walk the event log action by action."
        actions={
          <>
            <Button
              variant="ghost"
              icon="refresh"
              loading={runs.isFetching}
              onClick={() => void runs.refetch()}
            >
              Refresh
            </Button>
            <Button variant="primary" icon="play" onClick={() => setNewOpen(true)}>
              New execution
            </Button>
          </>
        }
      />

      <Card>
        <CardBody className="filter-bar">
          <label className="filter-field">
            <span>Status</span>
            <Select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value)
                updateFilter('status', event.target.value)
              }}
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {statuses.map((item) => (
                <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>
              ))}
            </Select>
          </label>
          <label className="filter-field">
            <span>Origin</span>
            <Select
              value={origin}
              onChange={(event) => {
                setOrigin(event.target.value)
                updateFilter('origin', event.target.value)
              }}
              aria-label="Filter by origin"
            >
              <option value="">All origins</option>
              <option value="LIVE">Live</option>
              <option value="DEMO">Demo fixtures</option>
            </Select>
          </label>
          <label className="filter-field filter-field--wide">
            <span>Workflow</span>
            <Select
              value={workflowFilter}
              onChange={(event) => updateFilter('workflow', event.target.value)}
              aria-label="Filter by workflow"
            >
              <option value="">All workflows</option>
              {(workflows.data ?? []).map((workflow) => (
                <option key={workflow.id} value={workflow.id}>{workflow.name}</option>
              ))}
            </Select>
          </label>
          <div className="filter-bar__right">
            {runs.data ? <Badge tone="neutral">{pluralize(runs.data.length, 'run')}</Badge> : null}
            {hasFilters ? (
              <Button
                size="sm"
                variant="ghost"
                icon="close"
                onClick={() => {
                  setStatus('')
                  setOrigin('')
                  setParams(new URLSearchParams())
                }}
              >
                Clear filters
              </Button>
            ) : null}
          </div>
        </CardBody>
      </Card>

      {runs.isError ? (
        <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
      ) : runs.isPending ? (
        <Card>
          <CardBody>
            <LoadingState label="Loading executions" rows={5} />
          </CardBody>
        </Card>
      ) : (
        <Card>
          <CardHeader
            title="Executions"
            subtitle={
              hasFilters
                ? 'Filtered view. Clearing filters shows the full audit store.'
                : 'Newest first. Duration is measured on the injected backend clock.'
            }
            icon="activity"
            action={
              runs.data && runs.data.length > 0 ? (
                <span className="fine-print">{pluralize(runs.data.length, 'record')}</span>
              ) : null
            }
          />
          <CardBody className="card__body--flush">
            <RunTable
              runs={runs.data ?? []}
              emptyAction={
                <Button variant="primary" icon="play" onClick={() => setNewOpen(true)}>
                  Start an execution
                </Button>
              }
            />
          </CardBody>
        </Card>
      )}

      <Modal
        open={newOpen}
        onClose={() => {
          setNewOpen(false)
          setFormError(null)
          setParams((current) => {
            const next = new URLSearchParams(current)
            next.delete('new')
            return next
          })
        }}
        title="New execution"
        description="Runs execute in the backend only. Nothing is started from the browser."
        footer={
          <>
            <Button variant="ghost" onClick={() => setNewOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              icon="play"
              loading={createRun.isPending}
              disabled={!selectedWorkflow}
              onClick={() => createRun.mutate()}
            >
              Start execution
            </Button>
          </>
        }
      >
        <div className="stack-md">
          {formError ? <InlineNotice tone="danger" title="Execution not started">{formError}</InlineNotice> : null}
          {workflows.isPending ? <LoadingState label="Loading workflows" rows={2} /> : null}
          {workflows.data && runnable.length === 0 ? (
            <InlineNotice tone="warning" title="No runnable workflows">
              Every version is a draft. Open Workflow Studio, create a workflow and publish a version first.
            </InlineNotice>
          ) : null}
          <FormField label="Workflow" htmlFor="run-workflow" required>
            <Select
              id="run-workflow"
              value={workflowId}
              onChange={(event) => setWorkflowId(event.target.value)}
              disabled={runnable.length === 0}
            >
              {runnable.length === 0 ? <option value="">No published workflows</option> : null}
              {runnable.map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name} · v{workflow.latest_version} published
                </option>
              ))}
            </Select>
          </FormField>
          <FormField
            label="Random seed"
            htmlFor="execution-seed"
            hint="Fault selection and retries are seeded; the same seed reproduces the same path."
          >
            <TextInput
              id="execution-seed"
              type="number"
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </FormField>
          {selectedWorkflow ? (
            <dl className="kv">
              <div><dt>Version</dt><dd>v{selectedWorkflow.latest_version}</dd></div>
              <div><dt>Origin will be</dt><dd><Badge tone="success">Live</Badge></dd></div>
              <div><dt>Driver</dt><dd className="mono">simulated-v1</dd></div>
            </dl>
          ) : null}
        </div>
      </Modal>
    </div>
  )
}