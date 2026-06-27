# engram-memory (TypeScript/JavaScript SDK)

Official SDK for [Engram](https://github.com/your-org/engram) — the open-source long-term memory engine
for LLM agents. Zero runtime dependencies (uses the global `fetch`); works in Node 18+, browsers, Deno,
Bun, and edge runtimes.

```bash
npm install engram-memory
```

You need a running Engram server (the SDK talks to its HTTP API):

```bash
pip install "engram-memory[serve]"
export ENGRAM_OPEN=1                 # dev: Bearer text is the namespace; anonymous requires ENGRAM_ALLOW_ANONYMOUS=1
export ENGRAM_EMBEDDER=hashing       # zero-download default; use bge-small for better local embeddings
export ENGRAM_LLM=deepseek           # optional: enables /v1/chat/completions generation
uvicorn engram.server.app:app --port 8000
```

## Quickstart

```ts
import { EngramClient } from 'engram-memory'

const engram = new EngramClient({
  baseUrl: 'http://localhost:8000',
  apiKey: 'sk-alice-123', // one key == one isolated memory namespace
})

const sessionId = 'codex:super-memory:thread-123'

// content-free wiring check: namespace/session/focus/counts/next actions, no memory text
const status = await engram.agentStatus({ sessionId })
console.log(status.recommended_next_actions)

// write
await engram.remember('I live in Shenzhen and work at Tencent.', { sessionId })

// read — a small, dated, relevant slice to ground your own prompt
const { context, tokens_est } = await engram.recall('where do I live?', { sessionId })

// thread/task ended — finish consolidation, summarize, clear working memory
await engram.closeSession(sessionId)
const report = await engram.sessionReport(sessionId) // what this session saved; sensitive facts redacted
const sessions = await engram.sessions({ limit: 20, query: 'codex' }) // content-free cross-agent index

// or a single direct answer (abstains when not in memory)
const { answer } = await engram.search('Where do I work?')
```

## OpenAI-compatible chat (drop-in)

Engram exposes an OpenAI-compatible `/v1/chat/completions` that recalls + injects relevant memory before
the model answers, and remembers the turn afterward. Use the SDK's `chat.completions` surface…

```ts
const completion = await engram.chat.completions.create({
  model: 'engram',
  messages: [{ role: 'user', content: 'Remind me where I work.' }],
  memory: { session_id: 'codex:super-memory:thread-123', scope: 'auto' },
})
console.log(completion.choices[0].message.content)
console.log(completion.engram) // { recalled, memory_tokens_est, session_id, remembered }
```

…or keep your existing OpenAI SDK and just change the base URL:

```ts
import OpenAI from 'openai'
const openai = new OpenAI({ baseURL: 'http://localhost:8000/v1', apiKey: 'sk-alice-123' })
await openai.chat.completions.create({ model: 'engram', messages: [...] }) // now memory-augmented
```

Streaming works too:

```ts
const stream = await engram.chat.completions.create({
  model: 'engram',
  messages: [{ role: 'user', content: 'tell me about my projects' }],
  stream: true,
})
for await (const chunk of stream) process.stdout.write(chunk.choices[0]?.delta?.content ?? '')
```

Control the memory layer per request: `memory: { session_id: 'codex:repo:thread' }` to attach the turn to
an agent thread, `{ scope: 'long' }` / `{ scope: 'working' }` for write routing, `{ recall: false }`,
`{ remember: false }`, `{ as_of: 1700864000 }` for a point-in-time memory view, or
`{ redact_sensitive: true }` to omit sensitive facts from injected memory. Redacted contexts are
structured-facts-only: no profile, summaries, or raw chunks.

## Importing an existing history

```ts
import { readFileSync } from 'node:fs'

// ChatGPT export, OpenAI messages, JSONL, or a transcript — the server auto-detects the format.
await engram.import({ data: JSON.parse(readFileSync('conversations.json', 'utf8')), format: 'chatgpt' })
```

## User-owned export

```ts
// Safe by default: non-sensitive facts + graph; no profile, raw episodes, or summaries.
const portable = await engram.export()

// Full private export only after an explicit user action.
const fullPrivate = await engram.export({ includeSensitive: true })
```

## Memory management UI

```ts
// Content-free session index: which Codex / Claude / app sessions touched this namespace.
const sessions = await engram.sessions({ limit: 20, query: 'claude-code' })

// Share-safe/default view: paginate and search editable non-sensitive facts.
const page = await engram.memories({
  factsLimit: 50,
  factsOffset: 0,
  episodesLimit: 0,
  status: 'live',
  query: 'work',
})

// Owner-visible inspection view: include raw episodes, profile, and sensitive facts.
const fullPrivatePage = await engram.memories({
  factsLimit: 50,
  episodesLimit: 10,
  includeSensitive: true,
})
```

## API surface

| Method | HTTP | Returns |
| --- | --- | --- |
| `health()` | GET /health | `Health` readiness + safe deployment introspection |
| `remember(content, { sessionId?, scope? })` | POST /v1/remember | `RememberResult` |
| `recall(query, { nChunks?, sessionId?, asOf?, redactSensitive? })` | POST /v1/recall (lean) | `RecallResult` |
| `closeSession(sessionId, { summarize?, clearWorking? })` | POST /v1/sessions/close | `CloseSessionResult` |
| `sessions({ limit?, offset?, query? })` | GET /v1/sessions | content-free cross-agent/app session index |
| `sessionReport(sessionId, { includeSensitive? })` | GET /v1/sessions/report | `SessionReport` for what this session saved |
| `search(query, { asOf?, redactSensitive? })` | POST /v1/recall | `SearchResult` |
| `memories({ factsLimit?, factsOffset?, episodesLimit?, episodesOffset?, status?, query?, includeSensitive? })` | GET /v1/memories | paged `MemoryDump`; share-safe by default, full owner-visible view with `includeSensitive: true` |
| `agentStatus({ sessionId? })` | GET /v1/agent/status | `AgentStatus` content-free namespace/session/focus/counts/next actions |
| `stats()` | GET /v1/stats | `MemoryStats` content-free namespace observability, including consolidation backlog, hot/cold fact tiers, and page-in/out counts |
| `profile()` | GET /v1/profile | `ProfileResult` |
| `addFact({ subject?, predicate, object })` | POST /v1/facts | `{ ok, id, text }` |
| `updateFact(id, patch)` / `deleteFact(id)` | PATCH/DELETE /v1/facts/:id | — |
| `getFocus()` / `setFocus(f)` | GET/PUT /v1/focus | `Focus` |
| `getPolicy()` / `setPolicy(p)` | GET/PUT /v1/policy | `PolicyResponse` |
| `graph({ asOf?, includeSensitive? })` | GET /v1/graph | `GraphData`; share-safe by default, full owner-visible graph with `includeSensitive: true` |
| `import(params)` | POST /v1/import | `ImportResult` |
| `export({ includeSensitive? })` | GET /v1/export | share-safe structured JSON by default; full private JSON with `includeSensitive: true` |
| `forget({ confirm: true })` | POST /v1/forget | `{ ok, message }`; irreversible namespace wipe |
| `chat.completions.create(params)` | POST /v1/chat/completions | `ChatCompletion` / stream |

Errors throw `EngramError` with `.status` and a parsed `.detail`.

## Develop

```bash
npm install
npm run build       # dual ESM + CJS + .d.ts into dist/
npm test            # runtime smoke tests against the built dist/
npm run typecheck
```

## License

Engram is **dual-licensed**: open source under [GNU AGPL-3.0](../../LICENSE), or a separate **commercial
license** for proprietary/closed-source use. Commercial use that won't comply with the AGPL requires
authorization — see [`COMMERCIAL-LICENSE.md`](../../COMMERCIAL-LICENSE.md).
