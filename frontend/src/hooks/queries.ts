import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import type { FactEdit, FactWrite, Focus } from '../types'

// Query keys are namespaced by the signed-in key so switching accounts never bleeds cache.
const ns = () => useAuth.getState().apiKey ?? 'anon'
export const qk = {
  memories: () => ['memories', ns()] as const,
  graph: () => ['graph', ns()] as const,
  focus: () => ['focus', ns()] as const,
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
  }
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
