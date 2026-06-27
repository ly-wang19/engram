# Cross-Agent Memory Layer

Engram's product job is not to be another chat log. It is the user's own memory layer that multiple
agents can share: Claude Code, Codex, Cursor, a local script, or a hosted app should all read and write
the same durable memory namespace.

Copy-paste setup recipes for specific clients live in
[`agent-adapters.md`](agent-adapters.md).

## Mental Model

```text
user namespace
  global memory              stable user preferences, identity, standing instructions
  project memory             repo/product decisions, benchmark rules, architecture notes
  session/thread provenance  where each memory came from
  working memory             short-lived state for the current session only
```

The Bearer key selects the namespace. `session_id` is provenance and lifecycle scope; it is not a
separate brain. A user's sessions should normally flow into the same namespace so future agents can
retrieve the right slice.

## Client Lifecycle

Every agent adapter should implement this loop:

```text
0. Start / wiring check
   agent_status(session_id)    # content-free: namespace/session/focus/counts/next actions

1. Before relevant work
   recall(query, session_id)

2. During work
   remember(durable fact/preference/decision, session_id, scope="auto" or "long")

3. End thread / switch task
   close_session(session_id)

4. Optional user-visible audit
   session_report(session_id) # what this session saved, sensitive facts redacted by default
```

Do not ask the user to decide every `remember` call. The adapter should remember stable facts,
preferences, decisions, project rules, bug conclusions, and architecture notes. Transient state can be
stored with `scope="working"` or left to `scope="auto"` routing.

You can exercise this whole loop without a server:

```bash
python examples/cross_agent_lifecycle.py --local
```

## Cross-Agent Handoff

The core product behavior is that one agent can write and another agent can benefit later:

```text
Codex session
  remember("Project decision: the launch checklist must include committed eval logs.",
           session_id="codex:super-memory:handoff-source")
  close_session("codex:super-memory:handoff-source")

Claude Code session
  recall("What launch checklist decision did the previous agent record?",
         session_id="claude-code:super-memory:handoff-target")
```

Both sessions must use the same user namespace, for example the same Bearer key. The session IDs are
provenance and cleanup scopes; they do not create separate brains. You can run a zero-server local smoke
test first:

```bash
python examples/cross_agent_handoff.py --local
```

To test a real HTTP server:

```bash
python examples/cross_agent_handoff.py --base http://localhost:8000 --key me --project super-memory
```

## Namespace Strategy

Use one namespace per user-owned memory space.

For a personal local setup:

```text
ENGRAM_API_KEY=me
```

For local MCP, each agent may launch its own `python -m engram.mcp` stdio process. As long as those
processes point at the same namespace and `ENGRAM_DATA_DIR`, Engram refreshes the hot in-memory cache when
another process saves a newer manifest, so an already-running target agent can see a source agent's
latest writes on the next recall/status call. Local write transactions are also serialized per namespace,
so two agents saving at the same time do not overwrite each other's read-modify-save snapshot.

For hosted multi-tenant service:

```text
Authorization: Bearer <user-private-key>
```

For stricter project separation, either use separate keys or encode project identity in the session id.
Prefer shared user namespace plus project-aware `session_id` first, because cross-project user
preferences should still transfer.

## Session ID Strategy

Use stable, inspectable IDs:

```text
claude-code:<repo-name>:<thread-id>
codex:<repo-name>:<thread-id>
cursor:<workspace-name>:<thread-id>
app:<product-name>:<conversation-id>
```

Examples:

```text
claude-code:super-memory:2026-06-26T10-30
codex:super-memory:thread-abc123
cursor:engram-frontend:issue-42
```

## MCP Integration

Use MCP for Claude Desktop, Claude Code, Cursor, and other MCP-capable agents:

```bash
pip install "engram-memory[mcp]"
python -m engram.mcp
```

Agent policy:

```text
At session start or when debugging memory wiring:
  engram_agent_status(session_id)

Before answering project/user-history-dependent requests:
  engram_recall(query, max_chunks=6)

When the user states a durable preference, project decision, or reusable fact:
  engram_remember(content, session_id, scope="long" or "auto")

When the user states temporary task/session state:
  engram_remember(content, session_id, scope="working")

When the user says to focus on or suppress a class of memories:
  engram_set_focus(track=[...]) or engram_set_focus(mute=[...])

When the thread ends or switches tasks:
  engram_close_session(session_id)
```

For a hosted Engram service:

```bash
ENGRAM_API_URL=http://localhost:8000 ENGRAM_API_KEY=me python -m engram.mcp
```

Before connecting a real agent, verify the exact Python runtime the agent will launch:

```bash
engram-agent-doctor --client codex --python /path/to/python
```

Fast path for a personal workstation:

```bash
engram-agent-bootstrap --local --dry-run --namespace me --python /path/to/python
engram-agent-bootstrap --local --namespace me --python /path/to/python
engram-agent-bootstrap --local --install-policy --namespace me --python /path/to/python
```

The bootstrap command installs Codex + project `.mcp.json`, runs the doctor, verifies the installed
config files, and prints the AGENTS.md policy text. Add `--install-policy` to write that policy into a
managed block in AGENTS.md; `--uninstall-policy` removes only the Engram block. It supports
`--bootstrap-targets codex` or `--bootstrap-targets mcp-json` for partial setup.

For HTTP service mode, use the same flow with `--api-url http://localhost:8000 --api-key me` instead of
`--local --namespace me`.

