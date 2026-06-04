import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import type { FactEdit, FactWrite, Focus, Policy } from '../types'

// Query keys are namespaced by the signed-in key so switching accounts never bleeds cache.
const ns = () => useAuth.getState().apiKey ?? 'anon'
export const qk = {
  memories: () => ['memories', ns()] as const,
  graph: () => ['graph', ns()] as const,
  focus: () => ['focus', ns()] as const,
  policy: () => ['policy', ns()] as const,
  profile: () => ['profile', ns()] as const,
  working: () => ['working', ns()] as const,
  conflicts: () => ['conflicts', ns()] as const,
  health: () => ['health'] as const,
}

export function useHealth() {
  return useQuery({ queryKey: qk.health(), queryFn: api.health, staleTime: 30_000, retry: false })
}

export function useMemories() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.memories(), queryFn: api.memories, enabled, retry: false })
}

export function useGraph() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.graph(), queryFn: api.graph, enabled, retry: false })
}

export function useFocus() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.focus(), queryFn: api.focus, enabled, retry: false })
}

/** Invalidate everything that a write can change (facts, profile, graph). */
function useInvalidateMemory() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: qk.memories() })
    qc.invalidateQueries({ queryKey: qk.graph() })
    qc.invalidateQueries({ queryKey: qk.focus() })
    qc.invalidateQueries({ queryKey: qk.profile() })
  }
}

export function useStructuredProfile() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.profile(), queryFn: api.structuredProfile, enabled, retry: false })
}

export function useWorking() {
  const enabled = !!useAuth((s) => s.apiKey)
  return useQuery({ queryKey: qk.working(), queryFn: () => api.working(), enabled, retry: false })
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
      qc.invalidateQueries({ queryKey: qk.memories() })
      qc.invalidateQueries({ queryKey: qk.profile() })
      qc.invalidateQueries({ queryKey: qk.graph() })
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
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.working() }),
  })
}

export function useRemember() {
  const invalidate = useInvalidateMemory()
  return useMutation({
    mutationFn: (content: string) => api.remember(content),
    onSuccess: invalidate,
  })
}

export function useRecall() {
  return useMutation({ mutationFn: (query: string) => api.recall(query) })
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
      qc.invalidateQueries({ queryKey: qk.memories() }) // persona may change
    },
  })
}
