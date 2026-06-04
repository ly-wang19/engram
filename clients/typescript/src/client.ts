/**
 * EngramClient — the official TypeScript/JavaScript SDK for the Engram memory server.
 *
 * Zero runtime dependencies: it uses the global `fetch` (Node 18+ or the browser). One Bearer key per
 * client maps to one isolated memory namespace on the server.
 *
 *   import { EngramClient } from 'engram-memory'
 *   const engram = new EngramClient({ baseUrl: 'http://localhost:8000', apiKey: 'sk-alice-123' })
 *   await engram.remember('I live in Shenzhen and work at Tencent.')
 *   const { context } = await engram.recall('where do I live?')
 */
import type {
  ChatCompletion,
  ChatCompletionChunk,
  ChatCompletionCreateParams,
  Fact,
  FactInput,
  FactPatch,
  Focus,
  GraphData,
  Health,
  ImportParams,
  ImportResult,
  MemoryDump,
  OkMessage,
  Policy,
  PolicyResponse,
  ProfileResult,
  RecallResult,
  RememberResult,
  SearchResult,
} from './types'

/** Thrown for any non-2xx response. `detail` carries the parsed error body when available. */
export class EngramError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'EngramError'
  }
}

export interface EngramClientOptions {
  /** Server base URL. Default 'http://localhost:8000'. A trailing '/v1' is allowed and normalized away. */
  baseUrl?: string
  /** Bearer API key — selects the memory namespace on the server. */
  apiKey?: string
  /** Custom fetch implementation (e.g. node-fetch on older runtimes). Defaults to global fetch. */
  fetch?: typeof fetch
  /** Extra headers sent on every request. */
  headers?: Record<string, string>
}

/** Overloaded create(): returns a stream when `stream: true`, else a single completion. */
export interface ChatCompletionsCreate {
  (params: ChatCompletionCreateParams & { stream: true }): Promise<AsyncIterable<ChatCompletionChunk>>
  (params: ChatCompletionCreateParams & { stream?: false }): Promise<ChatCompletion>
}

export class EngramClient {
  readonly baseUrl: string
  private readonly apiKey?: string
  private readonly _fetch: typeof fetch
  private readonly extraHeaders: Record<string, string>

  /** OpenAI-compatible surface: `client.chat.completions.create({ messages })`. */
  readonly chat: { completions: { create: ChatCompletionsCreate } }

  constructor(options: EngramClientOptions = {}) {
    const base = options.baseUrl ?? 'http://localhost:8000'
    // tolerate a base that already ends in /v1 (so both forms work)
    this.baseUrl = base.replace(/\/$/, '').replace(/\/v1$/, '')
    this.apiKey = options.apiKey
    const f = options.fetch ?? (globalThis.fetch ? globalThis.fetch.bind(globalThis) : undefined)
    if (!f) {
      throw new Error('No global fetch found. Use Node 18+, a browser, or pass options.fetch.')
    }
    this._fetch = f
    this.extraHeaders = options.headers ?? {}
    this.chat = {
      completions: { create: this.createChatCompletion.bind(this) as ChatCompletionsCreate },
    }
  }

  // --- low-level plumbing ---------------------------------------------------
  private buildHeaders(json: boolean): Record<string, string> {
    const h: Record<string, string> = { ...this.extraHeaders }
    if (json) h['Content-Type'] = 'application/json'
    if (this.apiKey) h['Authorization'] = `Bearer ${this.apiKey}`
    return h
  }

