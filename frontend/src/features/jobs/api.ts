import { request } from '../../lib/api'

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'
export interface JobResult {
  ok: boolean
  session_id: string
  outcomes_status: string
  outcomes_error: string | null
  outcomes: number
  facts_added?: number
  episodes?: number
}
export interface DistillationJob {
  id: string
  session_id: string
  status: JobStatus
  reason: string
  attempt: number
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  result: JobResult | null
  deduplicated: boolean
  history: Array<{
    attempt: number
    status: JobStatus
    reason: string
    started_at: string | null
    finished_at: string | null
    result: JobResult | null
  }>
}
export interface JobsQuery {
  status?: JobStatus
  session_id?: string
  limit?: number
  offset?: number
}
export interface JobsResponse {
  jobs: DistillationJob[]
  counts: Record<JobStatus, number>
  page: { total: number; limit: number; offset: number; next_offset: number | null }
}
export const jobsApi = {
  list: (params: JobsQuery = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) query.set(key, String(value))
    })
    return request<JobsResponse>(`/v1/jobs?${query}`)
  },
  get: (id: string) => request<DistillationJob>(`/v1/jobs/${encodeURIComponent(id)}`),
  enqueue: (session_id: string) => request<DistillationJob>('/v1/jobs', {
    method: 'POST', body: JSON.stringify({ session_id, outcomes: true }),
  }),
  retry: (id: string) => request<DistillationJob>(`/v1/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
}
