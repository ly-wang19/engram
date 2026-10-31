import { request } from '../../lib/api'

export interface EvidenceItem {
  id: string; kind: 'fact' | 'episode'; source_id: string; text: string; source_token: string
  truncated: boolean; event_time: number; session_id: string | null; source: string
}
export interface AskInput { query: string; n_chunks: number; as_of?: number; redact_sensitive: boolean }
export interface AskResult {
  context: string; context_evidence: EvidenceItem[]; citations: EvidenceItem[]
  claims: Array<{ text: string; evidence_ids: string[] }>; answer: string; tokens_est: number
  as_of: number | null; redacted_sensitive: boolean
  answer_status: 'no_evidence' | 'unavailable' | 'error' | 'invalid_citations' | 'stale_evidence' | 'completed' | 'abstained'
}
export interface SourceEpisode { id: string; session_id: string; content: string; event_time: number }
export interface EvidenceSource {
  kind: 'fact' | 'episode'; episode?: SourceEpisode
  fact?: { id: string; text: string; subject: string; predicate: string; object: string; source: string; sensitive: boolean }
  episodes?: SourceEpisode[]; raw_redacted?: boolean
  page?: { total: number; next_offset: number | null }; as_of: number | null
}
export const evidenceApi = {
  ask: (input: AskInput) => request<AskResult>('/v1/ask', { method: 'POST', body: JSON.stringify(input) }),
  source: (token: string, sensitive: boolean, offset: number) => request<EvidenceSource>(
    `/v1/evidence/${encodeURIComponent(token)}?include_sensitive=${sensitive}&offset=${offset}&limit=10`),
}
