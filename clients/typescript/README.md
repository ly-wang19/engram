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
export ENGRAM_OPEN=1                 # dev: bearer text is the namespace (use ENGRAM_API_KEYS in prod)
export ENGRAM_EMBEDDER=bge-small     # or `hashing` for an instant, no-download dev server
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

// write
await engram.remember('I live in Shenzhen and work at Tencent.')

// read — a small, dated, relevant slice to ground your own prompt
const { context, tokens_est } = await engram.recall('where do I live?')

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
})
console.log(completion.choices[0].message.content)
console.log(completion.engram) // { recalled, memory_tokens_est, remembered }
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

Control the memory layer per request: `memory: { recall: false }` or `{ remember: false }`.

## Importing an existing history

```ts
import { readFileSync } from 'node:fs'

// ChatGPT export, OpenAI messages, JSONL, or a transcript — the server auto-detects the format.
await engram.import({ data: JSON.parse(readFileSync('conversations.json', 'utf8')), format: 'chatgpt' })
```

## API surface

| Method | HTTP | Returns |
| --- | --- | --- |
| `remember(content, { sessionId? })` | POST /v1/remember | `RememberResult` |
| `recall(query, { nChunks? })` | POST /v1/recall (lean) | `RecallResult` |
| `search(query)` | POST /v1/recall | `SearchResult` |
| `memories()` | GET /v1/memories | `MemoryDump` |
| `profile()` | GET /v1/profile | `ProfileResult` |
| `addFact({ subject?, predicate, object })` | POST /v1/facts | `{ ok, id, text }` |
| `updateFact(id, patch)` / `deleteFact(id)` | PATCH/DELETE /v1/facts/:id | — |
| `getFocus()` / `setFocus(f)` | GET/PUT /v1/focus | `Focus` |
| `getPolicy()` / `setPolicy(p)` | GET/PUT /v1/policy | `PolicyResponse` |
| `graph()` | GET /v1/graph | `GraphData` |
| `import(params)` | POST /v1/import | `ImportResult` |
| `export()` | GET /v1/export | full JSON |
| `forget()` | POST /v1/forget | `{ ok, message }` |
| `chat.completions.create(params)` | POST /v1/chat/completions | `ChatCompletion` / stream |

Errors throw `EngramError` with `.status` and a parsed `.detail`.

## Develop

```bash
npm install
npm run build       # dual ESM + CJS + .d.ts into dist/
npm run typecheck
```

## License

Engram is **dual-licensed**: open source under [GNU AGPL-3.0](../../LICENSE), or a separate **commercial
license** for proprietary/closed-source use. Commercial use that won't comply with the AGPL requires
authorization — see [`COMMERCIAL-LICENSE.md`](../../COMMERCIAL-LICENSE.md).