For Codex, `engram-agent-setup --install-codex --dry-run ...` previews the config change,
`--install-codex --doctor` writes `[mcp_servers.engram]` with a backup and immediately verifies the MCP
runtime, and `--uninstall-codex` removes only that Engram block.
For Claude Code, Cursor, and other project-level MCP clients, `engram-agent-setup --install-mcp-json`
writes an `engram` server into `.mcp.json` with the same dry-run, backup, doctor, and uninstall flow.

The doctor uses a temporary local namespace, starts two actual `python -m engram.mcp` stdio servers
against the same local data dir, optionally checks `~/.codex/config.toml` and `.mcp.json`, and verifies
that the MCP tools can preload the target session, remember from a source session, close it, report what
it saved, export a share-safe memory payload, recall the source memory from the already-running target
server, set focus, close both sessions, and persist at least one live fact. Add `--api-url` and
`--api-key` to verify the remote Engram service and user namespace too:

```bash
engram-agent-doctor --client codex --python /path/to/python \
  --api-url http://localhost:8000 \
  --api-key me \
  --codex-config ~/.codex/config.toml \
  --mcp-json ./.mcp.json
```

## User Ownership Loop

Agents should treat memory as user-owned state, not hidden model state:

```text
Inspect:
  engram_list_sessions(response_format="json")
  engram_list_facts(response_format="json")

Correct one memory:
  engram_update_fact(fact_id, object="new value")

Delete one memory:
  engram_delete_fact(fact_id, confirm=true)

Tune recall behavior:
  engram_get_focus()
  engram_set_focus(track=["project decisions"], mute=["health details"])

Export:
  engram_export(response_format="json")                  # share-safe by default
  engram_export(include_sensitive=true, response_format="json")  # explicit private export

Erase everything:
  engram_forget(confirm=true)
```

Use precise fact updates/deletes when the user corrects a memory. Reserve full namespace erasure for an
explicit "forget everything" request. Use focus when the user is asking to change what Engram emphasizes
or suppresses, not when they are correcting a specific stored fact.

## C-End Product Surface

A user-facing product should make the memory layer visible and governable. The minimum useful surface is
not a chat-log page; it is the lifecycle and ownership loop:

```text
Conversation setup
  show the current namespace/key
  let the app set a stable session_id such as app:<product>:<conversation-id>
  call agent_status(session_id) and show content-free counts/backlog/next actions
  list sessions with /v1/sessions so the user can see which agents/apps touched memory

During the conversation
  recall(query, session_id) before model calls that depend on prior context
  remember(content, session_id, scope="auto" or "long") for durable preferences/decisions/facts
  remember(content, session_id, scope="working") for short-lived task state

At conversation end
  close_session(session_id)
  show session_report(session_id) so the user can see what durable memory was saved

Memory management
  list/paginate/search facts with include_sensitive=false when rendering share-safe views
  allow precise edit/delete of single facts
  export share-safe JSON by default; require explicit action for include_sensitive=true
  require explicit confirmation before full namespace erase
```

The bundled management console at `/ui` follows this pattern: the Conversations view exposes
`session_id`, write `scope`, content-free agent status, close-session, and session report; the Facts and
Privacy views cover inspect/edit/delete/focus/export/erase.

## OpenAI-Compatible Integration

For apps already using the OpenAI SDK, change `base_url` and pass Engram's `memory` extension:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="me")

client.chat.completions.create(
    model="engram",
    messages=[{"role": "user", "content": "Continue the benchmark work."}],
    extra_body={
        "memory": {
            "session_id": "codex:super-memory:thread-abc123",
            "recall": True,
            "remember": True,
            "scope": "auto",
        }
    },
)
```

When the thread ends:

```bash
curl -s -X POST http://localhost:8000/v1/sessions/close \
  -H "Authorization: Bearer me" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"codex:super-memory:thread-abc123"}'
```

## TypeScript SDK Integration

```ts
import { EngramClient } from 'engram-memory'

const sessionId = 'codex:super-memory:thread-abc123'
const engram = new EngramClient({ baseUrl: 'http://localhost:8000', apiKey: 'me' })

const status = await engram.agentStatus({ sessionId })
console.log(status.session, status.focus)

const memory = await engram.recall('current project rules and user preferences', { sessionId })

await engram.remember('Project rule: benchmark claims require committed raw logs.', {
  sessionId,
  scope: 'long',
})

await engram.closeSession(sessionId)
const report = await engram.sessionReport(sessionId)
console.log(report.facts)
```

For drop-in chat:

```ts
await engram.chat.completions.create({
  model: 'engram',
  messages: [{ role: 'user', content: 'Continue the release notes.' }],
  memory: {
    session_id: sessionId,
    recall: true,
    remember: true,
    scope: 'auto',
  },
})
```

## What To Remember

Good durable memories:

- User preferences and standing instructions
- Project rules and architecture decisions
- Reusable debugging conclusions
- Benchmark/evaluation constraints
- People, teams, repos, and recurring workflows

Avoid durable memories:

- Secrets, tokens, credentials
- One-off command output
- Large logs
- Passing physical/emotional state
- Temporary task state unless marked as working memory

## Product Bar

A good cross-agent adapter should make memory automatic:

```text
The user should not have to say "call remember now."
The user should be able to inspect, correct, delete, and export what was remembered.
The same memory should improve the next agent, not just the current chat.
```
