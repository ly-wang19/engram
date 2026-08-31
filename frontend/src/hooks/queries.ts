import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  type GraphQuery,
  type MemoriesQuery,
  type RecallOptions,
  type RememberOptions,
  type SessionsQuery,
} from '../lib/api'
import { useAuth } from '../store/auth'
import type { FactEdit, FactWrite, Focus, Policy } from '../types'

// Query keys are namespaced by the signed-in key so switching accounts never bleeds cache.
const ns = () => useAuth.getState().apiKey ?? 'anon'
export const qk = {
  memoriesRoot: () => ['memories', ns()] as const,
  memories: (params: MemoriesQuery = {}) => ['memories', ns(), params] as const,
  sessionsRoot: () => ['sessions', ns()] as const,
  sessions: (params: SessionsQuery = {}) => ['sessions', ns(), params] as const,
  agentStatus: (sessionId?: string) => ['agent-status', ns(), sessionId ?? 'default'] as const,
  sessionReport: (sessionId: string) => ['session-report', ns(), sessionId] as const,
  graphRoot: () => ['graph', ns()] as const,
  graph: (params: GraphQuery = {}) => ['graph', ns(), params] as const,
  focus: () => ['focus', ns()] as const,
  policy: () => ['policy', ns()] as const,
  profile: () => ['profile', ns()] as const,
  working: (sessionId?: string) => ['working', ns(), sessionId ?? 'all'] as const,
  conflicts: () => ['conflicts', ns()] as const,
  health: () => ['health'] as const,
  // Namespaced like every other key (see ns() above) — the audit is per-memory-space, so an
  // un-namespaced key would show the previous space's findings, with its one-click bulk delete, after
  // a key switch. Prefix key: invalidating ['audit', ns()] matches every limit the Health page holds.
  auditRoot: () => ['audit', ns()] as const,
  audit: (limit: number) => ['audit', ns(), limit] as const,
}

export function useHealth() {
  return useQuery({ queryKey: qk.health(), queryFn: api.health, staleTime: 30_000, retry: false })
}

export function useMemories(params: MemoriesQuery = {}) {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.memories(params), queryFn: () => api.memories(params), enabled, retry: false })
}

export function useSessions(params: SessionsQuery = {}) {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.sessions(params), queryFn: () => api.sessions(params), enabled, retry: false })
}

export function useAgentStatus(sessionId?: string) {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({
    queryKey: qk.agentStatus(sessionId),
    queryFn: () => api.agentStatus(sessionId),
    enabled,
    retry: false,
    staleTime: 15_000,
  })
}

export function useSessionReport(sessionId: string | null | undefined, includeSensitive = false) {
  const enabled = !!useAuth((s) => s.apiKey) && !!sessionId
  return useQuery({
    queryKey: qk.sessionReport(sessionId ?? ''),
    queryFn: () => api.sessionReport(sessionId!, includeSensitive),
    enabled,
    retry: false,
  })
}

export function useGraph(params: GraphQuery = {}) {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.graph(params), queryFn: () => api.graph(params), enabled, retry: false })
}

export function useFocus() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.focus(), queryFn: api.focus, enabled, retry: false })
}

/** Invalidate everything that a write can change (facts, profile, graph). */
function useInvalidateMemory() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: qk.memoriesRoot() })
    qc.invalidateQueries({ queryKey: qk.sessionsRoot() })
    qc.invalidateQueries({ queryKey: qk.graphRoot() })
    qc.invalidateQueries({ queryKey: qk.focus() })
    qc.invalidateQueries({ queryKey: qk.profile() })
    // The health audit is derived from the same facts. Without this a delete leaves the Health page
    // showing the rows it just erased — and re-clicking its (irreversible) clear button reports
    // "the slot changed since it was checked", blaming the store for the owner's own edit.
    qc.invalidateQueries({ queryKey: qk.auditRoot() })
  }
}

/** Raw episodes, fetched only when something needs to resolve a fact's provenance ids to source text. */
export function useSourceEpisodes(enabled: boolean) {
  return useQuery({
    queryKey: ['source-episodes'],
    queryFn: () => api.memories({ facts_limit: 0, episodes_limit: 200, include_sensitive: true }),
    enabled,
  })
}

export function useAudit(limit = 60) {
  return useQuery({
    queryKey: qk.audit(limit),
    queryFn: () => api.audit(limit),
  })
}

export function useStructuredProfile() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.profile(), queryFn: api.structuredProfile, enabled, retry: false })
}

export function useWorking(sessionId?: string) {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.working(sessionId), queryFn: () => api.working(sessionId), enabled, retry: false })
}

export function useConflicts() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.conflicts(), queryFn: api.conflicts, enabled, retry: false })
}

export function useResolveConflict() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, keep }: { id: string; keep: 'newer' | 'older' | 'both' }) =>
      api.resolveConflict(id, keep),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.conflicts() })
      qc.invalidateQueries({ queryKey: qk.memoriesRoot() })
      qc.invalidateQueries({ queryKey: qk.profile() })
      qc.invalidateQueries({ queryKey: qk.graphRoot() })
    },
  })
}

export function useAddWorking() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ content, kind, ttl }: { content: string; kind?: string; ttl?: number }) =>
      api.addWorking(content, kind, ttl),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.working() }),
  })
}

export function useClearWorking() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (session_id?: string) => api.clearWorking(session_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['working', ns()] }),
  })
}

export function useRemember() {
  const invalidate = useInvalidateMemory()
  return useMutation({
    mutationFn: (req: string | ({ content: string } & RememberOptions)) =>
      typeof req === 'string' ? api.remember(req) : api.remember(req.content, req),
    onSuccess: invalidate,
  })
}

export function useCloseSession() {
  const invalidate = useInvalidateMemory()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (session_id?: string) => api.closeSession(session_id),
    onSuccess: () => {
      invalidate()
      qc.invalidateQueries({ queryKey: ['working', ns()] })
    },
  })
}

export function useRecall() {
  return useMutation({
    mutationFn: (req: string | ({ query: string } & RecallOptions)) =>
      typeof req === 'string' ? api.recall(req) : api.recall(req.query, req),
  })
}

export function useAddFact() {
  const invalidate = useInvalidateMemory()
  return useMutation({ mutationFn: (fact: FactWrite) => api.addFact(fact), onSuccess: invalidate })
}

export function useEditFact() {
  const invalidate = useInvalidateMemory()
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: FactEdit }) => api.editFact(id, patch),
    onSuccess: invalidate,
  })
}

export function useDeleteFact() {
  const invalidate = useInvalidateMemory()
  return useMutation({ mutationFn: (id: string) => api.deleteFact(id), onSuccess: invalidate })
}

export function useClearSlot() {
  const invalidate = useInvalidateMemory()
  return useMutation({
    mutationFn: ({ subject, predicate, count }: { subject: string; predicate: string; count: number }) =>
      api.clearSlot(subject, predicate, count),
    onSuccess: invalidate,
  })
}

export function useSetFocus() {
  const invalidate = useInvalidateMemory()
  return useMutation({ mutationFn: (focus: Partial<Focus>) => api.setFocus(focus), onSuccess: invalidate })
}

export function useForget() {
  const invalidate = useInvalidateMemory()
  return useMutation({ mutationFn: () => api.forget(), onSuccess: invalidate })
}

export function usePolicy() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.policy(), queryFn: api.policy, enabled, retry: false })
}

export function useSetPolicy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (patch: Partial<Policy>) => api.setPolicy(patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.policy() })
      qc.invalidateQueries({ queryKey: qk.memoriesRoot() }) // persona may change
    },
  })
}
