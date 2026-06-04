import { currentKey, useAuth } from '../store/auth'
import { getT } from '../i18n'
import type {
  Conflict,
  Focus,
  FactEdit,
  FactWrite,
  GraphData,
  Health,
  MemoryDump,
  Policy,
  PolicyResponse,
  RecallResult,
  RememberResult,
  StructuredProfile,
  WorkingItem,
} from '../types'

// Same-origin by default (FastAPI serves the SPA + API together). Override with
// VITE_API_BASE when the API lives elsewhere.
const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const key = currentKey()
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (key) headers.set('Authorization', `Bearer ${key}`)

  const res = await fetch(`${BASE}${path}`, { ...init, headers })

  if (res.status === 401) {
    // Stale / wrong key — drop it so the app returns to the login gate.
    useAuth.getState().logout()
    throw new ApiError(401, getT().errors.unauthorized)
  }
  if (!res.ok) {
    let detail = getT().errors.requestFailed(res.status)
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const json = (body: unknown) => JSON.stringify(body)

export const api = {
  health: () => request<Health>('/health'),

  memories: () => request<MemoryDump>('/v1/memories'),
  graph: () => request<GraphData>('/v1/graph'),
  focus: () => request<Focus>('/v1/focus'),

  remember: (content: string, session_id = 'default') =>
    request<RememberResult>('/v1/remember', { method: 'POST', body: json({ content, session_id }) }),

  recall: (query: string, n_chunks = 6) =>
    request<RecallResult>('/v1/recall', { method: 'POST', body: json({ query, lean: true, n_chunks }) }),

  addFact: (fact: FactWrite) =>
    request<{ ok: boolean; id: string; text: string }>('/v1/facts', {
      method: 'POST',
      body: json({ subject: 'user', ...fact }),
    }),

  editFact: (id: string, patch: FactEdit) =>
    request<{ ok: boolean; id: string; text: string }>(`/v1/facts/${id}`, {
      method: 'PATCH',
      body: json(patch),
    }),

  deleteFact: (id: string) =>
    request<{ ok: boolean }>(`/v1/facts/${id}`, { method: 'DELETE' }),

  setFocus: (focus: Partial<Focus>) =>
    request<{ ok: boolean; focus: Focus }>('/v1/focus', { method: 'PUT', body: json(focus) }),

  structuredProfile: () => request<StructuredProfile>('/v1/profile/structured'),

  policy: () => request<PolicyResponse>('/v1/policy'),

  setPolicy: (patch: Partial<Policy>) =>
    request<{ ok: boolean } & PolicyResponse>('/v1/policy', { method: 'PUT', body: json(patch) }),

  forget: () => request<{ ok: boolean; message: string }>('/v1/forget', { method: 'POST', body: json({}) }),

  working: (sessionId?: string) =>
    request<{ items: WorkingItem[] }>(`/v1/working${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`),

  addWorking: (content: string, kind = 'state', ttl_seconds?: number, session_id = 'default') =>
    request<{ ok: boolean; id: string }>('/v1/working', {
      method: 'POST',
      body: json({ content, kind, ttl_seconds, session_id }),
    }),

  clearWorking: (session_id = 'default') =>
    request<{ ok: boolean; cleared: number }>(`/v1/working?session_id=${encodeURIComponent(session_id)}`, {
      method: 'DELETE',
    }),

  conflicts: () => request<{ conflicts: Conflict[] }>('/v1/conflicts'),

  resolveConflict: (id: string, keep: 'newer' | 'older' | 'both') =>
    request<{ ok: boolean }>(`/v1/conflicts/${id}/resolve`, { method: 'POST', body: json({ keep }) }),

  /** Trigger a browser download of the full data export (keeps the Bearer header). */
  async exportDownload(filenameHint = 'me', includeSensitive = true): Promise<void> {
    const key = currentKey()
    const qs = includeSensitive ? '' : '?include_sensitive=false'
    const res = await fetch(`${BASE}/v1/export${qs}`, {
      headers: key ? { Authorization: `Bearer ${key}` } : undefined,
    })
    if (!res.ok) throw new ApiError(res.status, getT().errors.exportFailed(res.status))
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `engram_${filenameHint}_export.json`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },
}
