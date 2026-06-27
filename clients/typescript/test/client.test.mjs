import assert from 'node:assert/strict'
import test from 'node:test'

import { EngramClient } from '../dist/index.js'

const counts = {
  episodes: 0,
  episodes_consolidated: 0,
  episodes_pending: 0,
  episodes_ephemeral: 0,
  facts_hot: 0,
  facts_cold: 0,
  cold_pages_out: 0,
  cold_pages_in: 0,
  facts_live: 0,
  facts_superseded: 0,
  facts_sensitive: 0,
  working_live: 0,
  summaries: 0,
  entities: 0,
  relations: 0,
  graph_orphan_entities: 0,
  graph_stale_relations: 0,
  pending_conflicts: 0,
}

test('agentStatus calls the content-free agent status endpoint', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({
      ok: true,
      user: 'sdk-user',
      session_id: 'codex:repo/thread 1',
      mode: 'content_free_agent_status',
      focus: { track: [], mute: [] },
      session: {
        id: 'codex:repo/thread 1',
        episodes: 0,
        episodes_pending: 0,
        working_live: 0,
      },
      counts,
      consolidation_backlog: false,
      storage: 'memory',
      embedder: 'HashingEmbedder',
      llm_configured: false,
      recommended_next_actions: [
        'Call engram_recall before answering tasks that depend on prior user/project context.',
      ],
      tools: {
        read_context: 'engram_recall',
        write_memory: 'engram_remember',
        close_session: 'engram_close_session',
        inspect_facts: 'engram_list_facts',
        correct_fact: 'engram_update_fact',
        delete_fact: 'engram_delete_fact',
        focus: 'engram_get_focus / engram_set_focus',
      },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const client = new EngramClient({
    baseUrl: 'http://engram.test/v1',
    apiKey: 'sk-test',
    fetch,
  })

  const status = await client.agentStatus({ sessionId: 'codex:repo/thread 1' })

  assert.equal(calls.length, 1)
  assert.equal(
    calls[0].url,
    'http://engram.test/v1/agent/status?session_id=codex%3Arepo%2Fthread%201',
  )
  assert.equal(calls[0].init.method, undefined)
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-test')
  assert.equal(calls[0].init.headers['Content-Type'], undefined)
  assert.equal(status.mode, 'content_free_agent_status')
  assert.equal(status.tools.read_context, 'engram_recall')
})

test('sessionReport calls the session audit endpoint with redaction default', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({
      ok: true,
      user: 'sdk-user',
      session_id: 'codex:repo/thread 1',
      include_sensitive: false,
      episodes: 1,
      episodes_consolidated: 1,
      episodes_pending: 0,
      working_live: 0,
      facts_added: 1,
      facts_redacted: 0,
      facts: [],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const client = new EngramClient({
    baseUrl: 'http://engram.test',
    apiKey: 'sk-test',
    fetch,
  })

  const report = await client.sessionReport('codex:repo/thread 1')

  assert.equal(calls.length, 1)
  assert.equal(
    calls[0].url,
    'http://engram.test/v1/sessions/report?session_id=codex%3Arepo%2Fthread+1&include_sensitive=false',
  )
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-test')
  assert.equal(report.session_id, 'codex:repo/thread 1')
  assert.equal(report.include_sensitive, false)
})

test('sessions calls the content-free cross-agent session index endpoint', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({
      ok: true,
      user: 'sdk-user',
      sessions: [
        {
          id: 'claude-code:repo/thread 2',
          episodes: 2,
          episodes_consolidated: 2,
          episodes_pending: 0,
          facts_added: 3,
          facts_sensitive: 1,
          working_live: 0,
          summaries: 1,
          first_event_at: 1782500000,
          first_event_at_h: '2026-06-27 10:00:00',
          last_event_at: 1782503600,
          last_event_at_h: '2026-06-27 11:00:00',
        },
      ],
      page: {
        offset: 4,
        limit: 2,
        total: 7,
        next_offset: 6,
        has_more: true,
        items: [],
      },
      next_offset: 6,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const client = new EngramClient({
    baseUrl: 'http://engram.test/v1',
    apiKey: 'sk-test',
    fetch,
  })

  const index = await client.sessions({ limit: 2, offset: 4, query: 'claude-code' })

  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'http://engram.test/v1/sessions?limit=2&offset=4&q=claude-code')
  assert.equal(calls[0].init.method, undefined)
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-test')
  assert.equal(calls[0].init.headers['Content-Type'], undefined)
  assert.equal(index.sessions[0].id, 'claude-code:repo/thread 2')
  assert.equal(index.page.next_offset, 6)
})

test('memories supports paging search and share-safe filtering for owner UIs', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({
      user: 'sdk-user',
      profile: '',
      counts: {
        episodes: 2,
        facts_live: 3,
        facts_superseded: 1,
        summaries: 1,
      },
      facts: [
        {
          id: 'fact-1',
          text: 'user works at Moonshot AI',
          display: 'user works at Moonshot AI',
          subject: 'user',
          predicate: 'employer',
          object: 'Moonshot AI',
          valid_at: '2026-06-27',
          invalid_at: null,
          status: 'live',
          source: 'user',
          supersedes: null,
          category: 'work',
          sensitive: false,
          salience: 1,
          provenance: [],
        },
      ],
      episodes: [],
      facts_page: {
        offset: 4,
        limit: 2,
        total: 7,
        next_offset: 6,
        has_more: true,
        items: [],
      },
      episodes_page: {
        offset: 0,
        limit: 0,
        total: 0,
        next_offset: null,
        has_more: false,
        items: [],
      },
      next_offsets: { facts: 6, episodes: null },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const client = new EngramClient({
    baseUrl: 'http://engram.test',
    apiKey: 'sk-test',
    fetch,
  })

  const dump = await client.memories({
    factsLimit: 2,
    factsOffset: 4,
    episodesLimit: 0,
    status: 'live',
    query: 'work',
    includeSensitive: false,
  })

  assert.equal(calls.length, 1)
  assert.equal(
    calls[0].url,
    'http://engram.test/v1/memories?facts_limit=2&facts_offset=4&episodes_limit=0&status=live&q=work&include_sensitive=false',
  )
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-test')
  assert.equal(dump.facts_page.next_offset, 6)
  assert.equal(dump.episodes.length, 0)
  assert.equal(dump.facts[0].sensitive, false)
})

test('export defaults to share-safe payload and can request full private export', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    const includeSensitive = url.endsWith('include_sensitive=true')
    return new Response(JSON.stringify({
      engram_export_version: 1,
      include_sensitive: includeSensitive,
      redacted_sensitive: !includeSensitive,
      facts: includeSensitive
        ? [{ object: 'Moonshot AI' }, { object: 'diabetes', sensitive: true }]
        : [{ object: 'Moonshot AI' }],
      graph: { nodes: [], edges: [] },
      episodes: includeSensitive ? [{ content: 'private raw text' }] : [],
      profile: includeSensitive ? 'private profile' : '',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const client = new EngramClient({
    baseUrl: 'http://engram.test/v1',
    apiKey: 'sk-test',
    fetch,
  })

  const safeExport = await client.export()
  const fullExport = await client.export({ includeSensitive: true })

  assert.equal(calls[0].url, 'http://engram.test/v1/export?include_sensitive=false')
  assert.equal(calls[1].url, 'http://engram.test/v1/export?include_sensitive=true')
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-test')
  assert.equal(safeExport.include_sensitive, false)
  assert.equal(safeExport.redacted_sensitive, true)
  assert.deepEqual(safeExport.facts.map((fact) => fact.object), ['Moonshot AI'])
  assert.equal(fullExport.include_sensitive, true)
  assert.deepEqual(fullExport.facts.map((fact) => fact.object), ['Moonshot AI', 'diabetes'])
})

test('forget requires explicit confirmation before sending destructive request', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({
      ok: true,
      message: 'All memory erased.',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const client = new EngramClient({
    baseUrl: 'http://engram.test/v1',
    apiKey: 'sk-test',
    fetch,
  })

  assert.throws(
    () => client.forget(),
    (err) => err.name === 'EngramError' && err.status === 400 && /confirm/.test(err.message),
  )
  assert.equal(calls.length, 0)

  const done = await client.forget({ confirm: true })

  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'http://engram.test/v1/forget')
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-test')
  assert.deepEqual(JSON.parse(calls[0].init.body), { confirm: true })
  assert.equal(done.ok, true)
})