  private async assertOk(res: Response): Promise<void> {
    if (res.ok) return
    let detail: unknown
    let message = `Engram request failed (${res.status})`
    try {
      const body = (await res.json()) as { detail?: unknown }
      detail = body
      if (body?.detail) {
        message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      }
    } catch {
      /* non-JSON error body */
    }
    throw new EngramError(res.status, message, detail)
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const hasBody = init.body != null
    const res = await this._fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: { ...this.buildHeaders(hasBody), ...(init.headers as Record<string, string>) },
    })
    await this.assertOk(res)
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  private post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, { method: 'POST', body: body === undefined ? '{}' : JSON.stringify(body) })
  }

  // --- health ---------------------------------------------------------------
  health(): Promise<Health> {
    return this.request<Health>('/health')
  }

  // --- write / read memory --------------------------------------------------
  /** Store a message and consolidate it into bi-temporal facts. */
  remember(content: string, options: { sessionId?: string } = {}): Promise<RememberResult> {
    return this.post<RememberResult>('/v1/remember', {
      content,
      session_id: options.sessionId ?? 'default',
    })
  }

  /** Retrieve a small, relevant, dated context to answer from (the lean read path). */
  recall(query: string, options: { nChunks?: number } = {}): Promise<RecallResult> {
    return this.post<RecallResult>('/v1/recall', { query, lean: true, n_chunks: options.nChunks ?? 6 })
  }

  /** Answer a single factual question directly (abstains when not in memory). */
  search(query: string): Promise<SearchResult> {
    return this.post<SearchResult>('/v1/recall', { query, lean: false })
  }

  /** Everything stored for this namespace: profile, counts, facts, episodes. */
  memories(): Promise<MemoryDump> {
    return this.request<MemoryDump>('/v1/memories')
  }

  /** The synthesized user profile + key facts. */
  profile(): Promise<ProfileResult> {
    return this.request<ProfileResult>('/v1/profile')
  }

  // --- editable facts -------------------------------------------------------
  /** Assert an authoritative fact (user-sourced; never silently overwritten). */
  addFact(fact: FactInput): Promise<{ ok: boolean; id: string; text: string }> {
    return this.post('/v1/facts', { subject: 'user', ...fact })
  }

  updateFact(id: string, patch: FactPatch): Promise<{ ok: boolean; id: string; text: string }> {
    return this.request(`/v1/facts/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  }

  deleteFact(id: string): Promise<{ ok: boolean }> {
    return this.request(`/v1/facts/${encodeURIComponent(id)}`, { method: 'DELETE' })
  }

  // --- focus / policy / graph ----------------------------------------------
  getFocus(): Promise<Focus> {
    return this.request<Focus>('/v1/focus')
  }

  setFocus(focus: Partial<Focus>): Promise<{ ok: boolean; focus: Focus }> {
    return this.request('/v1/focus', { method: 'PUT', body: JSON.stringify(focus) })
  }

  getPolicy(): Promise<PolicyResponse> {
    return this.request<PolicyResponse>('/v1/policy')
  }

  setPolicy(patch: Partial<Policy>): Promise<{ ok: boolean } & PolicyResponse> {
    return this.request('/v1/policy', { method: 'PUT', body: JSON.stringify(patch) })
  }

  graph(): Promise<GraphData> {
    return this.request<GraphData>('/v1/graph')
  }

  // --- bulk import / export / forget ---------------------------------------
  /** Bulk-import a history (ChatGPT export, OpenAI messages, JSONL, transcript) in one batched pass. */
  import(params: ImportParams): Promise<ImportResult> {
    return this.post<ImportResult>('/v1/import', params)
  }

  /** Full-fidelity data export (every bi-temporal stamp + provenance, episodes, summaries, graph). */
  export<T = Record<string, unknown>>(): Promise<T> {
    return this.request<T>('/v1/export')
  }

  /** Erase ALL memory in this namespace (irreversible). */
  forget(): Promise<OkMessage> {
    return this.post<OkMessage>('/v1/forget', {})
  }

  // --- OpenAI-compatible chat ----------------------------------------------
  private async createChatCompletion(
    params: ChatCompletionCreateParams,
  ): Promise<ChatCompletion | AsyncIterable<ChatCompletionChunk>> {
    const res = await this._fetch(`${this.baseUrl}/v1/chat/completions`, {
      method: 'POST',
      headers: this.buildHeaders(true),
      body: JSON.stringify(params),
    })
    await this.assertOk(res)
    if (params.stream) return parseSSE(res)
    return (await res.json()) as ChatCompletion
  }
}

/** Parse an OpenAI-style SSE stream into ChatCompletionChunk values. */
async function* parseSSE(res: Response): AsyncGenerator<ChatCompletionChunk> {
  if (!res.body) throw new EngramError(500, 'streaming requested but the response has no body')
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const event = buffer.slice(0, idx).trim()
        buffer = buffer.slice(idx + 2)
        if (!event.startsWith('data:')) continue
        const data = event.slice(5).trim()
        if (data === '[DONE]') return
        try {
          yield JSON.parse(data) as ChatCompletionChunk
        } catch {
          /* skip malformed chunk */
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
