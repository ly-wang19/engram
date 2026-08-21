/**
 * Type definitions mirroring the Engram HTTP API (engram/server/app.py).
 * These are the exact response shapes the server returns, so SDK calls are fully typed.
 */

export interface Health {
  ok: boolean
  ready: boolean
  service: string
  version: string
  /** 'invalid' when the server's own auth/limit configuration failed to load — not a client error. */
  auth_mode: 'api_keys' | 'open' | 'disabled' | 'invalid'
  anonymous_allowed: boolean
  embedder: string
  llm_configured: boolean
  answerer_configured: boolean
  storage: string
  users_hot: number
  max_hot_users: number
  max_hot_facts: number
}

/** Latency percentiles for one instrumented service operation (remember, recall, ...). */
export interface MetricOp {
  n: number
  p50_ms: number
  p95_ms: number
  avg_ms: number
  max_ms: number
  /** Size of the rolling sample the percentiles were computed over. */
  window: number
}

/** GET /metrics — aggregate-only by construction: no namespaces, queries or content. */
export interface Metrics {
  uptime_s: number
  /** Timed operations, keyed by operation name. */
  ops: Record<string, MetricOp>
  /** Plain counters (auth_rejected, rate_limited, idempotent_replays, ...). */
  counts: Record<string, number>
  tokens: {
    context_total: number
    baseline_context_total: number
    baseline_full_total: number
    calls_with_baseline: number
    /** full-history tokens / served-context tokens. null until one call measured both. */
    savings_ratio: number | null
  }
}

