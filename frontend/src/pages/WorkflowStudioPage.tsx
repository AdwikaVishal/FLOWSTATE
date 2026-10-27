import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useWorkflows } from '../api/queries'
import type { WorkflowCreatePayload, WorkflowSummary } from '../api/types'
import { Icon } from '../components/Icon'
import { Badge, OriginBadge } from '../components/ui/Badge'
import { Button, buttonClass } from '../components/ui/Button'
import { Card, CardBody } from '../components/ui/Card'
import { FormField, Select, TextArea, TextInput } from '../components/ui/Fields'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, InlineNotice, PageSkeleton } from '../components/ui/States'
import { formatDateOnly, pluralize, relativeTime } from '../lib/format'

const starter: WorkflowCreatePayload = {
  name: '',
  description: '',
  publish: true,
  actions: [
    {
      ordinal: 0,
      kind: 'ASSERT',
      label: 'Confirm the sandbox session',
      target: { surface: 'session' },
      input: { effects: [] },
      postconditions: [{ path: 'session.authenticated', op: 'eq', value: true }],
      side_effect_class: 'READ_ONLY',
    },
    {
      ordinal: 1,
      kind: 'TYPE',
      label: 'Record the review note',
      target: { surface: 'approval-note', role: 'textbox' },
      input: {
        effects: [
          { op: 'set', path: 'approvals.note', value: 'Reviewed in the FLOWSTATE sandbox.' },
        ],
      },
      postconditions: [{ path: 'approvals.note', op: 'exists' }],
      side_effect_class: 'IDEMPOTENT',
    },
    {
      ordinal: 2,
      kind: 'AUTHORIZE',
      label: 'Approve the invoice',
      target: { surface: 'approve-button', role: 'button', name: 'Approve invoice' },
      input: {
        effects: [{ op: 'set', path: 'invoice.status', value: 'APPROVED' }],
      },
      postconditions: [{ path: 'invoice.status', op: 'eq', value: 'APPROVED' }],
      side_effect_class: 'IRREVERSIBLE',
      requires_authorization: true,
    },
  ],
  invariants: [
    {
      name: 'Invoice is approved',
      description: 'The workflow is only verified if the invoice reaches APPROVED.',
      kind: 'TERMINAL_STATE',
      severity: 'CRITICAL',
      expression: { kind: 'state', path: 'invoice.status', op: 'eq', value: 'APPROVED' },
    },
  ],
}

function templatePayload(template: string, name: string, description: string): WorkflowCreatePayload {
  if (template === 'blank') {
    return { name, description, publish: false, actions: [], invariants: [] }
  }
  return {
    ...starter,
    name,
    description: description || starter.description,
    actions: starter.actions.map((action) => ({ ...action })),
    invariants: starter.invariants.map((invariant) => ({ ...invariant })),
  }
}

function WorkflowCard({ workflow }: { workflow: WorkflowSummary }) {
  return (
    <Link
      to={`/workflows/${workflow.id}`}
      className="workflow-card"
      aria-label={`Open ${workflow.name}`}
    >
      <div className="workflow-card__top">
        <span className="workflow-card__glyph">
          <Icon name="workflow" size={18} />
        </span>
        <div className="workflow-card__badges">
          <OriginBadge origin={workflow.origin} />
          {workflow.latest_status ? (
            <Badge tone={workflow.latest_status === 'PUBLISHED' ? 'success' : 'neutral'}>
              {workflow.latest_status === 'PUBLISHED' ? `v${workflow.latest_version} published` : 'Draft'}
            </Badge>
          ) : (
            <Badge tone="warning">No actions</Badge>
          )}
        </div>
      </div>
      <h2 className="workflow-card__title">{workflow.name}</h2>
      <p className="workflow-card__description">{workflow.description || 'No description provided.'}</p>
      <div className="workflow-card__stats">
        <span>
          <Icon name="layers" size={13} />
          {pluralize(workflow.version_count, 'version')}
        </span>
        <span>
          <Icon name="activity" size={13} />
          {pluralize(workflow.run_count, 'run')}
        </span>
        <span>
          <Icon name="clock" size={13} />
          {formatDateOnly(workflow.created_at)}
        </span>
      </div>
      <div className="workflow-card__footer">
        <span className="mono">{workflow.slug}</span>
        <span className="text-link">
          Open studio
          <Icon name="arrowRight" size={13} />
        </span>
      </div>
    </Link>
  )
}

