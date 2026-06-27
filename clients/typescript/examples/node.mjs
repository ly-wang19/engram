// Run against a local Engram server:
//   pip install "engram-memory[serve]"
//   ENGRAM_OPEN=1 ENGRAM_EMBEDDER=hashing ENGRAM_LLM=deepseek uvicorn engram.server.app:app
//   node examples/node.mjs
//
// (After `npm run build`, import from 'engram-memory'. Here we import the built dist directly.)
import { EngramClient } from '../dist/index.js'

const engram = new EngramClient({
  baseUrl: process.env.ENGRAM_URL ?? 'http://localhost:8000',
  apiKey: process.env.ENGRAM_API_KEY ?? 'demo-user',
})

const sessionId = process.env.ENGRAM_SESSION_ID ?? 'sdk-demo:node'

const status = await engram.agentStatus({ sessionId })
console.log('--- agent memory status ---')
console.log({
  namespace: status.user,
  session: status.session,
  focus: status.focus,
  facts: status.counts.facts_live,
})

await engram.remember('I live in Shenzhen and my favorite language is Python.', { sessionId })
await engram.remember('Actually I just switched jobs — I now work at Moonshot AI.', { sessionId })

const { context } = await engram.recall('where do I work and live?', { sessionId })
console.log('--- recalled memory ---\n' + context)

const { answer } = await engram.search('What is my favorite programming language?')
console.log('\nsearch answer:', answer)

// OpenAI-compatible chat with automatic memory injection (requires ENGRAM_LLM on the server).
try {
  const completion = await engram.chat.completions.create({
    model: 'engram',
    messages: [{ role: 'user', content: 'Remind me where I work.' }],
    memory: { session_id: sessionId, scope: 'auto' },
  })
  console.log('\nchat answer:', completion.choices[0].message.content)
  console.log('memory used:', completion.engram)
} catch (err) {
  console.log('\n(chat skipped — set ENGRAM_LLM on the server to enable generation)')
}

await engram.closeSession(sessionId)
console.log('\nclosed memory session:', sessionId)

const report = await engram.sessionReport(sessionId)
console.log('\n--- session memory report ---')
console.log({
  episodes: report.episodes,
  factsAdded: report.facts_added,
  factsRedacted: report.facts_redacted,
  facts: report.facts.map((f) => f.text),
})

const sessions = await engram.sessions({ limit: 10, query: 'sdk-demo' })
console.log('\n--- cross-agent session index ---')
console.log(sessions.sessions.map((s) => ({
  id: s.id,
  episodes: s.episodes,
  factsAdded: s.facts_added,
  workingLive: s.working_live,
})))

const memoryPage = await engram.memories({
  factsLimit: 10,
  episodesLimit: 0,
  status: 'live',
})
console.log('\n--- user-owned memory page ---')
console.log({
  totalFacts: memoryPage.facts_page.total,
  nextFactOffset: memoryPage.next_offsets.facts,
  facts: memoryPage.facts.map((f) => ({
    id: f.id,
    text: f.display ?? f.text,
    sensitive: f.sensitive ?? false,
  })),
})

const portable = await engram.export()
console.log('\n--- share-safe export ---')
console.log({
  includeSensitive: portable.include_sensitive,
  redactedSensitive: portable.redacted_sensitive,
  facts: portable.facts.length,
  episodes: portable.episodes.length,
})
