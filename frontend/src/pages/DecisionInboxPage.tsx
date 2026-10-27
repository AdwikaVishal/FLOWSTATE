import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useDecisions } from '../api/queries'
import type { BlockerEvidence, BlockerKind, Decision, DecisionHistory } from '../api/types'
import { Icon, type IconName } from '../components/Icon'
import { Badge, OriginBadge, type BadgeTone } from '../components/ui/Badge'
import { Button, buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader } from '../components/ui/Card'
import { CopyButton } from '../components/ui/CopyButton'
import { FormField, Select, TextArea, TextInput } from '../components/ui/Fields'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../components/ui/States'
import {
  formatDate,
  formatValue,
  outcomeTone,
  relativeTime,
  shortHash,
  shortId,
  statusLabel,
} from '../lib/format'

const openStatuses = new Set(['REQUESTED', 'NOTIFIED', 'DEFERRED', 'INFO_PROVIDED'])

function isOpen(decision: Decision): boolean {
  return openStatuses.has(decision.status)
}

const blockerMeta: Record<BlockerKind, { label: string; tone: BadgeTone; icon: IconName }> = {
  MISSING_INPUT: { label: 'Missing input', tone: 'warning', icon: 'file' },
  VALIDATION_FAILED: { label: 'Validation failed', tone: 'danger', icon: 'alert' },
  CONFLICTING_EVIDENCE: { label: 'Conflicting evidence', tone: 'danger', icon: 'alert' },
  PERMISSION_REQUIRED: { label: 'Permission required', tone: 'warning', icon: 'lock' },
  HUMAN_APPROVAL_REQUIRED: { label: 'Approval required', tone: 'info', icon: 'lock' },
  TOOL_UNAVAILABLE: { label: 'Tool unavailable', tone: 'danger', icon: 'zap' },
  UNKNOWN_OUTCOME: { label: 'Unknown outcome', tone: 'warning', icon: 'circleDot' },
  RECOVERY_PREREQUISITE_MISSING: { label: 'Recovery prerequisite', tone: 'warning', icon: 'shield' },
}

function BlockerBadge({ kind }: { kind: BlockerKind | null }) {
  if (!kind) return <Badge tone="neutral">Unclassified</Badge>
  const meta = blockerMeta[kind] ?? { label: statusLabel(kind), tone: 'neutral' as BadgeTone }
  return (
    <Badge tone={meta.tone} icon={meta.icon}>
      {meta.label}
    </Badge>
  )
}

function validationTone(validation: string): BadgeTone {
  if (['PASS', 'VALID'].includes(validation)) return 'success'
  if (['FAIL', 'EXPIRED', 'MISSING'].includes(validation)) return 'danger'
  return 'neutral'
}

function evidenceValue(value: Record<string, unknown>): string {
  if ('value' in value) {
    const raw = value.value
    if (raw === null || raw === undefined) return '—'
    return typeof raw === 'object' ? JSON.stringify(raw) : String(raw)
  }
  if ('event_type' in value) {
    return `${String(value.event_type)} · seq ${String(value.seq ?? '—')}`
  }
  return formatValue(value)
}

function conditionLabel(condition: Record<string, unknown>): string {
  const code = condition.code ? String(condition.code).replaceAll('_', ' ') : null
  const check =
    condition.path !== undefined
      ? `${String(condition.path)} ${String(condition.op ?? '')} ${formatValue(condition.value)}`
      : null
  return [code, check].filter(Boolean).join(' — ') || 'Condition'
}

function DecisionListItem({
  decision,
  active,
  onSelect,
}: {
  decision: Decision
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      className={`decision-item${active ? ' is-active' : ''}`}
      onClick={onSelect}
      aria-current={active ? 'true' : undefined}
    >
      <div className="decision-item__top">
        <BlockerBadge kind={decision.kind} />
        <span className="fine-print">{relativeTime(decision.requested_at)}</span>
      </div>
      <strong className="decision-item__question">{decision.question}</strong>
      <div className="decision-item__meta">
        <span>{decision.workflow_name || 'Workflow'}</span>
        <span className="mono">node {decision.node_key || decision.action_ordinal}</span>
        <Badge tone={outcomeTone(decision.status)}>{statusLabel(decision.status)}</Badge>
        {decision.origin === 'DEMO' ? <OriginBadge origin="DEMO" /> : null}
      </div>
    </button>
  )
}

