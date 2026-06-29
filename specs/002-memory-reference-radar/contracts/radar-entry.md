# Contract: Radar Entry

Each source in `research.md` should be representable by this contract. Markdown tables may split these
fields across sections, but no promoted source should omit them.

## Required Fields

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Stable source name used throughout the radar |
| `url` | Yes | Canonical public URL |
| `category` | Yes | Source category such as memory system, GraphRAG, knowledge workspace, benchmark, or radar |
| `priority` | Yes | P0, P1, P2, benchmark/radar, or archived |
| `architecture_signal` | Yes | The strongest design pattern worth studying |
| `engram_learning` | Yes | What Engram can learn without copying implementation |
| `adoption_candidate` | Yes | Candidate informed by this source, or `research-only` |
| `evidence_status` | Yes | External-only, reproduced locally, benchmarked in Engram harness, contradicted, or insufficient evidence |
| `clean_room_license_status` | Yes | Architecture-only, license pending, license reviewed, or blocked |
| `last_verified` | Recommended | Date the URL and high-level summary were checked |

## Acceptance Checks

- The URL returns a successful response or is marked stale with the check date.
- The entry states whether claims are external-only or Engram-reproduced.
- The entry does not copy benchmark numbers into Engram evidence.
- The entry can be mapped to at least one capability pattern or explicitly marked research-only.
- The entry's license status blocks code reuse until review is complete.

## Example

```text
name: LLM Wiki
url: https://github.com/nashsu/llm_wiki/blob/main/README_CN.md
category: Knowledge compilation workspace
priority: P1
architecture_signal: Immutable sources -> generated Wiki -> schema/purpose; graph diagnostics
engram_learning: Human-readable, source-backed memory workspace and graph-health checks
adoption_candidate: Memory workspace diagnostics
evidence_status: External-only
clean_room_license_status: GPLv3; architecture-only clean-room
last_verified: 2026-06-29
```
