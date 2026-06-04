# Engram

**🌐 English | [中文](README.zh-CN.md)**

**An open-source long-term memory engine for LLM agents — built around one principle: every number we
publish, you can reproduce.**

**🎬 [Live demo / 在线动画演示 →](https://ly-wang19.github.io/engram-memory/)** — see how it works in 60 seconds.

Engram gives LLM agents durable, queryable memory across sessions: it stores what happened, distills
atomic facts, tracks how they change over time (bi-temporal), resolves contradictions without losing
history, and retrieves the right context with a hybrid semantic + lexical + graph + recency search.

> Status: **alpha**. The end-to-end loop runs with **zero setup** (no API keys, no services). The
> benchmark numbers below run on real models and are reproducible with one command. See
> [`CLAUDE.md`](CLAUDE.md) for the full project charter and [`RESULTS.md`](RESULTS.md) for the complete
> methodology and raw logs.

## Why another memory system?

The field has two real gaps, and we target both:

1. **Most memory systems lose to the dumb "full-context" baseline on accuracy** — they win on cost, not
   correctness. We always report full-context in the same table, so you can see exactly where we stand.
2. **Every vendor reports benchmark numbers on a different, non-reproducible harness.** Mem0 appears as
   58% / 66% / 92% across sources; three papers give three contradictory orderings. We ship **one neutral
   harness**, in-repo, with the official judge baked in — and publish the raw per-question logs.

In a field where every number is contested, *being the scoreboard everyone can verify* is the point.

## Results — LongMemEval_S (500 questions, official judge)

Measured on the real [LongMemEval_S](https://github.com/xiaowu0162/LongMemEval) benchmark (500 questions,
~50 sessions / ~115k tokens of haystack per question), graded by the **official category-specific
LongMemEval judge prompts**. Answerer **doubao-seed-2.0-pro**, judge **DeepSeek-V3.2** — the *same judge
family Hunyuan reports against*, so this is a fair, comparable number, not a friendly one.

**The headline system is `engram_lean`: it answers from a small *retrieved* slice, never the full history.**
This is the real test of a memory system — and the project's core thesis (beat full-context on accuracy at
a fraction of the tokens):

| System | Overall | Avg tokens | Notes |
|---|---:|---:|---|
| **Engram** (`engram_lean`) | **83.6%** | **9.6k** | retrieves a lean slice; 0 errors / 500 |
| full-context baseline (same answerer+judge) | 74.8% | 79k | stuffs the whole haystack in the prompt |
| Hunyuan Hy-Memory (closed; self-reported) | 85.2% | — | same DeepSeek judge family |
| Mem0-2026 / OMEGA (self-reported) | 94.4 / 95.4 | — | their own answer+judge pipelines |