function EvidenceTable({ evidence }: { evidence: BlockerEvidence[] }) {
  if (evidence.length === 0) {
    return (
      <EmptyState
        icon="file"
        title="No evidence recorded"
        description="This blocker did not resolve any persisted fact."
        compact
      />
    )
  }
  return (
    <div className="evidence-table-wrap">
      <table className="evidence-table">
        <thead>
          <tr>
            <th scope="col">Fact</th>
            <th scope="col">Source</th>
            <th scope="col">Value</th>
            <th scope="col">Check</th>
            <th scope="col">Observed</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((item) => (
            <tr key={item.id}>
              <td>
                <strong>{item.label}</strong>
                <span className="fine-print">{item.relation}</span>
                {item.record_ref ? (
                  <span className="fine-print mono">{item.record_ref}</span>
                ) : null}
              </td>
              <td>
                <span className="mono evidence-source">{item.source_path}</span>
                <span className="fine-print">{item.source_kind.replaceAll('_', ' ').toLowerCase()}</span>
              </td>
              <td className="mono evidence-value">{evidenceValue(item.value_json)}</td>
              <td>
                <Badge tone={validationTone(item.validation)}>{statusLabel(item.validation)}</Badge>
                <span className="fine-print evidence-detail">{item.detail}</span>
              </td>
              <td>
                <span>{formatDate(item.observed_at)}</span>
                {item.fresh_until ? (
                  <span className="fine-print">fresh until {formatDate(item.fresh_until)}</span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const historyTone: Record<DecisionHistory['action'], BadgeTone> = {
  APPROVE: 'success',
  RESUME: 'info',
  REJECT: 'danger',
  PROVIDE_INFO: 'accent',
  DEFER: 'warning',
}

function HistoryList({ history }: { history: DecisionHistory[] }) {
  if (history.length === 0) {
    return (
      <EmptyState
        icon="clock"
        title="No decision actions yet"
        description="Every approve, reject, defer or supplied value lands here with the acting identity and role."
        compact
      />
    )
  }
  return (
    <ol className="history-list">
      {history.map((entry) => (
        <li key={entry.id}>
          <span className={`history-dot history-dot--${historyTone[entry.action] ?? 'neutral'}`} />
          <div className="history-body">
            <div className="history-head">
              <Badge tone={historyTone[entry.action] ?? 'neutral'}>
                {statusLabel(entry.action)}
              </Badge>
              <span>{entry.actor}</span>
              <Badge tone="neutral" mono>
                {entry.actor_role || '—'}
              </Badge>
              <span className="fine-print">{relativeTime(entry.created_at)}</span>
            </div>
            <div className="fine-print">
              Outcome: {statusLabel(entry.outcome)}
              {entry.note ? ` · ${entry.note}` : ''}
            </div>
          </div>
        </li>
      ))}
    </ol>
  )
}

export function DecisionInboxPage() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [openOnly, setOpenOnly] = useState(true)
  const [kind, setKind] = useState<BlockerKind | ''>('')
  const decisions = useDecisions(openOnly, kind || null)

  const [dialog, setDialog] = useState<'approve' | 'reject' | 'provide' | 'defer' | null>(null)
  const [note, setNote] = useState('')
  const [infoValues, setInfoValues] = useState<Record<string, string>>({})
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const selected = useMemo(() => {
    const requested = params.get('decision')
    if (requested) {
      const match = decisions.data?.find((decision) => decision.id === requested)
      if (match) return match
    }
    return decisions.data?.[0] ?? null
  }, [decisions.data, params])

  useEffect(() => {
    if (!selected) return
    if (params.get('decision') === selected.id) return
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        next.set('decision', selected.id)
        return next
      },
      { replace: true },
    )
  }, [params, selected, setParams])

  const selectDecision = (decision: Decision) => {
    setNotice(null)
    setActionError(null)
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.set('decision', decision.id)
      return next
    })
  }

  const invalidate = () => {
    for (const key of ['decisions', 'runs', 'overview', 'audit']) {
      void queryClient.invalidateQueries({ queryKey: [key] })
    }
  }

  const onSuccess = (message: string) => {
    setDialog(null)
    setNote('')
    setInfoValues({})
    setActionError(null)
    setNotice(message)
    // Keep the decision that was just handled on screen: a decided record drops
    // out of the open queue, and jumping to a different blocker mid-task loses
    // the operator's context.
    setOpenOnly(false)
    invalidate()
  }

  const onError = (error: Error) => setActionError(error.message)

  const approve = useMutation({
    mutationFn: () => api.approveDecision(selected?.id ?? '', note.trim()),
    onSuccess: (decision) =>
      onSuccess(
        `Authorized by ${decision.decided_role ?? 'a human'}. Preconditions and evidence were re-checked, then the run resumed.`,
      ),
    onError,
  })

  const reject = useMutation({
    mutationFn: () => api.rejectDecision(selected?.id ?? '', note.trim()),
    onSuccess: () => onSuccess('Rejected. The run stopped safely and nothing gated was executed.'),
    onError,
  })

  const provideInfo = useMutation({
    mutationFn: () =>
      api.provideDecisionInfo(selected?.id ?? '', infoValues, note.trim()),
    onSuccess: () =>
      onSuccess('Information recorded. The blocker was re-evaluated before the run could move.'),
    onError,
  })

  const defer = useMutation({
    mutationFn: () => api.deferDecision(selected?.id ?? '', note.trim()),
    onSuccess: () => onSuccess('Deferred. The run stays blocked and the decision stays in the inbox.'),
    onError,
  })

  const pending =
    approve.isPending || reject.isPending || provideInfo.isPending || defer.isPending

  const openCount = decisions.data?.filter(isOpen).length ?? 0
  const requiredInfo = selected?.input_spec ?? []
  const missingRequired = requiredInfo
    .filter((field) => field.required)
    .some((field) => !infoValues[field.key]?.trim())

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Operate"
        title="Decision Inbox"
        description="Only the minimum unresolved choice for each run is surfaced, with the blocker classification, the persisted evidence and the conditions that will be re-checked before resuming."
        actions={
          <>
            <div className="segmented" role="group" aria-label="Decision scope">
              <button
                type="button"
                className={openOnly ? 'is-active' : ''}
                onClick={() => setOpenOnly(true)}
              >
                Open
              </button>
              <button
                type="button"
                className={!openOnly ? 'is-active' : ''}
                onClick={() => setOpenOnly(false)}
              >
                All
              </button>
            </div>
            <Button
              variant="ghost"
              icon="refresh"
              loading={decisions.isFetching}
              onClick={() => void decisions.refetch()}
            >
              Refresh
            </Button>
          </>
        }
      />

      {notice ? (
        <InlineNotice tone="success" title="Decision recorded">
          {notice}
        </InlineNotice>
      ) : null}
      {actionError ? (
        <InlineNotice tone="danger" title="Decision not applied">
          {actionError}
        </InlineNotice>
      ) : null}

      <div className="filter-bar">
        <label className="filter-bar__field">
          <span>Blocker cause</span>
          <Select value={kind} onChange={(event) => setKind(event.target.value as BlockerKind | '')}>
            <option value="">All causes</option>
            {(Object.keys(blockerMeta) as BlockerKind[]).map((item) => (
              <option key={item} value={item}>
                {blockerMeta[item].label}
              </option>
            ))}
          </Select>
        </label>
        <span className="filter-bar__count">
          {decisions.data?.length ?? 0} in view · {openCount} open
        </span>
      </div>

      {decisions.isError ? (
        <ErrorState error={decisions.error} onRetry={() => void decisions.refetch()} />
      ) : decisions.isPending ? (
        <LoadingState rows={6} label="Loading decisions" />
      ) : decisions.data?.length === 0 ? (
        <Card>
          <CardBody>
            <EmptyState
              icon="inbox"
              title={openOnly ? 'Inbox clear' : 'No decisions recorded'}
              description={
                kind
                  ? 'No decision matches this blocker cause.'
                  : openOnly
                    ? 'No run is waiting on a human. Blocked nodes appear here the moment the engine cannot proceed.'
                    : 'No decision has ever been requested in this workspace.'
              }
              action={
                kind ? (
                  <Button variant="secondary" onClick={() => setKind('')}>
                    Clear the filter
                  </Button>
                ) : openOnly ? (
                  <Button variant="secondary" onClick={() => setOpenOnly(false)}>
                    View all decisions
                  </Button>
                ) : (
                  <Link to="/runs?new=1" className={buttonClass('primary', 'md')}>
                    Run a workflow
                  </Link>
                )
              }
            />
          </CardBody>
        </Card>
      ) : (
        <div className="inbox-layout">
          <Card className="inbox-list">
            <CardHeader
              title={openOnly ? 'Waiting for a human' : 'All decisions'}
              subtitle={`${decisions.data?.length ?? 0} in view`}
              icon="inbox"
            />
            <CardBody className="card__body--flush">
              <div className="decision-list" role="list">
                {decisions.data?.map((decision) => (
                  <DecisionListItem
                    key={decision.id}
                    decision={decision}
                    active={selected?.id === decision.id}
                    onSelect={() => selectDecision(decision)}
                  />
                ))}
              </div>
            </CardBody>
          </Card>

          {selected ? (
            <div className="stack-md">
              <Card>
                <CardHeader
                  title={selected.question}
                  subtitle={
                    <span className="row-actions">
                      <BlockerBadge kind={selected.kind} />
                      <Badge tone={outcomeTone(selected.status)}>
                        {statusLabel(selected.status)}
                      </Badge>
                      <OriginBadge origin={selected.origin} />
                      <span className="mono">node {selected.node_key || selected.action_ordinal}</span>
                    </span>
                  }
                  icon="lock"
                  action={<CopyButton value={selected.id} label="Copy decision id" />}
                />
                <CardBody className="stack-md">
                  {isOpen(selected) ? (
                    selected.can_decide ? (
                      <InlineNotice tone="warning" title="This run is paused">
                        Approving does not make the run safe on its own: authority, evidence
                        freshness and node preconditions are re-checked before it resumes.
                      </InlineNotice>
                    ) : (
                      <InlineNotice tone="danger" title="Your acting role cannot decide this">
                        This blocker requires <strong>{statusLabel(selected.required_role ?? 'ADMIN')}</strong>.
                        Switch the acting role in the top bar, or hand this to the right person. The run
                        stays blocked either way.
                      </InlineNotice>
                    )
                  ) : (
                    <InlineNotice
                      tone={outcomeTone(selected.status) === 'danger' ? 'danger' : 'info'}
                      title={`Decision ${statusLabel(selected.status).toLowerCase()}`}
                    >
                      {selected.decided_by
                        ? `Recorded by ${selected.decided_by} ${relativeTime(selected.decided_at)}.`
                        : 'This decision is closed.'}
                      {selected.capability_token_id
                        ? ` Capability token ${shortId(selected.capability_token_id, 12)} was issued.`
                        : ''}
                    </InlineNotice>
                  )}

                  <div className="decision-grid">
                    <div>
                      <h3 className="subheading">Why it is blocked</h3>
                      <p className="body-text">{selected.blocker?.detail || selected.reason}</p>
                      {selected.policy_citation ? (
                        <blockquote className="policy-quote">{selected.policy_citation}</blockquote>
                      ) : null}
                      {selected.blocker ? (
                        <div className="row-actions">
                          <Badge tone="neutral" mono>
                            {selected.blocker.code}
                          </Badge>
                          <Badge tone="neutral">
                            requires {statusLabel(selected.blocker.required_role)}
                          </Badge>
                          <Badge tone={selected.blocker.status === 'OPEN' ? 'warning' : 'success'}>
                            blocker {statusLabel(selected.blocker.status)}
                          </Badge>
                        </div>
                      ) : null}
                    </div>
                    <dl className="kv">
                      <div>
                        <dt>Workflow</dt>
                        <dd>
                          {selected.workflow_id ? (
                            <Link to={`/workflows/${selected.workflow_id}`} className="text-link">
                              {selected.workflow_name || 'Open workflow'}
                            </Link>
                          ) : (
                            selected.workflow_name || '—'
                          )}
                          {selected.version ? (
                            <span className="fine-print"> v{selected.version}</span>
                          ) : null}
                        </dd>
                      </div>
                      <div>
                        <dt>Run</dt>
                        <dd>
                          <Link to={`/runs/${selected.run_id}`} className="text-link mono">
                            {shortId(selected.run_id, 12)}
                          </Link>
                        </dd>
                      </div>
                      <div>
                        <dt>Run status</dt>
                        <dd>{statusLabel(selected.run_status)}</dd>
                      </div>
                      <div>
                        <dt>Requested</dt>
                        <dd>{formatDate(selected.requested_at)}</dd>
                      </div>
                      <div>
                        <dt>Expires</dt>
                        <dd>
                          {formatDate(selected.expires_at)} ({relativeTime(selected.expires_at)})
                        </dd>
                      </div>
                      {selected.deferred_until ? (
                        <div>
                          <dt>Deferred until</dt>
                          <dd>{formatDate(selected.deferred_until)}</dd>
                        </div>
                      ) : null}
                    </dl>
                  </div>

                  <div>
                    <h3 className="subheading">Checked again before resume</h3>
                    {selected.resume_conditions.length === 0 ? (
                      <p className="body-text fine-print">
                        No additional conditions were declared. Node preconditions and evidence
                        freshness are still re-evaluated.
                      </p>
                    ) : (
                      <ul className="condition-list">
                        {selected.resume_conditions.map((condition, index) => (
                          <li key={`${conditionLabel(condition)}-${index}`}>
                            <Icon name="check" size={13} />
                            <span>{conditionLabel(condition)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  {requiredInfo.length > 0 ? (
                    <div>
                      <h3 className="subheading">Information this blocker needs</h3>
                      <ul className="condition-list">
                        {requiredInfo.map((field) => (
                          <li key={field.key}>
                            <Icon name="file" size={13} />
                            <span>
                              {field.label}
                              {field.required ? ' (required)' : ''}
                              {field.path ? (
                                <span className="fine-print mono"> {field.path}</span>
                              ) : null}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  <div>
                    <h3 className="subheading">Permitted choices</h3>
                    <ul className="option-list">
                      {selected.options.map((option) => (
                        <li
                          key={option.value}
                          className={
                            selected.recommended_option === option.value ? 'is-recommended' : ''
                          }
                        >
                          <div className="option-list__head">
                            <strong>{option.label}</strong>
                            {selected.recommended_option === option.value ? (
                              <Badge tone="info">Recommended</Badge>
                            ) : null}
                          </div>
                          {option.consequence ? <p>{option.consequence}</p> : null}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="row-actions decision-actions">
                    {isOpen(selected) ? (
                      <>
                        <Button
                          variant="primary"
                          icon="check"
                          disabled={!selected.can_decide}
                          title={
                            selected.can_decide
                              ? undefined
                              : `Requires the ${statusLabel(selected.required_role ?? 'ADMIN')} role`
                          }
                          onClick={() => {
                            setNote('')
                            setActionError(null)
                            setDialog('approve')
                          }}
                        >
                          Approve
                        </Button>
                        {selected.permitted_actions.includes('provide_info') ? (
                          <Button
                            variant="secondary"
                            icon="file"
                            disabled={!selected.can_decide}
                            onClick={() => {
                              setNote('')
                              setInfoValues({})
                              setActionError(null)
                              setDialog('provide')
                            }}
                          >
                            Provide information
                          </Button>
                        ) : null}
                        <Button
                          variant="ghost"
                          icon="clock"
                          onClick={() => {
                            setNote('')
                            setActionError(null)
                            setDialog('defer')
                          }}
                        >
                          Defer
                        </Button>
                        <Button
                          variant="danger"
                          icon="ban"
                          disabled={!selected.can_decide}
                          onClick={() => {
                            setNote('')
                            setActionError(null)
                            setDialog('reject')
                          }}
                        >
                          Reject
                        </Button>
                      </>
                    ) : (
                      <>
                        <Link
                          to={`/runs/${selected.run_id}`}
                          className={buttonClass('secondary', 'md')}
                        >
                          <Icon name="activity" size={15} />
                          <span>Inspect the run</span>
                        </Link>
                        {selected.workflow_id ? (
                          <Link
                            to={`/workflows/${selected.workflow_id}`}
                            className={buttonClass('ghost', 'md')}
                          >
                            <Icon name="workflow" size={15} />
                            <span>Open the workflow</span>
                          </Link>
                        ) : null}
                      </>
                    )}
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader
                  title="Evidence packet"
                  subtitle="Persisted facts the question relies on. Values are read from run state and the audit log; nothing here is model-generated."
                  icon="file"
                  action={
                    <Badge tone="neutral" mono title={selected.blocker?.evidence_hash}>
                      hash {shortHash(selected.blocker?.evidence_hash ?? '')}
                    </Badge>
                  }
                />
                <CardBody>
                  <EvidenceTable evidence={selected.evidence_items} />
                  {selected.evidence.length > 0 ? (
                    <div className="evidence-artifacts">
                      <span className="fine-print">Sealed evidence artefacts</span>
                      <ul className="evidence-list">
                        {selected.evidence.map((evidence) => (
                          <li key={evidence.id}>
                            <span className="evidence-list__icon">
                              <Icon name="file" size={15} />
                            </span>
                            <div className="evidence-list__body">
                              <div className="evidence-list__head">
                                <strong>{statusLabel(evidence.kind)}</strong>
                                <Badge
                                  tone={evidence.verified ? 'success' : 'danger'}
                                  icon={evidence.verified ? 'check' : 'alert'}
                                >
                                  {evidence.verified ? 'hash verified' : 'hash mismatch'}
                                </Badge>
                              </div>
                              <span className="mono evidence-list__hash" title={evidence.sha256}>
                                sha256 {shortHash(evidence.sha256)}
                              </span>
                              <span className="fine-print">
                                {evidence.mime_type} · captured {formatDate(evidence.captured_at)}
                              </span>
                            </div>
                            <CopyButton value={evidence.sha256} label="Copy evidence hash" />
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </CardBody>
              </Card>

              <Card>
                <CardHeader
                  title="Decision history"
                  subtitle="Append-only. Every attempt is recorded, including refusals."
                  icon="clock"
                  action={<Badge tone="neutral">{selected.history.length}</Badge>}
                />
                <CardBody>
                  <HistoryList history={selected.history} />
                </CardBody>
              </Card>
            </div>
          ) : null}
        </div>
      )}

      <Modal
        open={dialog !== null}
        onClose={() => {
          if (!pending) setDialog(null)
        }}
        title={
          dialog === 'approve'
            ? 'Approve this blocker?'
            : dialog === 'reject'
              ? 'Reject this run?'
              : dialog === 'provide'
                ? 'Provide the missing information'
                : 'Defer this decision'
        }
        description={
          dialog === 'approve'
            ? 'Authority, evidence freshness and preconditions are re-checked before the run resumes.'
            : dialog === 'reject'
              ? 'The run stops safely. No gated action executes.'
              : dialog === 'provide'
                ? 'Values are written to the run state, then the blocker is re-evaluated.'
                : 'The run stays blocked; the decision remains in the inbox.'
        }
        footer={
          <>
            <Button variant="ghost" disabled={pending} onClick={() => setDialog(null)}>
              Cancel
            </Button>
            {dialog === 'approve' ? (
              <Button
                variant="primary"
                icon="check"
                loading={approve.isPending}
                onClick={() => approve.mutate()}
              >
                Re-check & resume
              </Button>
            ) : dialog === 'reject' ? (
              <Button
                variant="danger"
                icon="ban"
                loading={reject.isPending}
                onClick={() => reject.mutate()}
              >
                Reject and stop
              </Button>
            ) : dialog === 'provide' ? (
              <Button
                variant="primary"
                icon="file"
                loading={provideInfo.isPending}
                disabled={missingRequired}
                onClick={() => provideInfo.mutate()}
              >
                Submit information
              </Button>
            ) : (
              <Button
                variant="secondary"
                icon="clock"
                loading={defer.isPending}
                onClick={() => defer.mutate()}
              >
                Defer
              </Button>
            )}
          </>
        }
      >
        <div className="stack-md">
          {actionError ? (
            <InlineNotice tone="danger" title="The decision was not applied">
              {actionError}
            </InlineNotice>
          ) : null}
          {selected ? (
            <dl className="kv">
              <div>
                <dt>Blocker</dt>
                <dd>
                  <BlockerBadge kind={selected.kind} />
                </dd>
              </div>
              <div>
                <dt>Required role</dt>
                <dd>{statusLabel(selected.required_role ?? 'ADMIN')}</dd>
              </div>
              <div>
                <dt>Run</dt>
                <dd className="mono">{shortId(selected.run_id, 12)}</dd>
              </div>
              <div>
                <dt>Expires</dt>
                <dd>{relativeTime(selected.expires_at)}</dd>
              </div>
            </dl>
          ) : null}

          {dialog === 'provide' && selected ? (
            <div className="stack-md">
              {selected.input_spec.map((field) => (
                <FormField
                  key={field.key}
                  label={field.label}
                  htmlFor={`decision-input-${field.key}`}
                  required={field.required}
                  hint={field.path ? `Written to ${field.path}` : undefined}
                >
                  {field.type === 'datetime' ? (
                    <TextInput
                      id={`decision-input-${field.key}`}
                      value={infoValues[field.key] ?? ''}
                      placeholder={field.placeholder ?? '2027-04-01T00:00:00Z'}
                      onChange={(event) =>
                        setInfoValues((current) => ({
                          ...current,
                          [field.key]: event.target.value,
                        }))
                      }
                    />
                  ) : (
                    <TextInput
                      id={`decision-input-${field.key}`}
                      value={infoValues[field.key] ?? ''}
                      placeholder={field.placeholder ?? ''}
                      onChange={(event) =>
                        setInfoValues((current) => ({
                          ...current,
                          [field.key]: event.target.value,
                        }))
                      }
                    />
                  )}
                </FormField>
              ))}
            </div>
          ) : null}

          <FormField
            label={dialog === 'approve' ? 'Note (optional)' : 'Reason'}
            htmlFor="decision-note"
            hint="Stored in the decision history and the audit chain with your actor identity."
          >
            <TextArea
              id="decision-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              placeholder={
                dialog === 'approve'
                  ? 'Variance accepted; PO amendment attached.'
                  : dialog === 'reject'
                    ? 'Vendor has not explained the variance.'
                    : dialog === 'defer'
                      ? 'Waiting for the controller.'
                      : 'Source of the corrected value.'
              }
            />
          </FormField>
        </div>
      </Modal>
    </div>
  )
}