// Types mirror the Engram memory server (engram/server/app.py). Keep in sync with the API.

export type FactStatus = 'live' | 'superseded'
export type FactSource = 'extracted' | 'user'

export interface MemoryFact {
  id: string
  text: string
  subject: string
  predicate: string
  object: string
  valid_at: string // human date from the server
  invalid_at: string | null
  status: FactStatus
  source: FactSource
  supersedes: string | null
  salience: number
  provenance: string[]
}

export interface MemoryEpisode {
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

export interface MemoryDump {
  user: string
  profile: string
  counts: MemoryCounts
  facts: MemoryFact[]
  episodes: MemoryEpisode[]
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
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface Focus {
  track: string[]
  mute: string[]
}

export interface RememberResult {
  ok: boolean
  extracted: number
  total_facts?: number
  degraded?: string
  stored_raw?: boolean
}

export interface RecallResult {
  context: string
  tokens_est: number
}

export interface FactWrite {
  subject?: string
  predicate: string
  object: string
}

export interface FactEdit {
  subject?: string
  predicate?: string
  object?: string
}

export interface Health {
  ok: boolean
  service: string
  users_hot: number
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