export function WorkflowStudioPage() {
  const workflows = useWorkflows()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [template, setTemplate] = useState('starter')
  const [formError, setFormError] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return workflows.data ?? []
    return (workflows.data ?? []).filter(
      (workflow) =>
        workflow.name.toLowerCase().includes(term) ||
        workflow.slug.toLowerCase().includes(term) ||
        workflow.description.toLowerCase().includes(term),
    )
  }, [search, workflows.data])

  const create = useMutation({
    mutationFn: () => api.createWorkflow(templatePayload(template, name.trim(), description.trim())),
    onSuccess: (workflow) => {
      void queryClient.invalidateQueries({ queryKey: ['workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
      setOpen(false)
      setName('')
      setDescription('')
      setFormError(null)
      navigate(`/workflows/${workflow.id}`)
    },
    onError: (error: Error) => setFormError(error.message),
  })

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Build"
        title="Workflow Studio"
        description="Versioned, executable workflows with explicit checks. Publishing validates the action set; published versions are immutable."
        actions={
          <>
            <button className={buttonClass('secondary', 'md')} onClick={() => setOpen(true)}>
              <Icon name="plus" size={15} />
              <span>New workflow</span>
            </button>
            <Link to="/runs?new=1" className={buttonClass('primary', 'md')}>
              <Icon name="play" size={15} />
              <span>Run a workflow</span>
            </Link>
          </>
        }
      />

      <div className="studio-toolbar">
        <label className="search-field">
          <Icon name="search" size={15} />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Filter by name, slug or description"
            aria-label="Filter workflows"
          />
        </label>
        <div className="studio-toolbar__meta">
          {workflows.data ? (
            <span>
              {pluralize(workflows.data.length, 'workflow')} ·{' '}
              {pluralize(workflows.data.filter((item) => item.latest_status === 'PUBLISHED').length, 'published')}
            </span>
          ) : null}
        </div>
      </div>

      {workflows.isPending ? <PageSkeleton /> : null}
      {workflows.isError ? (
        <ErrorState error={workflows.error} onRetry={() => void workflows.refetch()} />
      ) : null}

      {workflows.data && workflows.data.length === 0 ? (
        <Card>
          <CardBody>
            <EmptyState
              icon="workflow"
              title="No workflows defined"
              description="Create a workflow from a starter template, or seed the labelled demonstration workspace from Settings."
              action={
                <div className="row-actions">
                  <Button variant="primary" icon="plus" onClick={() => setOpen(true)}>
                    New workflow
                  </Button>
                  <Link to="/settings" className={buttonClass('secondary', 'md')}>
                    Open settings
                  </Link>
                </div>
              }
            />
          </CardBody>
        </Card>
      ) : null}

      {workflows.data && workflows.data.length > 0 && filtered.length === 0 ? (
        <EmptyState
          icon="search"
          title="No workflows match"
          description={`Nothing matches “${search}”. Clear the filter to see all ${pluralize(workflows.data.length, 'workflow')}.`}
          action={
            <Button size="sm" onClick={() => setSearch('')}>
              Clear filter
            </Button>
          }
        />
      ) : null}

      {filtered.length > 0 ? (
        <div className="workflow-grid">
          {filtered.map((workflow) => (
            <WorkflowCard workflow={workflow} key={workflow.id} />
          ))}
          <button className="workflow-card workflow-card--new" onClick={() => setOpen(true)} type="button">
            <span className="workflow-card__glyph">
              <Icon name="plus" size={20} />
            </span>
            <strong>New workflow</strong>
            <p>Start from a verified invoice-review template or a blank draft.</p>
          </button>
        </div>
      ) : null}

      <div className="grid grid--3">
        <Card>
          <CardBody className="capability">
            <span className="capability__icon"><Icon name="layers" size={18} /></span>
            <h3>Versioned by construction</h3>
            <p>Publishing hashes the action set. Editing creates a new draft instead of mutating history.</p>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="capability">
            <span className="capability__icon"><Icon name="shield" size={18} /></span>
            <h3>Checks travel with actions</h3>
            <p>Preconditions run before the action; postconditions decide whether it can claim success.</p>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="capability">
            <span className="capability__icon"><Icon name="lock" size={18} /></span>
            <h3>Humans gate irreversible steps</h3>
            <p>Authorization-gated actions pause the run and open a decision with evidence.</p>
          </CardBody>
        </Card>
      </div>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="New workflow"
        description="The starter template is executable against the in-process invoice sandbox."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={create.isPending}
              disabled={name.trim().length < 3}
              onClick={() => create.mutate()}
            >
              Create workflow
            </Button>
          </>
        }
      >
        <form
          className="stack-md"
          onSubmit={(event) => {
            event.preventDefault()
            if (name.trim().length >= 3) create.mutate()
          }}
        >
          {formError ? <InlineNotice tone="danger" title="Workflow not created">{formError}</InlineNotice> : null}
          <FormField label="Name" htmlFor="workflow-name" hint="A unique slug is derived from the name." required>
            <TextInput
              id="workflow-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Vendor onboarding review"
              maxLength={200}
              required
            />
          </FormField>
          <FormField label="Description" htmlFor="workflow-description" hint="What this workflow verifies, and against which surface.">
            <TextArea
              id="workflow-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Review a vendor record, approve it, and confirm the ledger state."
              rows={3}
              maxLength={2000}
            />
          </FormField>
          <FormField label="Starting point" htmlFor="workflow-template">
            <Select id="workflow-template" value={template} onChange={(event) => setTemplate(event.target.value)}>
              <option value="starter">Invoice review starter — 3 actions, 1 invariant</option>
              <option value="blank">Blank draft — add actions later</option>
            </Select>
          </FormField>
          {template === 'starter' ? (
            <div className="fine-print">
              The starter publishes immediately and pauses at the approval step for a human decision.
            </div>
          ) : (
            <div className="fine-print">
              A blank workflow is created as a draft. It cannot execute until a version contains actions and is published.
            </div>
          )}
        </form>
      </Modal>

      {workflows.data && workflows.data.length > 0 ? (
        <p className="fine-print">
          Showing {filtered.length} of {workflows.data.length}. Last refreshed{' '}
          {relativeTime(new Date(workflows.dataUpdatedAt).toISOString())}.
        </p>
      ) : null}
    </div>
  )
}