import { request } from '../../lib/api'
export interface ForgetScope { session_ids?: string[]; source?: string | null; time_from?: number | null; time_to?: number | null }
export interface PreviewItem { id: string; text: string; redacted: boolean; session_id?: string; event_time?: number; source?: string }
export interface PreviewPage { items: PreviewItem[]; total: number; offset: number; limit: number; next_offset: number | null }
export interface ForgetPreview { token: string; version: string; scope: ForgetScope; expires_at: number;
  counts: Record<string, number>; episodes: PreviewPage; facts: PreviewPage; include_sensitive: boolean; policy: string }
export const forgettingApi = {
  preview: (scope: ForgetScope, include_sensitive: boolean) => request<ForgetPreview>('/v1/forget/preview', {
    method: 'POST', body: JSON.stringify({ scope, include_sensitive }),
  }),
  page: (token: string, kind: string, sensitive: boolean, offset: number) => request<PreviewPage>(
    `/v1/forget/preview/${encodeURIComponent(token)}?kind=${kind}&include_sensitive=${sensitive}&offset=${offset}&limit=20`),
  confirm: (preview: ForgetPreview) => request<{ ok: boolean; status: string; counts: Record<string, number> }>('/v1/forget/confirm', {
    method: 'POST', body: JSON.stringify({ scope: preview.scope, token: preview.token, version: preview.version, confirm: true }),
  }),
}
