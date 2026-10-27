import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, evidenceUrl } from '../api/client'
import { useCaptures } from '../api/queries'
import type {
  CaptureActionPatch,
  CaptureSession,
  CaptureWorkflowResponse,
  CapturedAction,
  RunDetail,
} from '../api/types'
import { Icon } from '../components/Icon'
import { Badge, StatusBadge } from '../components/ui/Badge'
import { Button, buttonClass } from '../components/ui/Button'
import { Card, CardBody, CardHeader } from '../components/ui/Card'
import { FormField, Select, TextArea, TextInput } from '../components/ui/Fields'
import { Modal } from '../components/ui/Modal'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, InlineNotice, LoadingState } from '../components/ui/States'
import { formatDate, formatDuration, pluralize, shortId, statusLabel } from '../lib/format'

const recordingStatuses = new Set(['RECORDING', 'PAUSED'])

function captureTone(status: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (status === 'SAVED') return 'success'
  if (status === 'VALIDATED') return 'info'
  if (status === 'REVIEW') return 'warning'
  if (status === 'FAILED') return 'danger'
  return 'neutral'
}

function actionTone(status: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (status === 'VALIDATED' || status === 'RESOLVED') return 'success'
  if (status === 'UNRESOLVED') return 'danger'
  if (status === 'AMBIGUOUS') return 'warning'
  return 'neutral'
}

function selectorLabel(action: CapturedAction): string {
  const first = action.selector[0]
  if (!first) return 'No selector'
  if (first.strategy === 'role') return `${first.role} · ${first.name ?? first.value}`
  return `${first.strategy} · ${first.value}`
}

function ActionCard({
  action,
  onEdit,
  onAccept,
  busy,
}: {
  action: CapturedAction
  onEdit: (action: CapturedAction) => void
  onAccept: (action: CapturedAction) => void
  busy: boolean
}) {
  const before = action.page_before?.page ?? '—'
  const after = action.page_after?.page ?? '—'
  return (
    <article className="capture-action">
      <div className="capture-action__head">
        <span className="capture-action__ordinal mono">{String(action.ordinal + 1).padStart(2, '0')}</span>
        <div className="capture-action__title">
          <strong>{action.label}</strong>
          <span className="capture-action__meta">
            <Badge tone="neutral" mono>
              {action.kind}
            </Badge>
            <span>{selectorLabel(action)}</span>
          </span>
        </div>
        <Badge tone={actionTone(action.status)} icon={action.status === 'AMBIGUOUS' ? 'alert' : 'circleDot'}>
          {statusLabel(action.status)}
        </Badge>
        <Button size="sm" variant="ghost" icon="file" onClick={() => onEdit(action)}>
          Edit
        </Button>
      </div>

      <div className="capture-action__grid">
        <div>
          <span className="label">Page transition</span>
          <p className="mono capture-action__value">
            {before} → {after}
          </p>
        </div>
        <div>
          <span className="label">Input</span>
          <p className="mono capture-action__value">
            {action.input_param ? `inputs.${action.input_param}` : action.input_value ?? 'No input'}
          </p>
        </div>
        <div>
          <span className="label">Expected result</span>
          <p className="capture-action__value">{action.expected_result || '—'}</p>
        </div>
      </div>

      {action.ambiguous ? (
        <InlineNotice tone="warning" title="Review needed">
          <p>
            {action.ambiguity_reason === 'only_css_selector'
              ? 'Only a CSS selector was available. Add an accessible locator or accept the risk explicitly.'
              : action.ambiguity_reason === 'no_observed_transition'
                ? 'The click produced no observable page transition, so the expected result cannot be inferred.'
                : action.ambiguity_reason === 'sensitive_value_redacted'
                  ? 'The recorded value looked sensitive and was not stored. Supply a safe parameter before saving.'
                  : 'The recorded identifier looks reusable data. Confirm the parameter name and value.'}
          </p>
          <div className="row-actions">
            <Button size="sm" icon="check" loading={busy} onClick={() => onAccept(action)}>
              Accept and resolve
            </Button>
            <Button size="sm" variant="ghost" icon="file" onClick={() => onEdit(action)}>
              Edit instead
            </Button>
          </div>
        </InlineNotice>
      ) : null}

      <details className="capture-action__details">
        <summary>
          <Icon name="chevronRight" size={12} /> Ranked locators and conditions
        </summary>
        <ol className="selector-list">
          {action.selector.map((selector) => (
            <li key={`${selector.strategy}-${selector.value}`}>
              <Badge tone={selector.strategy === 'role' ? 'success' : 'neutral'} mono>
                {selector.strategy}
              </Badge>
              <code>{selector.value}</code>
              {selector.score !== undefined ? <span className="muted">score {selector.score}</span> : null}
            </li>
          ))}
        </ol>
        <div className="capture-action__conditions">
          <div>
            <span className="label">Preconditions</span>
            <pre className="code-block">{JSON.stringify(action.preconditions, null, 2)}</pre>
          </div>
          <div>
            <span className="label">Postconditions</span>
            <pre className="code-block">{JSON.stringify(action.postconditions, null, 2)}</pre>
          </div>
        </div>
      </details>
    </article>
  )
}

