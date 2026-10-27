import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, getActorRole, streamRunEvents, type LiveConnectionState } from './client'
import type { BlockerKind } from './types'

export const queryKeys = {
  health: ['health'] as const,
  readiness: ['readiness'] as const,
  system: ['system'] as const,
  overview: ['overview'] as const,
  demo: ['demo'] as const,
  workflows: ['workflows'] as const,
  workflow: (id: string) => ['workflows', id] as const,
  version: (workflowId: string, versionId: string) =>
    ['workflows', workflowId, 'versions', versionId] as const,
  runs: (filters: Record<string, string | undefined>) => ['runs', filters] as const,
  run: (id: string) => ['runs', id] as const,
  decisions: (openOnly: boolean, kind: string | null) =>
    ['decisions', openOnly, kind, getActorRole()] as const,
  scenarios: ['scenarios'] as const,
  audit: ['audit'] as const,
  recoveries: ['recoveries'] as const,
  regressionTests: ['regression-tests'] as const,
  failureModes: ['feedback', 'failure-modes'] as const,
  captures: ['captures'] as const,
  capture: (id: string) => ['captures', id] as const,
}

export function useHealth() {
  return useQuery({ queryKey: queryKeys.health, queryFn: ({ signal }) => api.health(signal) })
}

export function useDemo() {
  return useQuery({ queryKey: queryKeys.demo, queryFn: ({ signal }) => api.demo(signal) })
}

export function useReadiness() {
  return useQuery({
    queryKey: queryKeys.readiness,
    queryFn: ({ signal }) => api.readiness(signal),
  })
}

export function useSystem() {
  return useQuery({ queryKey: queryKeys.system, queryFn: ({ signal }) => api.system(signal) })
}

export function useOverview() {
  return useQuery({
    queryKey: queryKeys.overview,
    queryFn: ({ signal }) => api.overview(signal),
  })
}

export function useWorkflows() {
  return useQuery({
    queryKey: queryKeys.workflows,
    queryFn: ({ signal }) => api.workflows(signal),
  })
}

export function useWorkflow(id: string) {
  return useQuery({
    queryKey: queryKeys.workflow(id),
    queryFn: ({ signal }) => api.workflow(id, signal),
    enabled: Boolean(id),
  })
}

export function useVersion(workflowId: string, versionId: string | null) {
  return useQuery({
    queryKey: queryKeys.version(workflowId, versionId ?? 'none'),
    queryFn: ({ signal }) => api.version(workflowId, versionId as string, signal),
    enabled: Boolean(workflowId && versionId),
  })
}

export function useTimeline(id: string) {
  return useQuery({
    queryKey: ['runs', id, 'timeline'] as const,
    queryFn: ({ signal }) => api.timeline(id, signal),
    enabled: Boolean(id),
  })
}

export function useRunEventStream(runId: string, initialSequence: number, enabled: boolean) {
  const queryClient = useQueryClient()
  const [connection, setConnection] = useState<LiveConnectionState>('connecting')
  const sequenceRef = useRef(initialSequence)
  sequenceRef.current = Math.max(sequenceRef.current, initialSequence)

  useEffect(() => {
    if (!enabled || !runId) return
    const controller = new AbortController()
    let invalidateTimer: number | undefined
    setConnection('connecting')
    void streamRunEvents(
      runId,
      sequenceRef.current,
      controller.signal,
      () => {
        if (invalidateTimer !== undefined) window.clearTimeout(invalidateTimer)
        invalidateTimer = window.setTimeout(() => {
          void queryClient.invalidateQueries({ queryKey: ['runs', runId] })
          void queryClient.invalidateQueries({ queryKey: ['runs'] })
          void queryClient.invalidateQueries({ queryKey: ['runs', runId, 'timeline'] })
          void queryClient.invalidateQueries({ queryKey: ['decisions'] })
          void queryClient.invalidateQueries({ queryKey: ['overview'] })
          void queryClient.invalidateQueries({ queryKey: ['audit'] })
        }, 120)
      },
      setConnection,
    )
    return () => {
      controller.abort()
      if (invalidateTimer !== undefined) window.clearTimeout(invalidateTimer)
    }
  }, [enabled, queryClient, runId])

  return connection
}

export function useRuns(filters: {
  status?: string
  origin?: string
  workflowId?: string
} = {}) {
  return useQuery({
    queryKey: queryKeys.runs(filters),
    queryFn: ({ signal }) => api.runs(filters, signal),
  })
}

export function useRun(id: string) {
  return useQuery({
    queryKey: queryKeys.run(id),
    queryFn: ({ signal }) => api.run(id, signal),
    enabled: Boolean(id),
  })
}

export function useDecisions(openOnly: boolean, kind: BlockerKind | null = null) {
  return useQuery({
    queryKey: queryKeys.decisions(openOnly, kind),
    queryFn: ({ signal }) => api.decisions({ openOnly, kind }, signal),
  })
}

export function useScenarios() {
  return useQuery({
    queryKey: queryKeys.scenarios,
    queryFn: ({ signal }) => api.scenarios(signal),
  })
}

export function useAudit() {
  return useQuery({ queryKey: queryKeys.audit, queryFn: ({ signal }) => api.audit(signal) })
}

export function useRecoveries() {
  return useQuery({
    queryKey: queryKeys.recoveries,
    queryFn: ({ signal }) => api.recoveries(signal),
  })
}

export function useRegressionTests() {
  return useQuery({
    queryKey: queryKeys.regressionTests,
    queryFn: ({ signal }) => api.regressionTests(signal),
  })
}

export function useFailureModes() {
  return useQuery({
    queryKey: queryKeys.failureModes,
    queryFn: ({ signal }) => api.failureModes(signal),
  })
}
export function useRunRecoveries(runId: string) {
  return useQuery({
    queryKey: ['runs', runId, 'recoveries'] as const,
    queryFn: ({ signal }) => api.runRecoveries(runId, signal),
    enabled: Boolean(runId),
  })
}

export function useCaptures() {
  return useQuery({ queryKey: queryKeys.captures, queryFn: ({ signal }) => api.captures(signal) })
}

export function useCapture(id: string | null) {
  return useQuery({
    queryKey: queryKeys.capture(id ?? 'none'),
    queryFn: ({ signal }) => api.capture(id as string, signal),
    enabled: Boolean(id),
  })
}
