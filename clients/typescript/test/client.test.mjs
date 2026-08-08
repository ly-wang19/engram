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
        erase_session: 'engram_erase_session',
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

test('eraseSession sends explicit confirmation to the source-erasure endpoint', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({
      ok: true,
      erasure: {
        id: 'erase_1',
        scope: 'session',
        requested_id: 'private/session',
        erased_at: 1,
        counts: { facts: 1, episodes: 1, working: 0, conflicts: 0 },
        digest: 'abc',
        verified: true,
        storage_verified: true,
      },
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

  const result = await client.eraseSession('private/session', { confirm: true })

  assert.equal(calls[0].url, 'http://engram.test/v1/sessions/erase')
  assert.equal(calls[0].init.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    session_id: 'private/session',
    confirm: true,
  })
  assert.equal(result.erasure.storage_verified, true)
})

test('deleteFact requires an explicit confirmation query for provenance erasure', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({ ok: true, erasure: { storage_verified: true } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const client = new EngramClient({ baseUrl: 'http://engram.test', fetch })

  await client.deleteFact('fact/a b')
  await client.deleteFact('fact/a b', { confirm: true })

  assert.equal(calls[0].url, 'http://engram.test/v1/facts/fact%2Fa%20b?confirm=false')
  assert.equal(calls[1].url, 'http://engram.test/v1/facts/fact%2Fa%20b?confirm=true')
  assert.equal(calls[0].init.method, 'DELETE')
})

test('personal-twin SDK maps the trusted control plane and never executes during authorization', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    let payload
    if (url.includes('/v1/twin/control/contract/history')) {
      payload = { ok: true, contracts: [{ version: 2 }, { version: 1 }], returned: 2 }
    } else if (url.endsWith('/v1/twin/control/contract')) {
      payload = {
        ok: true,
        contract: { version: 2 },
        model_context: { contract_version: 2, goals: [], principles: [], boundaries: [] },
      }
    } else if (url.endsWith('/v1/twin/contract') && init.method === 'PUT') {
      payload = {
        ok: true,
        contract: { version: 2 },
        model_context: { contract_version: 2, goals: [], principles: [], boundaries: [] },
      }
    } else if (url.endsWith('/v1/twin/contract')) {
      payload = {
        ok: true,
        contract_version: 1,
        model_context: { contract_version: 1, goals: [], principles: [], boundaries: [] },
      }
    } else if (url.endsWith('/v1/twin/control/capabilities')) {
      payload = { ok: true, registry: { schema_version: 1, grants: [] } }
    } else if (url.endsWith('/v1/twin/capabilities') && init.method === undefined) {
      payload = { ok: true, registry: { schema_version: 1, grants: [] } }
    } else if (url.endsWith('/v1/twin/capabilities')) {
      payload = {
        ok: true,
        grant: {
          id: 'grant/a b',
          capability: 'calendar',
          permission: 'execute',
          scopes: ['calendars/personal/**'],
          credential_ref: { provider: 'keychain', key: 'engram/calendar' },
        },
      }
    } else if (url.endsWith('/revoke')) {
      payload = { ok: true, grant: { id: 'grant/a b', revoked_at: 50 } }
    } else if (url.endsWith('/v1/twin/authorize')) {
      payload = {
        ok: true,
        request: { id: 'action_1' },
        decision: { id: 'decision_1', status: 'requires_confirmation' },
        executed: false,
      }
    } else if (url.endsWith('/confirm')) {
      payload = {
        ok: true,
        request: { id: 'action_1' },
        decision: { id: 'decision_1', status: 'allowed' },
        executable: true,
        executed: false,
        message: 'authorization is current',
      }
    } else if (url.includes('/v1/twin/decisions/')) {
      payload = {
        ok: true,
        request: { id: 'action_1' },
        decision: { id: 'decision_1', status: 'allowed' },
        executable: true,
        executed: false,
        message: 'authorization is current',
      }
    } else {
      payload = {
        ok: true,
        action: { id: 'record_1', executed_at: 51, outcome: 'Owner-approved event created' },
      }
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const client = new EngramClient({
    baseUrl: 'http://engram.test/v1',
    apiKey: 'sk-test',
    fetch,
  })

  await client.twinContract()
  await client.ownerTwinContract()
  await client.twinContractHistory(25)
  await client.reviseTwinContract({
    goals: [{ title: 'Protect focus' }],
    confirm_external_writes: true,
  })
  await client.capabilities()
  await client.ownerCapabilities()
  const grant = await client.grantCapability({
    capability: 'calendar',
    permission: 'execute',
    scopes: ['calendars/personal/**'],
    credentialRef: { provider: 'keychain', key: 'engram/calendar' },
    expiresAt: 42,
    provenance: ['owner:grant-1'],
  })
  await client.revokeCapability('grant/a b')
  const authorization = await client.authorizeTwinAction({
    capability: 'calendar',
    permission: 'execute',
    resource: 'calendars/personal/events/42',
    externalWrite: true,
  })
  await client.confirmTwinAction('decision_1')
  await client.twinDecision('decision_1')
  await client.recordTwinAction({
    decisionId: 'decision_1',
    outcome: 'Owner-approved event created',
    executedAt: 51,
    provenance: ['executor:calendar-1'],
  })

  assert.deepEqual(calls.map((call) => [call.init.method ?? 'GET', call.url]), [
    ['GET', 'http://engram.test/v1/twin/contract'],
    ['GET', 'http://engram.test/v1/twin/control/contract'],
    ['GET', 'http://engram.test/v1/twin/control/contract/history?limit=25'],
    ['PUT', 'http://engram.test/v1/twin/contract'],
    ['GET', 'http://engram.test/v1/twin/capabilities'],
    ['GET', 'http://engram.test/v1/twin/control/capabilities'],
    ['POST', 'http://engram.test/v1/twin/capabilities'],
    ['POST', 'http://engram.test/v1/twin/capabilities/grant%2Fa%20b/revoke'],
    ['POST', 'http://engram.test/v1/twin/authorize'],
    ['POST', 'http://engram.test/v1/twin/decisions/decision_1/confirm'],
    ['GET', 'http://engram.test/v1/twin/decisions/decision_1'],
    ['POST', 'http://engram.test/v1/twin/actions/record'],
  ])
  assert.deepEqual(JSON.parse(calls[3].init.body), {
    goals: [{ title: 'Protect focus' }],
    confirm_external_writes: true,
  })
  assert.deepEqual(JSON.parse(calls[6].init.body), {
    capability: 'calendar',
    permission: 'execute',
    scopes: ['calendars/personal/**'],
    credential_ref: { provider: 'keychain', key: 'engram/calendar' },
    expires_at: 42,
    provenance: ['owner:grant-1'],
  })
  assert.equal('secret' in grant.grant.credential_ref, false)
  assert.deepEqual(JSON.parse(calls[8].init.body), {
    capability: 'calendar',
    permission: 'execute',
    resource: 'calendars/personal/events/42',
    description: '',
    high_risk: false,
    external_write: true,
  })
  assert.equal(authorization.executed, false)
  assert.deepEqual(JSON.parse(calls[11].init.body), {
    decision_id: 'decision_1',
    outcome: 'Owner-approved event created',
    executed_at: 51,
    provenance: ['executor:calendar-1'],
  })
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

test('temporal read options forward valid time and transaction time independently', async () => {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({
      context: '',
      tokens_est: 0,
      answer: "I don't have that in memory.",
      facts: [],
      as_of: 15,
      known_at: 20,
      redacted_sensitive: false,
      nodes: [],
      edges: [],
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

  await client.recall('Where does the user work?', { asOf: 15, knownAt: 20 })
  await client.search('Where does the user work?', { asOf: 15, knownAt: 20 })
  await client.graph({ asOf: 15, knownAt: 20 })

  assert.equal(JSON.parse(calls[0].init.body).as_of, 15)
  assert.equal(JSON.parse(calls[0].init.body).known_at, 20)
  assert.equal(JSON.parse(calls[1].init.body).known_at, 20)
  assert.equal(calls[2].url, 'http://engram.test/v1/graph?as_of=15&known_at=20')
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
