import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { request } from '../../lib/api'
import { useAuth } from '../../store/auth'
import type { AuditFinding, MemoryFact } from '../../types'

export interface ReviewPage {
  total: number
  limit: number
  offset: number
  has_more: boolean
  next_offset: number | null
}
export interface HandlingEvent {
  at: number
  status: string
  reason: string
  note: string
  resolution_id?: string | null
}
export interface Question extends MemoryFact {
  review_status: 'open' | 'resolved'
  version: string
  source_changed: boolean
  resolution_id: string | null
  resolution: MemoryFact | null
  resolution_hidden: boolean
  note: string
  history: HandlingEvent[]
}
export interface Finding extends Omit<AuditFinding, 'kind'> {
  kind: string
  id: string
  version: string
  status: 'open' | 'ignored' | 'resolved'
  source_changed: boolean
  present: boolean
  history: HandlingEvent[]
  note: string
}
interface Paged<T> { items: T[]; page: ReviewPage; by_status: Record<string, number> }
export interface HealthQueue extends Paged<Finding> {
  checked: { facts: number; entities: number }
  total_findings: number
  by_kind: Record<string, number>
}
export interface ReviewQuery {
  kind?: string
  status?: string
  q?: string
  include_sensitive: boolean
  limit: number
  offset: number
}
const qs = (params: ReviewQuery) => new URLSearchParams(
  Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]),
).toString()

export function useOutcomeReview(params: ReviewQuery) {
  const apiKey = useAuth(s => s.apiKey)
  return useQuery({ queryKey: ['review', apiKey, 'outcomes', params],
    queryFn: () => request<Paged<MemoryFact>>(`/v1/review/outcomes?${qs(params)}`), enabled: !!apiKey, retry: false })
}
export function useQuestions(params: ReviewQuery) {
  const apiKey = useAuth(s => s.apiKey)
  return useQuery({ queryKey: ['review', apiKey, 'questions', params],
    queryFn: () => request<Paged<Question>>(`/v1/review/questions?${qs(params)}`), enabled: !!apiKey, retry: false })
}
export function useHealthQueue(params: ReviewQuery) {
  const apiKey = useAuth(s => s.apiKey)
  return useQuery({ queryKey: ['review', apiKey, 'health', params],
    queryFn: () => request<HealthQueue>(`/v1/review/health?${qs(params)}`), enabled: !!apiKey, retry: false })
}
export function useInvalidateReview() {
  const qc = useQueryClient()
  const apiKey = useAuth(s => s.apiKey)
  return () => qc.invalidateQueries({ queryKey: ['review', apiKey] })
}
interface Update {
  id: string
  status: string
  expected_version: string
  include_sensitive: boolean
  resolution_id?: string
  note?: string
}
export function useReviewUpdate(type: 'questions' | 'health') {
  const invalidate = useInvalidateReview()
  return useMutation({ mutationFn: ({ id, ...body }: Update) => request(`/v1/review/${type}/${encodeURIComponent(id)}`, {
    method: 'PATCH', body: JSON.stringify(body),
  }), onSuccess: invalidate, onError: invalidate })
}
