# Agent Adapter Recipes

These recipes turn Engram into a shared memory layer for multiple agents. You can run it two ways:

- **Local zero-server MCP**: each agent launches `python -m engram.mcp` and shares the same local
  namespace/data dir.
- **HTTP service**: agents proxy through a local or hosted Engram server with a Bearer key namespace.

For the fastest personal workstation setup, use local MCP first:

```bash
pip install "engram-memory[mcp]"
engram-agent-bootstrap --local --dry-run --namespace me --python /path/to/python
engram-agent-bootstrap --local --namespace me --python /path/to/python --install-policy
```

That installs Codex + project `.mcp.json` against the same local namespace, runs the doctor, and can write
the AGENTS.md memory policy. The doctor validates that the installed config files contain the requested
local `--namespace`, so Codex and project MCP clients do not silently point at different memory spaces.

For a hosted or self-hosted HTTP service, start Engram first:

```bash
pip install "engram-memory[server]"
export ENGRAM_OPEN=1
export ENGRAM_EMBEDDER=hashing
uvicorn engram.server.app:app --port 8000
```

Use one Bearer key as the user's namespace:

```bash
export ENGRAM_API_URL=http://localhost:8000
export ENGRAM_API_KEY=me
```

The intended lifecycle is:

```text
recall before work -> remember durable information during work -> close the session at the end
```

You can generate the snippets below instead of copying them by hand:

```bash
engram-agent-setup --client all --local --namespace me
engram-agent-setup --client codex --api-url http://localhost:8000 --api-key me
engram-agent-setup --client claude-code --local --namespace me
```

For HTTP onboarding, use the same bootstrap command with URL/key. It installs both Codex's `config.toml`
entry and the project `.mcp.json` entry, runs the doctor, and prints a ready-to-paste AGENTS.md policy:

```bash
engram-agent-bootstrap --dry-run \
  --api-url http://localhost:8000 \
  --api-key me

engram-agent-bootstrap \
  --python /path/to/python \
  --api-url http://localhost:8000 \
  --api-key me

engram-agent-bootstrap \
  --install-policy \
  --python /path/to/python \
  --api-url http://localhost:8000 \
  --api-key me
```

Use `--bootstrap-targets codex` or `--bootstrap-targets mcp-json` if you only want one side of the
setup. `--install-policy` writes the suggested Engram operating policy into a managed AGENTS.md block;
`--uninstall-policy` removes only that block. Use `--no-doctor` only when you need to skip runtime
verification.

For Claude Code, Cursor, and generic MCP clients that read project `.mcp.json`, you can install the
project config directly. The write mode is explicit, backs up `.mcp.json` before changing it, and
supports dry-run/uninstall:

```bash
engram-agent-setup --install-mcp-json --dry-run \
  --api-url http://localhost:8000 \
  --api-key me

engram-agent-setup --install-mcp-json --doctor \
  --doctor-client claude-code \
  --api-url http://localhost:8000 \
  --api-key me

engram-agent-setup --uninstall-mcp-json
```

For Codex, you can also install the config directly. The write mode is explicit, backs up
`~/.codex/config.toml` before changing it, and supports dry-run/uninstall:

```bash
engram-agent-setup --install-codex --dry-run \
  --api-url http://localhost:8000 \
  --api-key me

engram-agent-setup --install-codex \
  --api-url http://localhost:8000 \
  --api-key me \
  --doctor

engram-agent-setup --uninstall-codex
```

If `python -m engram.mcp` fails because that Python does not have the MCP SDK installed, point the
generator at the Python executable where you installed `engram-memory[mcp]`:

```bash
engram-agent-setup --client codex --python /path/to/python \
  --api-url http://localhost:8000 \
  --api-key me
```

Before wiring the config into a real agent, run the doctor against the same Python executable. It uses a
temporary local namespace, starts two actual `python -m engram.mcp` stdio servers against the same data
dir, and verifies the cross-agent MCP lifecycle end to end: the target server preloads an empty session,
the source session writes, closes, reports what was saved, the already-running target server recalls the
source memory, the runtime returns a safe memory export, and both sessions close.
Pass config paths when you also want it to confirm the installed files contain the expected Engram server
entry:

```bash
engram-agent-doctor --client codex --python /path/to/python

engram-agent-doctor --client codex --python /path/to/python \
  --codex-config ~/.codex/config.toml \
  --mcp-json ./.mcp.json
```

For a hosted or self-hosted HTTP service, add the same URL/key that the agent config will use. This
checks the local MCP runtime, the real stdio launch path, the optional config files, and the remote user
namespace with the same source-agent to target-agent handoff plus session report audit:

```bash
engram-agent-doctor --client codex --python /path/to/python \
  --api-url http://localhost:8000 \
  --api-key me
```

## Claude Code

Claude Code can add MCP servers from JSON. For a remote or self-hosted Engram HTTP service, proxy it
through the Engram MCP server:

```bash
claude mcp add-json engram \
  '{"type":"stdio","command":"python","args":["-m","engram.mcp","--api-url","http://localhost:8000","--api-key","me"]}'
```

For a purely local memory store with no HTTP service:

```bash
claude mcp add-json engram \
  '{"type":"stdio","command":"python","args":["-m","engram.mcp","--namespace","me"]}'
```

Project-shareable `.mcp.json`:

```json
{
  "mcpServers": {
    "engram": {
      "type": "stdio",
      "command": "python",
      "args": [
        "-m",
        "engram.mcp",
        "--api-url",
        "${ENGRAM_API_URL:-http://localhost:8000}",
        "--api-key",
        "${ENGRAM_API_KEY:-me}"
      ]
    }
  }
}
```

