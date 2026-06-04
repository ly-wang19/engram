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

await engram.remember('I live in Shenzhen and my favorite language is Python.')
await engram.remember('Actually I just switched jobs — I now work at Moonshot AI.')

const { context } = await engram.recall('where do I work and live?')
console.log('--- recalled memory ---\n' + context)

const { answer } = await engram.search('What is my favorite programming language?')
console.log('\nsearch answer:', answer)

// OpenAI-compatible chat with automatic memory injection (requires ENGRAM_LLM on the server).
try {
  const completion = await engram.chat.completions.create({
    model: 'engram',
    messages: [{ role: 'user', content: 'Remind me where I work.' }],
  })
  console.log('\nchat answer:', completion.choices[0].message.content)
  console.log('memory used:', completion.engram)
} catch (err) {
  console.log('\n(chat skipped — set ENGRAM_LLM on the server to enable generation)')
}