function ReplayResult({ run }: { run: RunDetail }) {
  const states = [...run.node_states].sort((a, b) => a.ordinal - b.ordinal)
  const screenshots = run.evidence.filter((item) => item.mime_type.startsWith('image/'))
  const tone =
    run.status === 'SUCCEEDED' ? 'success' : run.status === 'AWAITING_RECOVERY' ? 'warning' : 'danger'
  return (
    <div className="stack-md replay-result">
      <div className="replay-result__stats">
        <div>
          <span className="label">Status</span>
          <StatusBadge status={run.status} />
        </div>
        <div>
          <span className="label">Verdict</span>
          <StatusBadge status={run.verdict} />
        </div>
        <div>
          <span className="label">Driver</span>
          <span className="mono">{run.driver}</span>
        </div>
        <div>
          <span className="label">Duration</span>
          <span>{formatDuration(run.started_at, run.ended_at)}</span>
        </div>
      </div>

      <InlineNotice
        tone={tone}
        title={
          run.status === 'SUCCEEDED'
            ? 'Replay verified'
            : run.status === 'AWAITING_RECOVERY'
              ? 'Outcome unresolved — not counted as success'
              : 'Replay failed safely'
        }
      >
        <p>
          {run.failure_message ??
            'Every browser action passed its observed postcondition; the run verified against the learned invariant.'}
        </p>
      </InlineNotice>

      <div className="table-wrap">
        <table className="data-table">
          <caption className="visually-hidden">Replay node results</caption>
          <thead>
            <tr>
              <th>Node</th>
              <th>Type</th>
              <th>Status</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {states.map((state) => (
              <tr key={state.node_key}>
                <td>
                  <span className="mono">{state.node_key}</span>
                  <div className="muted">{state.label}</div>
                </td>
                <td>{statusLabel(state.node_type)}</td>
                <td>
                  <StatusBadge status={state.status} />
                </td>
                <td>
                  {state.error_code ? <span className="mono">{state.error_code}</span> : null}
                  <div className="muted">{state.error_message ?? 'Passed its checks.'}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {screenshots.length > 0 ? (
        <div>
          <span className="label">Screenshots</span>
          <div className="evidence-grid">
            {screenshots.map((item, index) => (
              <a
                key={item.id}
                href={evidenceUrl(run.id, item.id)}
                target="_blank"
                rel="noreferrer"
                className="evidence-thumb"
              >
                <img src={evidenceUrl(run.id, item.id)} alt={`Replay screenshot ${index + 1}`} />
                <span>{shortId(item.id, 10)}</span>
              </a>
            ))}
          </div>
        </div>
      ) : null}

      <Link to={`/runs/${run.id}`} className={buttonClass('secondary', 'sm')}>
        <Icon name="activity" size={14} />
        <span>Open execution timeline</span>
      </Link>
    </div>
  )
}

export function LearningStudioPage() {
  const captures = useCaptures()
  const queryClient = useQueryClient()
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const sessionRef = useRef<CaptureSession | null>(null)
  const queueRef = useRef<Record<string, unknown>[]>([])
  const timerRef = useRef<number | null>(null)
  const flushingRef = useRef(false)

  const [session, setSession] = useState<CaptureSession | null>(null)
  const [label, setLabel] = useState('Invoice lookup and validation')
  const [workflowName, setWorkflowName] = useState('')
  const [publish, setPublish] = useState(true)
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [variant, setVariant] = useState<'default' | 'changed'>('default')
  const [run, setRun] = useState<RunDetail | null>(null)
  const [saved, setSaved] = useState<CaptureWorkflowResponse | null>(null)
  const [editing, setEditing] = useState<CapturedAction | null>(null)
  const [editForm, setEditForm] = useState<CaptureActionPatch>({})
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    sessionRef.current = session
    if (!session) return
    setWorkflowName((current) => current || `Learned: ${session.label}`)
    setInputs((current) => {
      if (Object.keys(current).length > 0) return current
      const next: Record<string, string> = {}
      for (const [key, value] of Object.entries(session.suggested_inputs ?? {})) {
        next[key] = String(value ?? '')
      }
      return next
    })
  }, [session])

  const flushQueue = useCallback(async () => {
    if (flushingRef.current) return
    const active = sessionRef.current
    if (!active || queueRef.current.length === 0) return
    flushingRef.current = true
    const batch = queueRef.current.splice(0)
    try {
      const updated = await api.recordCaptureEvents(active.id, batch)
      setSession(updated)
      sessionRef.current = updated
      void queryClient.invalidateQueries({ queryKey: ['captures'] })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The captured action could not be stored.')
    } finally {
      flushingRef.current = false
      if (queueRef.current.length > 0) {
        timerRef.current = window.setTimeout(() => {
          timerRef.current = null
          void flushQueue()
        }, 120)
      }
    }
  }, [queryClient])

  const scheduleFlush = useCallback(() => {
    if (timerRef.current !== null) return
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      void flushQueue()
    }, 150)
  }, [flushQueue])

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return
      const data = event.data as {
        source?: string
        type?: string
        event?: Record<string, unknown>
      }
      if (!data || data.source !== 'flowstate-demo') return
      if (data.type === 'ready') {
        iframeRef.current?.contentWindow?.postMessage(
          {
            source: 'flowstate-console',
            type: 'recording',
            enabled: sessionRef.current?.status === 'RECORDING',
          },
          '*',
        )
        return
      }
      if (data.type === 'capture-event' && data.event && sessionRef.current?.status === 'RECORDING') {
        queueRef.current.push(data.event)
        scheduleFlush()
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [scheduleFlush])

  useEffect(() => {
    iframeRef.current?.contentWindow?.postMessage(
      {
        source: 'flowstate-console',
        type: 'recording',
        enabled: session?.status === 'RECORDING',
      },
      '*',
    )
  }, [session?.status])

  const start = useMutation({
    mutationFn: () => api.startCapture({ label: label.trim() }),
    onSuccess: (created) => {
      setSession(created)
      setRun(null)
      setSaved(null)
      setError(null)
      setNotice(null)
      setInputs({})
      setWorkflowName('')
      void queryClient.invalidateQueries({ queryKey: ['captures'] })
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const pause = useMutation({
    mutationFn: () => api.pauseCapture(session!.id),
    onSuccess: (updated) => setSession(updated),
    onError: (caught: Error) => setError(caught.message),
  })

  const resume = useMutation({
    mutationFn: () => api.resumeCapture(session!.id),
    onSuccess: (updated) => setSession(updated),
    onError: (caught: Error) => setError(caught.message),
  })

  const stop = useMutation({
    mutationFn: async () => {
      await flushQueue()
      await new Promise((resolve) => window.setTimeout(resolve, 140))
      await flushQueue()
      return api.stopCapture(session!.id)
    },
    onSuccess: (updated) => {
      setSession(updated)
      setNotice(
        `Recording stopped. ${pluralize(updated.actions.length, 'action')} captured; review each step before saving.`,
      )
      void queryClient.invalidateQueries({ queryKey: ['captures'] })
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const validate = useMutation({
    mutationFn: () => api.validateCapture(session!.id),
    onSuccess: (updated) => {
      setSession(updated)
      setError(null)
      setNotice(
        updated.validation.ok
          ? `All recorded selectors resolved against the controlled demo (${updated.validation.passed}/${updated.validation.checked}).`
          : 'At least one selector no longer resolves. The workflow cannot be published.',
      )
      void queryClient.invalidateQueries({ queryKey: ['captures'] })
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const save = useMutation({
    mutationFn: () =>
      api.saveCaptureWorkflow(session!.id, {
        name: workflowName.trim() || undefined,
        publish,
      }),
    onSuccess: (response) => {
      setSession(response.session)
      setSaved(response)
      setError(null)
      setNotice(
        `Saved ${response.workflow.name} v${response.version.version} as ${response.version.status.toLowerCase()}.`,
      )
      void queryClient.invalidateQueries({ queryKey: ['captures'] })
      void queryClient.invalidateQueries({ queryKey: ['workflows'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const patch = useMutation({
    mutationFn: ({ actionId, payload }: { actionId: string; payload: CaptureActionPatch }) =>
      api.updateCapturedAction(session!.id, actionId, payload),
    onSuccess: (updated) => {
      setSession(updated)
      setEditing(null)
      setError(null)
      setNotice('Review changes saved. Validate again before publishing.')
      void queryClient.invalidateQueries({ queryKey: ['captures'] })
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const replay = useMutation({
    mutationFn: () =>
      api.replayCapture(session!.id, {
        inputs,
        variant,
      }),
    onSuccess: (result) => {
      setRun(result)
      setError(null)
      setNotice(
        result.status === 'SUCCEEDED'
          ? 'Replay verified every typed step against the demo app.'
          : result.status === 'AWAITING_RECOVERY'
            ? 'The outcome is unknown and was not retried. See the blocked step for details.'
            : 'The replay failed safely; no run was marked successful.',
      )
      void queryClient.invalidateQueries({ queryKey: ['captures'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const reset = () => {
    setSession(null)
    setRun(null)
    setSaved(null)
    setError(null)
    setNotice(null)
    setEditing(null)
    setInputs({})
    setWorkflowName('')
  }

  const openEditor = (action: CapturedAction) => {
    setEditing(action)
    setEditForm({
      label: action.label,
      kind: action.kind,
      input_param: action.input_param ?? '',
      input_value: action.input_value ?? '',
      expected_result: action.expected_result,
      accept_ambiguity: Boolean(action.resolution?.accepted),
      resolution_note: action.resolution?.note ?? '',
    })
  }

  const acceptAmbiguity = (action: CapturedAction) =>
    patch.mutate({
      actionId: action.id,
      payload: {
        accept_ambiguity: true,
        resolution_note: 'Reviewed in the learning studio and accepted explicitly.',
      },
    })

  const isRecording = session ? recordingStatuses.has(session.status) : false
  const canValidate =
    session?.status === 'REVIEW' || session?.status === 'VALIDATED' || session?.status === 'SAVED'
  const validationOk = Boolean(session?.validation?.ok)
  const hasAmbiguities = (session?.unresolved_ambiguities.length ?? 0) > 0

  return (
    <div className="stack-lg">
      <PageHeader
        eyebrow="Build"
        title="Learning Studio"
        description="Record a real interaction with the controlled invoice demo, review every inferred step, then replay the saved workflow with different permitted inputs."
        actions={
          session ? (
            <Button variant="ghost" icon="plus" onClick={reset}>
              New recording
            </Button>
          ) : null
        }
      />

      {error ? (
        <InlineNotice tone="danger" title="Action failed">
          <p>{error}</p>
        </InlineNotice>
      ) : null}
      {notice ? (
        <InlineNotice tone="info" title="Learning studio">
          <p>{notice}</p>
        </InlineNotice>
      ) : null}

      {!session ? (
        <div className="grid grid--main-rail">
          <div className="stack-lg">
            <Card>
              <CardHeader
                title="Start a controlled recording"
                subtitle="Only the Northwind invoice demo is recordable. Interactions are captured as typed candidates; nothing is published automatically."
                icon="workflow"
              />
              <CardBody className="stack-md">
                <FormField
                  label="Recording label"
                  htmlFor="capture-label"
                  hint="Used as the default workflow name. You can rename it before saving."
                  required
                >
                  <TextInput
                    id="capture-label"
                    value={label}
                    onChange={(event) => setLabel(event.target.value)}
                    placeholder="Invoice lookup and validation"
                  />
                </FormField>
                <div className="row-actions">
                  <Button
                    variant="primary"
                    icon="play"
                    loading={start.isPending}
                    disabled={label.trim().length < 3}
                    onClick={() => start.mutate()}
                  >
                    Start recording
                  </Button>
                  <span className="muted">
                    A headless Playwright driver validates and replays; the demo below is the live recordable surface.
                  </span>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="What is captured" icon="shield" />
              <CardBody>
                <div className="learning-principles">
                  <div>
                    <strong>Structured, not a script</strong>
                    <p>Action type, ranked locators, page transition, expected and observed result, and pre/postconditions.</p>
                  </div>
                  <div>
                    <strong>No secrets</strong>
                    <p>Password, token, card and account-shaped values are redacted before they leave the demo page.</p>
                  </div>
                  <div>
                    <strong>Review before publishing</strong>
                    <p>Ambiguous selectors and unclear intent block save until a human resolves them.</p>
                  </div>
                  <div>
                    <strong>Verified replay</strong>
                    <p>A replay is a real engine run: postconditions, timeouts, screenshots and the audit chain all apply.</p>
                  </div>
                </div>
              </CardBody>
            </Card>
          </div>

          <div className="stack-md">
            <Card>
              <CardHeader title="Recent recordings" subtitle="Select a session to review or replay it." icon="layers" />
              <CardBody className="card__body--flush">
                {captures.isPending ? (
                  <div className="card__body">
                    <LoadingState rows={4} />
                  </div>
                ) : captures.isError ? (
                  <div className="card__body">
                    <ErrorState error={captures.error} onRetry={() => void captures.refetch()} />
                  </div>
                ) : (captures.data ?? []).length === 0 ? (
                  <div className="card__body">
                    <EmptyState
                      icon="workflow"
                      title="No recordings yet"
                      description="Start a recording and interact with the invoice demo. The first session will appear here."
                      compact
                    />
                  </div>
                ) : (
                  <ul className="capture-history">
                    {(captures.data ?? []).map((item) => (
                      <li key={item.id}>
                        <button type="button" className="capture-history__item" onClick={() => setSession(item)}>
                          <span>
                            <strong>{item.label}</strong>
                            <small>
                              {formatDate(item.created_at)} · {pluralize(item.event_count, 'action')}
                            </small>
                          </span>
                          <Badge tone={captureTone(item.status)}>{statusLabel(item.status)}</Badge>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>
          </div>
        </div>
      ) : (
        <>
          <div className="learning-statusbar">
            <div>
              <span className="label">Session</span>
              <strong>{session.label}</strong>
              <span className="mono muted">{shortId(session.id, 12)}</span>
            </div>
            <Badge tone={captureTone(session.status)} icon="circleDot">
              {statusLabel(session.status)}
            </Badge>
            <span>
              <Icon name="layers" size={13} /> {pluralize(session.event_count, 'action')}
            </span>
            <span className="mono">{session.demo_url}</span>
          </div>

          {isRecording ? (
            <div className="learning-stage">
              <Card>
                <CardHeader
                  title="Controlled demo app"
                  subtitle="Interact with the invoice lookup below. Values are captured as parameters, never as code."
                  icon="workflow"
                  action={
                    <div className="row-actions">
                      {session.status === 'RECORDING' ? (
                        <Button
                          size="sm"
                          icon="pause"
                          loading={pause.isPending}
                          onClick={() => pause.mutate()}
                        >
                          Pause
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          icon="play"
                          loading={resume.isPending}
                          onClick={() => resume.mutate()}
                        >
                          Resume
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="danger"
                        icon="ban"
                        loading={stop.isPending}
                        onClick={() => stop.mutate()}
                      >
                        Stop and review
                      </Button>
                    </div>
                  }
                />
                <CardBody className="demo-embed">
                  <iframe
                    ref={iframeRef}
                    title="Northwind invoice demo"
                    src={`/demo-app/?capture=1&capture_session=${session.id}`}
                    className="demo-frame"
                  />
                </CardBody>
              </Card>

              <div className="stack-md">
                <Card>
                  <CardHeader
                    title="Captured so far"
                    subtitle={
                      session.status === 'PAUSED'
                        ? 'Recording is paused. Resume to capture more actions.'
                        : 'Type a supplier and invoice, load the invoice, then validate it.'
                    }
                    icon="list"
                  />
                  <CardBody>
                    {session.actions.length === 0 ? (
                      <EmptyState
                        icon="circleDot"
                        title="Waiting for the first action"
                        description="Fill in Supplier ID and Invoice ID in the demo. The recorder will add them here."
                        compact
                      />
                    ) : (
                      <ul className="capture-live-list">
                        {session.actions.map((action) => (
                          <li key={action.id}>
                            <span className="mono">{String(action.ordinal + 1).padStart(2, '0')}</span>
                            <span>
                              <strong>{action.label}</strong>
                              <small>
                                {action.kind} · {selectorLabel(action)}
                              </small>
                            </span>
                            {action.ambiguous ? <Badge tone="warning">Review</Badge> : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardBody>
                </Card>
                <Card>
                  <CardBody>
                    <p className="muted">
                      Recommended path: change <span className="mono">Supplier ID</span> and{' '}
                      <span className="mono">Invoice ID</span>, click <strong>Load invoice</strong>, then{' '}
                      <strong>Validate invoice</strong>. Stop when the validation result is visible.
                    </p>
                  </CardBody>
                </Card>
              </div>
            </div>
          ) : (
            <div className="grid grid--main-rail">
              <div className="stack-lg">
                <Card>
                  <CardHeader
                    title="Captured action graph"
                    subtitle="Every step is a typed candidate. Review locators and intent before it becomes a workflow."
                    icon="gitBranch"
                    action={
                      <div className="row-actions">
                        <Button
                          size="sm"
                          icon="check"
                          loading={validate.isPending}
                          disabled={!canValidate || session.actions.length === 0}
                          onClick={() => validate.mutate()}
                        >
                          Validate selectors
                        </Button>
                      </div>
                    }
                  />
                  <CardBody className="stack-md">
                    {hasAmbiguities ? (
                      <InlineNotice tone="warning" title="Unresolved actions">
                        <p>
                          Ordinals {session.unresolved_ambiguities.map((item) => item + 1).join(', ')} need
                          a human decision. Accept them explicitly or edit the action before saving.
                        </p>
                      </InlineNotice>
                    ) : null}
                    {session.validation.checked ? (
                      <InlineNotice tone={validationOk ? 'success' : 'danger'} title="Selector validation">
                        <p>
                          {validationOk
                            ? `${session.validation.passed}/${session.validation.checked} recorded steps resolved to a unique element.`
                            : 'The demo structure no longer matches at least one recorded selector. Publishing is blocked.'}
                        </p>
                        {session.validation.actions ? (
                          <ul className="validation-list">
                            {session.validation.actions.map((entry, index) => (
                              <li key={`${entry.node_key ?? index}-${entry.ordinal ?? index}`}>
                                <Badge tone={entry.ok ? 'success' : 'danger'} icon={entry.ok ? 'check' : 'x'}>
                                  {entry.ok ? 'Resolved' : entry.code ?? 'Failed'}
                                </Badge>
                                <span className="mono">{entry.node_key ?? `step ${index + 1}`}</span>
                                <span className="muted">
                                  {entry.strategy}
                                  {entry.value ? ` · ${entry.value}` : ''}
                                </span>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </InlineNotice>
                    ) : null}

                    {session.actions.map((action) => (
                      <ActionCard
                        key={action.id}
                        action={action}
                        onEdit={openEditor}
                        onAccept={acceptAmbiguity}
                        busy={patch.isPending}
                      />
                    ))}
                  </CardBody>
                </Card>

                {saved ? (
                  <Card>
                    <CardHeader
                      title="Saved workflow"
                      subtitle={`${saved.workflow.name} · v${saved.version.version} ${saved.version.status}`}
                      icon="check"
                      action={
                        <Link to={`/workflows/${saved.workflow.id}`} className={buttonClass('secondary', 'sm')}>
                          <Icon name="workflow" size={14} />
                          <span>Open workflow</span>
                        </Link>
                      }
                    />
                    <CardBody>
                      <div className="saved-grid">
                        <div>
                          <span className="label">Content hash</span>
                          <span className="mono">{saved.version.content_hash.slice(0, 16)}…</span>
                        </div>
                        <div>
                          <span className="label">Actions</span>
                          <span>{saved.version.action_count}</span>
                        </div>
                        <div>
                          <span className="label">Inputs</span>
                          <span className="mono">
                            {Object.keys(
                              (saved.version.input_schema as { properties?: Record<string, unknown> })
                                .properties ?? {},
                            ).join(', ') || 'none'}
                          </span>
                        </div>
                        <div>
                          <span className="label">Invariants</span>
                          <span>{saved.version.invariant_count}</span>
                        </div>
                      </div>
                    </CardBody>
                  </Card>
                ) : null}
              </div>

              <div className="stack-md">
                <Card>
                  <CardHeader title="Save workflow" icon="layers" />
                  <CardBody className="stack-md">
                    <FormField label="Workflow name" htmlFor="learning-workflow-name" required>
                      <TextInput
                        id="learning-workflow-name"
                        value={workflowName}
                        onChange={(event) => setWorkflowName(event.target.value)}
                      />
                    </FormField>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={publish}
                        onChange={(event) => setPublish(event.target.checked)}
                      />
                      <span>Publish after saving</span>
                    </label>
                    {publish && !validationOk ? (
                      <p className="muted">
                        Validate the recorded selectors against the demo before publishing. You can still
                        save a draft.
                      </p>
                    ) : null}
                    <Button
                      variant="primary"
                      icon="check"
                      loading={save.isPending}
                      disabled={hasAmbiguities || (publish && !validationOk)}
                      onClick={() => save.mutate()}
                    >
                      {session.status === 'SAVED' ? 'Create new version' : 'Save workflow'}
                    </Button>
                    {session.workflow_id ? (
                      <Link to={`/workflows/${session.workflow_id}`} className="text-link">
                        Open saved workflow <Icon name="arrowRight" size={13} />
                      </Link>
                    ) : null}
                  </CardBody>
                </Card>

                <Card>
                  <CardHeader
                    title="Replay in Chromium"
                    subtitle="A real engine run against the controlled demo. Inputs are validated against the saved schema."
                    icon="play"
                  />
                  <CardBody className="stack-md">
                    {Object.keys(inputs).length === 0 ? (
                      <p className="muted">No reusable inputs were parameterized in this recording.</p>
                    ) : (
                      Object.keys(inputs).map((key) => (
                        <FormField
                          key={key}
                          label={statusLabel(key)}
                          htmlFor={`replay-${key}`}
                          hint={`Bound to inputs.${key}`}
                        >
                          <TextInput
                            id={`replay-${key}`}
                            value={inputs[key]}
                            onChange={(event) =>
                              setInputs((current) => ({ ...current, [key]: event.target.value }))
                            }
                          />
                        </FormField>
                      ))
                    )}
                    <FormField
                      label="Demo structure"
                      htmlFor="replay-variant"
                      hint="Use the changed layout to prove selector drift fails safely."
                    >
                      <Select
                        id="replay-variant"
                        value={variant}
                        onChange={(event) => setVariant(event.target.value as 'default' | 'changed')}
                      >
                        <option value="default">Current layout</option>
                        <option value="changed">Changed layout (drift test)</option>
                      </Select>
                    </FormField>
                    <Button
                      variant="primary"
                      icon="play"
                      loading={replay.isPending}
                      disabled={
                        session.status !== 'SAVED' || session.version_status !== 'PUBLISHED'
                      }
                      onClick={() => replay.mutate()}
                    >
                      Execute replay
                    </Button>
                    {!session.workflow_id ? (
                      <p className="muted">Save and publish the workflow before replaying it.</p>
                    ) : session.status !== 'SAVED' ? (
                      <p className="muted">
                        Save the reviewed changes before replay. Unsaved edits are never replayed.
                      </p>
                    ) : session.version_status !== 'PUBLISHED' ? (
                      <p className="muted">
                        This version is {session.version_status?.toLowerCase() ?? 'not published'}.
                        Publish it from Workflow Studio before replaying.
                      </p>
                    ) : null}
                  </CardBody>
                </Card>

                {session.recent_runs.length > 0 ? (
                  <Card>
                    <CardHeader title="Recent replays" icon="activity" />
                    <CardBody className="card__body--flush">
                      <ul className="capture-history">
                        {session.recent_runs.map((item) => (
                          <li key={item.id}>
                            <Link to={`/runs/${item.id}`} className="capture-history__item">
                              <span>
                                <strong className="mono">{shortId(item.id, 10)}</strong>
                                <small>
                                  {formatDate(item.created_at)} · {item.driver}
                                </small>
                              </span>
                              <StatusBadge status={item.status} />
                            </Link>
                          </li>
                        ))}
                      </ul>
                    </CardBody>
                  </Card>
                ) : null}
              </div>
            </div>
          )}

          {run ? (
            <Card>
              <CardHeader
                title="Replay result"
                subtitle={`Run ${shortId(run.id, 12)} · ${formatDate(run.created_at)}`}
                icon="activity"
                action={
                  <Link to={`/runs/${run.id}`} className={buttonClass('secondary', 'sm')}>
                    <Icon name="activity" size={14} />
                    <span>Open in Execution Inspector</span>
                  </Link>
                }
              />
              <CardBody>
                <ReplayResult run={run} />
              </CardBody>
            </Card>
          ) : null}
        </>
      )}

      <Modal
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
        title="Review captured action"
        description="Correct the inferred intent. FLOWSTATE will not guess: an accepted ambiguity is recorded as a human decision."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon="check"
              loading={patch.isPending}
              disabled={!editForm.label}
              onClick={() => {
                if (!editing) return
                patch.mutate({
                  actionId: editing.id,
                  payload: {
                    ...editForm,
                    input_param: editForm.input_param || null,
                    input_value: editForm.input_value || null,
                  },
                })
              }}
            >
              Save changes
            </Button>
          </>
        }
      >
        {editing ? (
          <div className="stack-md">
            <div className="grid grid--2">
              <FormField label="Label" htmlFor="edit-label" required>
                <TextInput
                  id="edit-label"
                  value={editForm.label ?? ''}
                  onChange={(event) => setEditForm((current) => ({ ...current, label: event.target.value }))}
                />
              </FormField>
              <FormField label="Action type" htmlFor="edit-kind">
                <Select
                  id="edit-kind"
                  value={editForm.kind ?? 'CLICK'}
                  onChange={(event) => setEditForm((current) => ({ ...current, kind: event.target.value }))}
                >
                  {['CLICK', 'TYPE', 'SELECT', 'SUBMIT', 'NAVIGATE'].map((kind) => (
                    <option key={kind} value={kind}>
                      {kind}
                    </option>
                  ))}
                </Select>
              </FormField>
            </div>
            <div className="grid grid--2">
              <FormField
                label="Input parameter"
                htmlFor="edit-param"
                hint="Bind this field to a reusable workflow input, for example invoice_id."
              >
                <TextInput
                  id="edit-param"
                  value={editForm.input_param ?? ''}
                  onChange={(event) =>
                    setEditForm((current) => ({ ...current, input_param: event.target.value }))
                  }
                  placeholder="invoice_id"
                />
              </FormField>
              <FormField label="Captured value" htmlFor="edit-value" hint="Used as the replay default.">
                <TextInput
                  id="edit-value"
                  value={editForm.input_value ?? ''}
                  onChange={(event) =>
                    setEditForm((current) => ({ ...current, input_value: event.target.value }))
                  }
                />
              </FormField>
            </div>
            <FormField label="Expected result" htmlFor="edit-expected">
              <TextArea
                id="edit-expected"
                rows={3}
                value={editForm.expected_result ?? ''}
                onChange={(event) =>
                  setEditForm((current) => ({ ...current, expected_result: event.target.value }))
                }
              />
            </FormField>
            {editing.ambiguous ? (
              <>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={Boolean(editForm.accept_ambiguity)}
                    onChange={(event) =>
                      setEditForm((current) => ({ ...current, accept_ambiguity: event.target.checked }))
                    }
                  />
                  <span>I reviewed this ambiguity and accept it</span>
                </label>
                <FormField label="Resolution note" htmlFor="edit-note">
                  <TextArea
                    id="edit-note"
                    rows={2}
                    value={editForm.resolution_note ?? ''}
                    onChange={(event) =>
                      setEditForm((current) => ({ ...current, resolution_note: event.target.value }))
                    }
                  />
                </FormField>
              </>
            ) : null}
          </div>
        ) : null}
      </Modal>

      {session && isRecording ? null : (
        <Card>
          <CardHeader title="Recordings" subtitle="History of controlled capture sessions." icon="layers" />
          <CardBody className="card__body--flush">
            {captures.isPending ? (
              <div className="card__body">
                <LoadingState rows={3} />
              </div>
            ) : (
              <ul className="capture-history">
                {(captures.data ?? []).map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`capture-history__item${item.id === session?.id ? ' is-active' : ''}`}
                      onClick={() => {
                        setSession(item)
                        setRun(null)
                        setSaved(null)
                      }}
                    >
                      <span>
                        <strong>{item.label}</strong>
                        <small>
                          {formatDate(item.created_at)} · {pluralize(item.event_count, 'action')}
                        </small>
                      </span>
                      <Badge tone={captureTone(item.status)}>{statusLabel(item.status)}</Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  )
}