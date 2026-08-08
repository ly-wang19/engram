/**
 * Type definitions mirroring the Engram HTTP API (engram/server/app.py).
 * These are the exact response shapes the server returns, so SDK calls are fully typed.
 */

export interface Health {
  ok: boolean
  ready: boolean
  service: string
  auth_mode: 'api_keys' | 'open' | 'disabled' | 'invalid'
  anonymous_allowed: boolean
  owner_control_configured: boolean
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
  known_at: number | null
  redacted_sensitive: boolean
  full_tokens?: number
  answer?: string
}

/** Direct factual answer with supporting facts (search). */
export interface SearchResult {
  answer: string
  facts: string[]
  as_of: number | null
  known_at: number | null
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

export interface ErasureReceipt {
  id: string
  scope: 'fact' | 'session'
  requested_id: string
  erased_at: number
  counts: {
    facts: number
    episodes: number
    working: number
    conflicts: number
  }
  digest: string
  verified: boolean
  storage_verified: boolean
  canonical_storage_verified: boolean
  live_index_verified: boolean
  storage_backend: string
  /** Always false: backups, snapshots, SSD remapping, and old vector fragments are outside this proof. */
  physical_media_erasure_guaranteed: false
}

export interface ErasureResult {
  ok: boolean
  confirmation_required?: boolean
  message?: string
  erasure?: ErasureReceipt
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
    erase_session: string
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

// --- personal-twin governance ---------------------------------------------

export type CapabilityPermission = 'observe' | 'draft' | 'execute'
export type TwinBoundaryEffect = 'deny' | 'require_confirmation'
export type TwinDecisionStatus = 'denied' | 'requires_confirmation' | 'allowed'

export interface TwinGoal {
  id: string
  title: string
  description: string
  status: string
  priority: number
  provenance: string[]
}

export interface TwinPrinciple {
  id: string
  name: string
  statement: string
  priority: number
  provenance: string[]
}

export interface TwinBoundary {
  id: string
  description: string
  effect: TwinBoundaryEffect
  capability: string
  scopes: string[]
  minimum_permission: CapabilityPermission
  model_visible: boolean
  provenance: string[]
}

export interface TwinContract {
  schema_version: 1
  version: number
  updated_at: number
  provenance: string[]
  confirm_high_risk_execution: boolean
  confirm_external_writes: boolean
  goals: TwinGoal[]
  principles: TwinPrinciple[]
  boundaries: TwinBoundary[]
}

/** Prompt-safe guidance. Enforcement scopes, effects, grants, and credential references are absent. */
export interface TwinModelContext {
  contract_version: number
  goals: Array<Pick<TwinGoal, 'title' | 'description' | 'status'>>
  principles: Array<Pick<TwinPrinciple, 'name' | 'statement'>>
  boundaries: Array<Pick<TwinBoundary, 'description'>>
}

export interface TwinContractResponse {
  ok: boolean
  contract: TwinContract
  model_context: TwinModelContext
}

/** Prompt-safe response available to the normal agent/app credential. */
export interface TwinContextResponse {
  ok: boolean
  contract_version: number
  model_context: TwinModelContext
}

export interface TwinContractHistoryResponse {
  ok: boolean
  contracts: TwinContract[]
  returned: number
}

/** Fields mirror the owner-control-plane API; each successful update creates a new contract version. */
export interface TwinContractPatch {
  goals?: Array<Partial<TwinGoal> & Pick<TwinGoal, 'title'>>
  principles?: Array<Partial<TwinPrinciple> & Pick<TwinPrinciple, 'statement'>>
  boundaries?: Array<Partial<TwinBoundary> & Pick<TwinBoundary, 'description'>>
  provenance?: string[]
  confirm_high_risk_execution?: boolean
  confirm_external_writes?: boolean
}

/** A provider lookup identifier only. Secret bytes must stay in the named keychain/vault. */
export interface CredentialRef {
  provider: string
  key: string
}

export interface CapabilityGrant {
  id: string
  capability: string
  permission: CapabilityPermission
  scopes: string[]
  /** Present only on the separately authenticated owner control plane. */
  credential_ref?: CredentialRef | null
  /** Prompt-safe indication returned to a normal agent instead of the lookup reference. */
  credential_configured?: boolean
  granted_at: number
  expires_at: number | null
  revoked_at: number | null
  provenance: string[]
}

export interface CapabilityRegistry {
  schema_version: 1
  grants: CapabilityGrant[]
}

export interface CapabilityRegistryResponse {
  ok: boolean
  registry: CapabilityRegistry
}

export interface CapabilityGrantInput {
  capability: string
  permission: CapabilityPermission
  scopes: string[]
  /** Lookup reference only; never pass a token, password, private key, or cookie. */
  credentialRef?: CredentialRef
  expiresAt?: number
  provenance?: string[]
}

export interface CapabilityGrantResponse {
  ok: boolean
  grant: CapabilityGrant
}

export interface TwinActionRequest {
  id: string
  capability: string
  permission: CapabilityPermission
  resource: string
  description: string
  high_risk: boolean
  external_write: boolean
  requested_at: number
}

export interface TwinActionDecision {
  id: string
  request_id: string
  status: TwinDecisionStatus
  reason: string
  grant_id: string | null
  policy_version: number
  decided_at: number
  valid_until: number | null
  confirmed_at: number | null
}

export interface TwinAuthorizationInput {
  capability: string
  permission: CapabilityPermission
  resource: string
  description?: string
  highRisk?: boolean
  externalWrite?: boolean
}

export interface TwinAuthorizationResult {
  ok: boolean
  request: TwinActionRequest
  decision: TwinActionDecision
  /** Always false: authorization evaluates policy and never executes the requested action. */
  executed: false
}

export interface TwinDecisionResult {
  ok: boolean
  message: string
  request: TwinActionRequest
  decision: TwinActionDecision
  executable: boolean
  executed: boolean
}

export interface TwinActionRecord {
  id: string
  request: TwinActionRequest
  decision: TwinActionDecision
  executed_at: number | null
  outcome: string
  provenance: string[]
}

export interface TwinActionRecordInput {
  decisionId: string
  outcome: string
  /** Set only by the trusted executor that can attest the action occurred. */
  executedAt?: number
  provenance?: string[]
}

export interface TwinActionRecordResult {
  ok: boolean
  message?: string
  action?: TwinActionRecord
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
  created_at: number
  expired_at: number | null
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
  /** epoch seconds for the transaction-time knowledge boundary */
  known_at?: number
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
  known_at: number | null
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