Direct project install with backup:

```bash
engram-agent-setup --install-mcp-json \
  --python /path/to/python \
  --api-url http://localhost:8000 \
  --api-key me
```

Install and immediately verify the MCP runtime:

```bash
engram-agent-setup --install-mcp-json --doctor \
  --doctor-client claude-code \
  --python /path/to/python \
  --api-url http://localhost:8000 \
  --api-key me
```

Rollback/uninstall removes only the `engram` entry from `.mcp.json` and backs up the current file first:

```bash
engram-agent-setup --uninstall-mcp-json
```

Tell Claude Code the operating policy once in project instructions:

```text
Use Engram as the user's cross-agent long-term memory.
At session start or when debugging memory wiring, call engram_agent_status with a session_id like
claude-code:<repo>:<thread>.
Before tasks that may depend on prior user/project context, call engram_recall with a session_id like
claude-code:<repo>:<thread>.
When the user states a durable preference, project rule, decision, or reusable fact, call
engram_remember with that session_id and `scope="long"` or `scope="auto"`.
When the user states short-lived task state, call engram_remember with `scope="working"`.
When the user asks you to emphasize or suppress a class of memories, call engram_set_focus; use
engram_get_focus to inspect the current focus policy.
When the thread ends or you switch tasks, call engram_close_session with the same session_id.
Do not store secrets, credentials, or large logs.
```

## Codex

Codex stores MCP servers in `config.toml`. The CLI and IDE extension share this configuration.

CLI setup:

```bash
codex mcp add engram -- python -m engram.mcp \
  --api-url http://localhost:8000 \
  --api-key me
```

Use `engram-agent-setup --client codex --python /path/to/python ...` if Codex should launch a specific
virtualenv or Python install.

Direct install with backup:

```bash
engram-agent-setup --install-codex \
  --python /path/to/python \
  --api-url http://localhost:8000 \
  --api-key me \
  --doctor
```

Rollback/uninstall removes only `[mcp_servers.engram]` and backs up the current config first:

```bash
engram-agent-setup --uninstall-codex
```

Equivalent `~/.codex/config.toml`:

```toml
[mcp_servers.engram]
command = "python"
args = [
  "-m",
  "engram.mcp",
  "--api-url",
  "http://localhost:8000",
  "--api-key",
  "me",
]
```

Local-only store:

```toml
[mcp_servers.engram]
command = "python"
args = ["-m", "engram.mcp", "--namespace", "me"]
```

Suggested `AGENTS.md` note:

```text
Use the Engram MCP tools as shared long-term memory across agent sessions.
At the start of a task or when debugging memory wiring, call engram_agent_status with the current
session_id. Then call engram_recall for relevant user/project context when the task may depend on prior
sessions.
Use session_id = codex:<repo-name>:<thread-id> when remembering or closing a session.
Call engram_remember with scope="long" or scope="auto" for durable preferences, decisions, project
rules, and reusable facts.
Call engram_remember with scope="working" for short-lived current-task state.
When the user asks to correct or delete a memory, call engram_list_facts to get the fact id, then call
engram_update_fact or engram_delete_fact(confirm=true). Do not use engram_forget unless the user asks to
erase the whole namespace.
When the user asks to export their memory, call engram_export(response_format="json"); it is share-safe by
default, and include_sensitive=true should only be used for an explicit private export.
When the user asks you to remember some topics more strongly or stop surfacing a category unless asked,
call engram_set_focus. Use engram_get_focus before changing focus when the current policy matters.
Call engram_close_session when the task/thread ends.
Never store secrets, credentials, or large raw logs.
```

## Cursor / Generic MCP Clients

Use the same stdio MCP server shape in whatever MCP config your client accepts:

```json
{
  "mcpServers": {
    "engram": {
      "type": "stdio",
      "command": "python",
      "args": [
        "-m",
        "engram.mcp",
        "--api-url",
        "http://localhost:8000",
        "--api-key",
        "me"
      ]
    }
  }
}
```

If the client supports streamable HTTP MCP, Engram can serve it directly:

```bash
python -m engram.mcp --http --host 127.0.0.1 --port 8765 \
  --api-url http://localhost:8000 \
  --api-key me
```

Then connect the client to:

```text
http://127.0.0.1:8765/mcp
```

## OpenAI-Compatible Apps

If your app already uses the OpenAI SDK, point it at Engram and pass the memory extension.

Python:

```python
from openai import OpenAI

session_id = "app:my-product:conversation-123"
client = OpenAI(base_url="http://localhost:8000/v1", api_key="me")

client.chat.completions.create(
    model="engram",
    messages=[{"role": "user", "content": "Continue the release note work."}],
    extra_body={
        "memory": {
            "session_id": session_id,
            "recall": True,
            "remember": True,
            "scope": "auto",
        }
    },
)
```

Close the session when the thread ends:

```bash
curl -s -X POST "$ENGRAM_API_URL/v1/sessions/close" \
  -H "Authorization: Bearer $ENGRAM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"app:my-product:conversation-123"}'
```

## Session ID Conventions

Use IDs that make provenance obvious:

```text
claude-code:<repo>:<thread>
codex:<repo>:<thread>
cursor:<workspace>:<thread>
app:<product>:<conversation>
```

Keep the same `session_id` for recall, remember, and close. Use the same API key across tools when you
want them to share memory; use different keys when you need hard separation.

## Sources

- Codex MCP configuration: <https://developers.openai.com/codex/mcp>
- Claude Code MCP configuration: <https://docs.anthropic.com/en/docs/claude-code/mcp>
