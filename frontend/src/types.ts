// Types mirror the Engram memory server (engram/server/app.py). Keep in sync with the API.

export type FactStatus = 'live' | 'superseded'
export type FactSource = 'extracted' | 'user'

export interface MemoryFact {
  id: string
  text: string
  display: string // localized rendering (中文 for Chinese-recorded facts); falls back to text
  subject: string
  predicate: string
  object: string
  valid_at: string // human stamp from the server: "YYYY-MM-DD HH:MM:SS" (Beijing, UTC+8)
  invalid_at: string | null
  status: FactStatus
  source: FactSource
  supersedes: string | null
  category: string
  sensitive: boolean
  salience: number
  provenance: string[]
}

export interface Conflict {
  id: string
  older: string
  newer: string
  older_text: string
  newer_text: string
  reason: string
}

export interface WorkingItem {
  id: string
  content: string
  kind: string
  session_id: string
  created: string
  expires_at: string | null
}

export interface MemoryEpisode {
  id: string
  date: string
  session: string
  content: string
  summary: string
}

export interface MemoryCounts {
  episodes: number
  facts_live: number
  facts_superseded: number
  summaries: number
}

export interface MemoryStatsCounts {
  episodes: number
  episodes_consolidated: number
  episodes_pending: number
  episodes_ephemeral: number
  facts_hot: number
  facts_cold: number
  cold_pages_out: number
  cold_pages_in: number
  facts_live: number
  facts_superseded: number
  facts_sensitive: number
  working_live: number
  summaries: number
  entities: number
  relations: number
  graph_orphan_entities: number
  graph_stale_relations: number
  pending_conflicts: number
}

export interface AgentStatus {
  ok: boolean
  user: string
  session_id: string | null
  mode: 'content_free_agent_status'
  focus: Focus
  session: {
    id: string | null
    episodes: number
    episodes_pending: number
    working_live: number
  }
  counts: MemoryStatsCounts
  consolidation_backlog: boolean
  storage: string
  embedder: string
  llm_configured: boolean
  recommended_next_actions: string[]
  tools: {
    read_context: string
    write_memory: string
    close_session: string
    inspect_facts: string
    correct_fact: string
    delete_fact: string
    focus: string
  }
}

export interface SessionIndexItem {
  id: string
  episodes: number
  episodes_consolidated: number
  episodes_pending: number
  facts_added: number
  facts_sensitive: number
  working_live: number
  summaries: number
  first_event_at: number | null
  first_event_at_h: string | null
  last_event_at: number | null
  last_event_at_h: string | null
}

export interface SessionsIndex {
  ok: boolean
  user: string
  sessions: SessionIndexItem[]
  page: Page<SessionIndexItem>
  next_offset: number | null
}

export interface MemoryDump {
  user: string
  profile: string
  counts: MemoryCounts
  facts: MemoryFact[]
  episodes: MemoryEpisode[]
  facts_page?: Page<MemoryFact>
  episodes_page?: Page<MemoryEpisode>
  next_offsets?: {
    facts: number | null
    episodes: number | null
  }
}

export interface SessionReportFact {
  id: string
  text: string
  display: string
  valid_at: string
  invalid_at: string | null
  status: FactStatus
  source: FactSource
  category: string
  sensitive: boolean
  redacted: boolean
  provenance: string[]
  subject?: string
  predicate?: string
  object?: string
}

export interface SessionReport {
  ok: boolean
  user: string
  session_id: string
  include_sensitive: boolean
  episodes: number
  episodes_consolidated: number
  episodes_pending: number
  working_live: number
  facts_added: number
  facts_redacted: number
  facts: SessionReportFact[]
}

export interface Page<T> {
  items: T[]
  total: number
  offset: number
  limit: number | null
  has_more: boolean
  next_offset: number | null
}

export interface GraphNode {
  id: string
  name: string
  type: string
}

export interface GraphEdge {
  source: string
  target: string
  predicate: string
  live: boolean
  fact_id?: string
  fact_text?: string
  valid_at_h?: string
  invalid_at_h?: string | null
  provenance?: string[]
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface Focus {
  track: string[]
  mute: string[]
}

export type RememberScope = 'auto' | 'long' | 'working'

export interface RememberResult {
  ok: boolean
  extracted: number
  total_facts?: number
  degraded?: string
  stored_raw?: boolean
  scope?: RememberScope // 'working' = routed to ephemeral tier (not long-term)
  kind?: string
}

export interface CloseSessionResult {
  ok: boolean
  session_id: string
  episodes: number
  pending_consolidated: number
  facts_added: number
  duplicates: number
  invalidated: number
  summaries: number
  reflected: number
  working_cleared: number
}

export interface RecallResult {
  context: string
  tokens_est: number // lean retrieved context
  full_tokens?: number // the whole history (the full-context baseline)
  answer?: string // the answer an agent would give from this memory (LLM over the lean context)
}

export interface FactWrite {
  subject?: string
  predicate: string
  object: string
  sensitive?: boolean
  category?: string
}

export interface FactEdit {
  subject?: string
  predicate?: string
  object?: string
  sensitive?: boolean
  category?: string
}

export interface Health {
  ok: boolean
  service: string
  users_hot: number
  ready?: boolean
  auth_mode?: 'api_keys' | 'open' | 'disabled'
  anonymous_allowed?: boolean
  embedder?: string
  llm_configured?: boolean
  answerer_configured?: boolean
  storage?: string
  max_hot_users?: number
  max_hot_facts?: number
}

export interface Policy {
  extract_instruction: string
  extract_system: string
  summary_system: string
  persona_system: string
}

export interface PolicyResponse {
  policy: Policy // user overrides ("" = use default)
  defaults: Policy // built-in prompts
}

// --- L2 structured profile (feature ③) ---
export interface Evidence {
  kind: 'user' | 'mentions' | 'reinforced'
  count: number
}

export interface ProfileBasic {
  field: string
  label: string
  value: string
  evidence: Evidence
  source: FactSource
  fact_id: string
}

export interface ProfileItem {
  item: string
  polarity: 'like' | 'dislike'
  category: string
  evidence: Evidence
  source: FactSource
  fact_id: string
  subject: string
  predicate: string
  object: string
}

export interface ProfileHabit {
  text: string
  evidence: Evidence
  fact_id: string
}

export interface StructuredProfile {
  basic: ProfileBasic[]
  preferences: Record<string, ProfileItem[]>
  habits: ProfileHabit[]
  tentative: ProfileItem[]
  counts: { basic: number; preferences: number; tentative: number; habits: number }
}

export interface AuditFinding {
  kind: 'machine_token' | 'empty_value' | 'unreduced_claim' | 'orphan_entity'
  why: string
  action: string
  fact_id?: string
  text?: string
  subject?: string
  predicate?: string
  object?: string
  source?: FactSource
  valid_at_h?: string
  entity?: string
}

export interface AuditReport {
  user: string
  checked: { facts: number; entities: number }
  total_findings: number
  by_kind: Record<string, number>
  findings: AuditFinding[]
  truncated: boolean
}
