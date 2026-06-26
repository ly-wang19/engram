/**
 * Type definitions mirroring the Engram HTTP API (engram/server/app.py).
 * These are the exact response shapes the server returns, so SDK calls are fully typed.
 */

export interface Health {
  ok: boolean
  ready: boolean
  service: string
  auth_mode: 'api_keys' | 'open' | 'disabled'
  anonymous_allowed: boolean
  embedder: string
  llm_configured: boolean
  answerer_configured: boolean
  storage: string
  users_hot: number
  max_hot_users: number
  max_hot_facts: number
}

export interface RememberResult {
  ok: boolean
  /** number of new facts extracted by consolidation */
  extracted?: number
  /** total live facts after this write */
  total_facts?: number
  /** present if consolidation degraded (raw memory still stored) */
  degraded?: string
  stored_raw?: boolean
}

/** Lean retrieved context — a small, dated slice to answer from (recall). */
export interface RecallResult {
  context: string
  tokens_est: number
  as_of: number | null
  redacted_sensitive: boolean
  full_tokens?: number
  answer?: string
}

/** Direct factual answer with supporting facts (search). */
export interface SearchResult {
  answer: string
  facts: string[]
  as_of: number | null
  redacted_sensitive: boolean
}

export type FactStatus = 'live' | 'superseded'
export type FactSource = 'extracted' | 'user'

export interface Fact {
  id: string
  text: string
  subject: string
  predicate: string
  object: string
  /** YYYY-MM-DD (valid-from) */
  valid_at: string
  /** YYYY-MM-DD or null if still valid */
  invalid_at: string | null
  status: FactStatus
  source: FactSource
  supersedes: string | null
  salience: number
  provenance: string[]
}

export interface EpisodeView {
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

/** Everything stored for a user (GET /v1/memories). */
export interface MemoryDump {
  user: string
  profile: string
  counts: MemoryCounts
  facts: Fact[]
  episodes: EpisodeView[]
}

export interface MemoryStats {
  user: string
  counts: {
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
  time_range: {
    first_event_at: number | null
    first_event_at_h: string | null
    last_event_at: number | null
    last_event_at_h: string | null
    oldest_fact_valid_at: number | null
    oldest_fact_valid_at_h: string | null
    newest_fact_valid_at: number | null
    newest_fact_valid_at_h: string | null
  }
  storage: string
  max_hot_facts: number
  embedder: string
  llm_configured: boolean
  answerer_configured: boolean
  consolidation_backlog: boolean
}

export interface ProfileResult {
  profile: string
  facts: string[]
}

export interface Focus {
  track: string[]
  mute: string[]
}

export interface Policy {
  extract_instruction: string
  extract_system: string
  summary_system: string
  persona_system: string
}

export interface PolicyResponse {
  policy: Policy
  defaults: Policy
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
  fact_id: string
  fact_text: string
  valid_at: number
  valid_at_h: string
  invalid_at: number | null
  invalid_at_h: string | null
  provenance: string[]
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface FactInput {
  subject?: string
  predicate: string
  object: string
}

export interface FactPatch {
  subject?: string
  predicate?: string
  object?: string
}

export interface ImportMessageInput {
  role?: string
  speaker?: string
  content: string
  event_time?: number
}

export interface ImportSessionInput {
  session_id: string
  messages: ImportMessageInput[]
  title?: string
  event_time?: number
}

/** Bulk import: pass pre-parsed `sessions`, OR raw `data` + a `format` to parse server-side. */
export interface ImportParams {
  sessions?: ImportSessionInput[]
  data?: unknown
  /** chatgpt | messages | records | jsonl | transcript | auto (default) */
  format?: string
  consolidate?: boolean
  summarize?: boolean
}

export interface ImportResult {
  ok: boolean
  sessions: number
  episodes: number
  facts_added: number
  summaries: number
}

// --- OpenAI-compatible chat (POST /v1/chat/completions) ---------------------

export type ChatRole = 'system' | 'user' | 'assistant' | 'tool'

export interface ChatMessage {
  role: ChatRole
  /** string, or OpenAI multimodal content parts */
  content: string | Array<Record<string, unknown>>
}

/** Engram extension to control the memory layer per request. */
export interface MemoryControls {
  /** recall + inject relevant memory before answering (default true) */
  recall?: boolean
  /** remember the user turn after answering (default true) */
  remember?: boolean
  /** number of full past conversations to include for detail (default 6) */
  n_chunks?: number
  /** epoch seconds for a point-in-time memory view */
  as_of?: number
  /** omit facts tagged sensitive from injected memory */
  redact_sensitive?: boolean
}

export interface ChatCompletionCreateParams {
  messages: ChatMessage[]
  model?: string
  stream?: boolean
  memory?: MemoryControls
  temperature?: number
  max_tokens?: number
  [key: string]: unknown
}

export interface ChatChoice {
  index: number
  message: { role: 'assistant'; content: string }
  finish_reason: string
}

export interface ChatUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface EngramChatMeta {
  recalled: boolean
  memory_tokens_est: number
  as_of: number | null
  redacted_sensitive: boolean
  remembered: boolean
}

export interface ChatCompletion {
  id: string
  object: 'chat.completion'
  created: number
  model: string
  choices: ChatChoice[]
  usage: ChatUsage
  /** Engram extension: what the memory layer did for this request. */
  engram: EngramChatMeta
}

export interface ChatCompletionChunk {
  id: string
  object: 'chat.completion.chunk'
  created: number
  model: string
  choices: Array<{
    index: number
    delta: { role?: string; content?: string }
    finish_reason: string | null
  }>
}

export interface OkMessage {
  ok: boolean
  message?: string
}