export interface RememberResult {
  ok: boolean
  scope?: string
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

export interface SessionIndex {
  ok: boolean
  user: string
  sessions: SessionIndexItem[]
  page: MemoryPage<SessionIndexItem>
  next_offset: number | null
}

export interface SessionListOptions {
  limit?: number
  offset?: number
  /** Text search over session ids. */
  query?: string
  /** Alias for query, matching the HTTP parameter. */
  q?: string
}

export type FactStatus = 'live' | 'superseded'
export type FactSource = 'extracted' | 'user'

export interface Fact {
  id: string
  text: string
  display?: string
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
  category?: string
  sensitive?: boolean
  redacted?: boolean
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

export type MemoryStatusFilter = 'live' | 'current' | 'superseded' | 'old' | 'history'

export interface MemoryPage<T> {
  offset: number
  limit: number | null
  total: number
  next_offset: number | null
  has_more: boolean
  items: T[]
}

export interface MemoryListOptions {
  factsLimit?: number
  factsOffset?: number
  episodesLimit?: number
  episodesOffset?: number
  status?: MemoryStatusFilter
  /** Text search over facts; raw episodes are searched only when includeSensitive=true. */
  query?: string
  /** Alias for query, matching the HTTP parameter. */
  q?: string
  /** Defaults to a share-safe list; pass true only for explicit owner-visible raw episodes/profile. */
  includeSensitive?: boolean
}

/** Paged memory view (GET /v1/memories); share-safe by default. */
export interface MemoryDump {
  user: string
  profile: string
  counts: MemoryCounts
  facts: Fact[]
  episodes: EpisodeView[]
  facts_page: MemoryPage<Fact>
  episodes_page: MemoryPage<EpisodeView>
  next_offsets: {
    facts: number | null
    episodes: number | null
  }
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
  counts: MemoryStats['counts']
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

export interface ProfileResult {
  profile: string
  facts: string[]
}

/**
 * How well-evidenced a structured-profile entry is. Deliberately a kind + a count rather than a
 * fabricated 0..1 score, so a UI can say *why* it believes something.
 */
export interface ProfileEvidence {
  kind: 'user' | 'reinforced' | 'mentions'
  count: number
}

export interface StructuredProfileBasic {
  field: string
  label: string
  value: string
  evidence: ProfileEvidence
  source: FactSource
  /** '' when the value came from identity resolution rather than a stored fact. */
  fact_id: string
}

export interface StructuredProfilePreference {
  item: string
  polarity: 'like' | 'dislike' | 'diet'
  category: string
  evidence: ProfileEvidence
  source: FactSource
  fact_id: string
  subject: string
  predicate: string
  object: string
}

export interface StructuredProfileHabit {
  text: string
  evidence: ProfileEvidence
  fact_id: string
}

/**
 * GET /v1/profile/structured — live facts grouped for display (basic info / preferences / habits),
 * split into confirmed vs `tentative`. A derived read-only view: it never filters retrieval.
 */
export interface StructuredProfile {
  basic: StructuredProfileBasic[]
  /** Confirmed preferences, keyed by coarse category. */
  preferences: Record<string, StructuredProfilePreference[]>
  habits: StructuredProfileHabit[]
  /** Preferences shown as 待确认 — weak signals, displayed apart from the confirmed set. */
  tentative: StructuredProfilePreference[]
  counts: {
    basic: number
    preferences: number
    tentative: number
    habits: number
  }
}

// --- working memory (ephemeral, session/TTL-scoped) -------------------------

export interface WorkingItem {
  id: string
  content: string
  kind: string
  session_id: string
  /** YYYY-MM-DD */
  created: string
  /** YYYY-MM-DD, or null when the item lives until the session is cleared. */
  expires_at: string | null
}

/** GET /v1/working — live items only; expired and consumed ones are already excluded. */
export interface WorkingMemory {
  items: WorkingItem[]
}

export interface AddWorkingResult {
  ok: boolean
  id: string
  kind: string
  /** Epoch seconds, or null when there is no hard expiry. */
  expires_at: number | null
}

export interface ClearWorkingResult {
  ok: boolean
  cleared: number
}

// --- suspected conflicts (detected in System-2, resolved by the user) -------

export interface Conflict {
  id: string
  /** Fact id of the older claim. */
  older: string
  /** Fact id of the newer claim. */
  newer: string
  older_text: string
  newer_text: string
  reason: string
}

export interface ConflictList {
  conflicts: Conflict[]
}

/** 'both' dismisses the conflict — the two claims are allowed to coexist. */
export type ConflictResolution = 'newer' | 'older' | 'both'

// --- admin: tenant API keys (ENGRAM_ADMIN_TOKEN, not a tenant key) ----------

/** A key record as the server publishes it — never the secret, never its digest. */
export interface ApiKeyRecord {
  id: string
  user: string
  label: string
  /** Epoch seconds. */
  created_at: number
  revoked: boolean
  last_used_at: number | null
}

/** POST /v1/admin/keys — `key` is the plaintext, returned once here and recoverable nowhere else. */
export interface IssuedApiKey extends ApiKeyRecord {
  key: string
}

export interface ApiKeyList {
  keys: ApiKeyRecord[]
}

export interface RevokeKeyResult {
  ok: boolean
  id: string
  revoked: boolean
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
  sensitive?: boolean
  category?: string
}

export interface FactPatch {
  subject?: string
  predicate?: string
  object?: string
  sensitive?: boolean
  category?: string
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

/** Shared by the mutating calls the server can replay instead of re-running (remember, import). */
export interface IdempotentOptions {
  /**
   * Sent as the `Idempotency-Key` header. The server replays the first successful response for a
   * repeated (tenant, key) pair, so retrying after a timeout does not store the same history twice.
   */
  idempotencyKey?: string
}

export interface MemoryExportFact {
  id: string
  subject: string
  predicate: string
  object: string
  text: string
  source: FactSource
  status: FactStatus
  category: string
  sensitive: boolean
  salience: number
  confidence: number
  valid_at: number
  valid_at_h: string
  invalid_at: number | null
  invalid_at_h: string | null
  created_at: number
  expired_at: number | null
  supersedes: string | null
  provenance: string[]
}

export interface MemoryExportEpisode {
  id: string
  session_id: string
  speaker: string
  event_time: number
  date: string
  content: string
  summary: string
}

export interface MemoryExport {
  engram_export_version: 1
  user: string
  include_sensitive: boolean
  redacted_sensitive: boolean
  profile: string
  focus: Focus
  facts: MemoryExportFact[]
  episodes: MemoryExportEpisode[]
  graph: GraphData
  redaction_note?: string
}

export interface ForgetOptions {
  /** Must be true; wiping a namespace is irreversible. */
  confirm: true
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
  /** conversation/thread id for recall and the background write */
  session_id?: string
  /** auto | long | working; controls how the background write is routed */
  scope?: string
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
  session_id?: string | null
  as_of: number | null
  redacted_sensitive: boolean
  remembered: boolean
  remember_scope?: string
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