**Engram beats the full-context baseline by +8.8 points while using ~8× fewer tokens** (9.6k vs 79k) — the
filtered slice is *more* accurate than the noisy full window, and the cost stays flat as history grows
(full-context can't). Per-category (`engram_lean`, full 500):

| Category | Score | n |
|---|---|---|
| single-session-assistant | 92.9% | 56 |
| abstention | 86.7% | 30 |
| knowledge-update | 87.5% | 72 |
| single-session-user | 87.5% | 64 |
| temporal-reasoning | 81.1% | 127 |
| multi-session | 79.3% | 121 |
| single-session-preference | 73.3% | 30 |

**Honest standing:** at **83.6%** Engram beats the full-context baseline decisively and at a fraction of the
tokens, but it is just **1.6 points below** Hunyuan (85.2) on the comparable judge and well below OMEGA/Mem0 (94–95, their
own backbones). The remaining gap is concentrated in multi-session reasoning and temporal aggregation. We
report this openly — no cherry-picked slice, no friendlier judge. Where Engram leads today is **token
efficiency, scalability, and reproducibility**; closing the accuracy gap is the public roadmap.

> Note on a prior number: an earlier table cited 86.0% for a system (`engram_full`) that prepended facts to
> the *entire* conversation history (~80k tokens). That system *contains* full-context, so it can't really
> lose to it — it doesn't validate the memory architecture. We replaced it as the headline with
> `engram_lean`, which retrieves a small slice and is the honest test of the thesis. The techniques borrowed
> from each leading system (ByteDance, Alibaba, Tencent, and others) are attributed in
> [`TECHNIQUES.md`](TECHNIQUES.md).

## Quickstart (zero setup, no API keys)

```bash
python examples/quickstart.py
```

Runs the full pipeline — ingest → consolidate → retrieve — using offline deterministic fallbacks (hashing
embedder, rule-based extractor, in-memory stores). Real backends (LanceDB, Kuzu, LiteLLM, BGE) plug in
behind the same interfaces via `pip install "engram-memory[all]"`.

```python
from engram import Memory

mem = Memory()
mem.add("My name is Wei and I work at Tencent.", user_id="u1")
mem.add("Actually I just switched jobs — I now work at Moonshot AI.", user_id="u1")
mem.consolidate()                      # System-2: extract facts, build graph, resolve conflicts

print(mem.search("Where does Wei work?", user_id="u1").answer())
# -> "Moonshot AI"  (the contradicted fact is invalidated, not deleted — history is preserved)
```

## Connect it to your agent

Engram ships a full **access layer** so any agent or app can use it — all backed by one multi-tenant
service (`MemoryService`), where each API key is an isolated memory namespace.

```bash
pip install "engram-memory[serve]"
export ENGRAM_OPEN=1                # dev: bearer text is the namespace (use ENGRAM_API_KEYS in prod)
export ENGRAM_EMBEDDER=bge-small    # or `hashing` for an instant, no-download dev server
export ENGRAM_LLM=deepseek          # optional: enables /v1/chat/completions generation
uvicorn engram.server.app:app --port 8000        # HTTP API + management console at /ui
```

**1. MCP server** — give Claude Desktop / Claude Code / Cursor a persistent memory (`engram_recall`,
`engram_remember`, `engram_search`, `engram_import`, …):

```bash
pip install "engram-memory[mcp]"
python -m engram.mcp                 # local memory at ~/.engram/data (zero external service)
# or proxy the server above:  python -m engram.mcp --api-url http://localhost:8000 --api-key sk-…
```
```jsonc
// claude_desktop_config.json
{ "mcpServers": { "engram": { "command": "python", "args": ["-m", "engram.mcp"] } } }
```

**2. JS/TS SDK + OpenAI-compatible API** — change one URL and your existing OpenAI code gets memory:
recall + inject before the model answers, remember the turn after.

```ts
import { EngramClient } from 'engram-memory'                  // npm i engram-memory
const engram = new EngramClient({ baseUrl: 'http://localhost:8000', apiKey: 'sk-alice-123' })
await engram.remember('I live in Shenzhen and work at Tencent.')
const { context } = await engram.recall('where do I live?')

// drop-in OpenAI compatibility (works with the official `openai` SDK too — just set base_url):
const out = await engram.chat.completions.create({ model: 'engram', messages: [
  { role: 'user', content: 'Remind me where I work.' } ] })
```

**3. Batch import** — bring your whole history (ChatGPT export, OpenAI messages, JSONL, transcript;
auto-detected):

```bash
python -m engram.connectors --file conversations.json --namespace me     # local, or --api-url …
```
```python
mem.import_data(open("conversations.json").read(), user_id="me")          # in-process
```

See [`examples/batch_import.py`](examples/batch_import.py) (zero-setup) and
[`clients/typescript/`](clients/typescript/) for the SDK.

## How it works

Engram is a **dual-process** memory system, modeled on the human System-1 / System-2 split: a fast write
path that never blocks on an LLM, and a slow consolidation path that does the heavy structuring offline.

```mermaid
flowchart TB
    ADD([add messages]) --> S1
    subgraph S1 [SYSTEM-1 · hot write path · no LLM · under 50ms]
        direction LR
        S1a[append lossless Episode] --> S1b[identity resolution<br/>across sessions/devices] --> S1c[light embed + enqueue]
    end
    S1 -. async queue .-> S2
    subgraph S2 [SYSTEM-2 · async consolidation · seconds]
        direction LR
        S2a[extract atomic Facts] --> S2b[build BI-TEMPORAL graph<br/>entities + relations] --> S2c[cheap conflict detect<br/>non-destructive invalidate] --> S2d[salience scoring + decay]
    end
    S2 --> TM
    subgraph TM [TYPED MEMORY · each type = its own store + retrieval policy]
        direction LR
        TMa[(Episodic)]
        TMb[(Semantic<br/>bi-temporal graph)]
        TMc[(Profile /<br/>Identity)]
        TMd[(Procedural)]
    end
    TM --> R
    Q([search query]) --> R
    subgraph R [READ PATH · hybrid retrieval · under 100ms]
        direction TB
        Ra[multi-hop query decomposition] --> Rb[parallel retrieve:<br/>dense vector + BM25 lexical + graph n-hop + recency/salience]
        Rb --> Rc[Reciprocal Rank Fusion + rerank] --> Rd[bi-temporal as-of filter] --> Re[abstention gate] --> Rf[assemble dated, provenance-tagged context]
    end
    Rf --> OUT([answer-ready context])
```

**The write path (System-1)** appends a lossless episode, resolves identity across sessions/devices, embeds
and enqueues — no LLM on the critical path, so it stays under ~50ms. **The consolidation path (System-2)**
runs asynchronously: it extracts atomic `(subject, predicate, object)` facts, builds a knowledge graph, and
resolves contradictions. **The read path** decomposes the question, retrieves through four complementary
channels in parallel, fuses and re-ranks them, applies a point-in-time temporal filter, and assembles a
dated, provenance-tagged context.

### What makes it different

| # | Design choice | Why it matters |
|---|---|---|
| 1 | **Bi-temporal facts** — every fact carries *valid time* (true in the world) **and** *transaction time* (when we learned it) | Makes "what did we know on date T?" (`as_of`) and knowledge-updates **first-class**, not bolted-on. This is why knowledge-update scores 93% and temporal 87%. |
| 2 | **Non-destructive conflict resolution** — a contradicted fact is *invalidated* (`invalid_at` + `supersedes` chain), never deleted | No silent memory corruption. Every fact answers "where did this come from?" and "what did it replace?" — full provenance + audit trail. |
| 3 | **Cheap conflict detection** — slot-match + embedding/NLI heuristics, escalate to an LLM **only** when ambiguous | Zep/Mem0-class temporal correctness **without** an LLM call per fact — the cost win at scale. |
| 4 | **Hybrid retrieval** — dense semantic + BM25 lexical + graph proximity + recency/salience, fused with RRF | No single retriever wins everywhere. The *validated* finding: **facts + raw chunks beats either alone** — facts add conflict-resolved/temporal signal, chunks restore lost detail. |
| 5 | **Dual-process split** — fast write, async consolidation | Read path stays sub-100ms while graph-building, dedup, and conflict resolution happen off the critical path. |
| 6 | **Pluggable everything** — LLM / embedder / vector store / graph store all sit behind interfaces with **zero-dep offline fallbacks** | `quickstart.py` and `pytest` run with **no API keys, no services**. Swap in BGE / LanceDB / Kuzu / any LLM via one config line. |
| 7 | **The reproducible harness** — one neutral eval, official judge baked in, full-context baseline in every table, raw logs published | In a field where every vendor's number is contested, *being the scoreboard anyone can verify* is the real moat. |

See [`CLAUDE.md`](CLAUDE.md) §3 for the full data model and conflict-resolution rules.

## Reproduce the benchmark

```bash
# 1. zero-dep smoke test + unit tests
pytest

# 2. retrieval recall on the real haystack (no LLM needed)
python eval/longmemeval.py --mode recall --data s --limit 500

# 3. full QA benchmark with the official judge — the headline engram_lean number + full-context baseline
#    (needs model access; any OpenAI-compatible provider works — see RESULTS.md for setup)
python eval/bench.py --data s --limit 500 --systems engram_lean,full_context \
    --answerer volcano:doubao-seed-2-0-pro-260215 --judge volcano:deepseek-v3-2-251201 \
    --extractor volcano:doubao-seed-1-6-flash-250615 --reasoning --persona \
    --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28
```

Raw per-question logs for the headline number live in [`RESULTS.md`](RESULTS.md). If you can't reproduce a
number we published, that's a bug — open an issue.

## License

Apache-2.0.
