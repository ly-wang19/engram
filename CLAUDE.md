# Engram — the open-source memory engine built to be #1 in the world

> **Project codename:** Engram (an *engram* is the physical trace a memory leaves in the brain).
> The name is provisional — rename freely. The package is `engram`, the repo is "Super-Memory".

This file is the single source of truth for *what we are building and why*. Read it fully before
writing code. It is written for any agent or human contributor walking in cold.

---

## 0. The mission (non-negotiable)

**Build the best long-term memory system in the world, fully open source, and prove it is #1 on
every public memory benchmark.**

- **Open-source #1 is the floor, global #1 is the ceiling.** Apache-2.0, reproducible, self-hostable.
- **Baselines we must beat** (in priority order): Tencent **Hunyuan Hy-Memory** (the named baseline),
  **Mem0 / Mem0g**, **Zep / Graphiti**, **Letta / MemGPT**, **MIRIX**, **MemoryOS**, **Cognee**,
  **Supermemory**, **OMEGA**, and — the one everybody quietly loses to — the **full-context baseline**.
- **The win condition is not a single number.** It is: *highest accuracy on a neutral, reproducible
  harness, at a fraction of the tokens and latency of full-context, with results anyone can replicate.*

We do not ship marketing numbers. We ship a harness that anyone can run to reproduce our numbers.
That discipline is itself a competitive weapon (see §4).

---

## 1. Know the enemy — competitive intelligence (as of 2026-05)

### 1.1 The baseline to beat: Tencent Hunyuan "Hy-Memory"

| Aspect | What Hunyuan does | Our read |
|---|---|---|
| Structure | 6-layer memory: L1 raw traces → L2 atomic facts → L3 identity profile → L4–L6 mental models / intent | Good hierarchy. We adopt the *spirit* (typed + tiered) and go further with a bi-temporal graph. |
| Dual path | System-1 (online, ms, writes L1–L4) + System-2 (offline, consolidates L5–L6) | We use the same dual-process split. It is the right shape. |
| Evolution chain | `supersedes` pointers link memories into causal chains | We do this **and** add bi-temporal validity (`valid_at`/`invalid_at`) + provenance + audit trail. |
| Benchmarks (self-reported) | **LongMemEval 85.2%**, PersonaMem 76.9% | LongMemEval 85.2 is **beatable** — OMEGA hit 95.4, Mem0-2026 hit 94.4. This is our first scalp. |
| Models | Memory model Kimi-K2.5, judge DeepSeek-V3.2 | Model-pluggable on our side; we report on multiple backbones. |
| **Openness** | **Closed commercial product. Not open source.** | **This is the opening.** No strong system owns the open-source memory crown. We take it. |

**Strategic conclusion:** Hunyuan validates the dual-process + layered design and gives us a concrete
target (LongMemEval 85.2 → we must clear 90+, aim 95+). Because it is closed, simply *being open and
competitive* wins the "open-source #1" framing. We then chase global #1 on the merits.

### 1.2 The scoreboard — scores to beat

| Benchmark | Category | Score to beat | Held by | Notes |
|---|---|---|---|---|
| **LongMemEval_S** | Overall | **95.4 / 94.4** | OMEGA / Mem0-2026 | Hunyuan only 85.2 here. Primary target. |
| LongMemEval_S | Multi-session reasoning | ~83 | OMEGA | **Hardest category — main attack surface.** |
| LongMemEval_S | Knowledge updates | ~96 | OMEGA | Needs real conflict/temporal handling. |
| LongMemEval_S | Temporal reasoning | ~94 | OMEGA | Needs bi-temporal model. |
| **LongMemEval_M** (~1.5M tok) | Overall | **no published SOTA** | — | **Open, winnable flag. Plant it here.** |
| **LOCOMO** | Overall (headline) | 92.5 / 93.05 | Mem0-2026 / EverMemOS | Benchmark is partly discredited (see §1.4). |
| LOCOMO | Overall (defensible) | ~73 / ~75 | full-context / Zep | The honest ceiling on a clean harness. |
| LOCOMO | Multi-hop J | ~51 | Mem0 | **Weakest area industry-wide — attack it.** |
| LOCOMO | Temporal J | ~58 | Mem0g | Bi-temporal model should dominate here. |
| **PersonaMem-v2** | Overall | only **37–48%** | frontier LLMs | Far from solved. High-upside target. |
| **BEAM** | 1M / 10M tokens | 64.1 / 48.6 | Mem0-2026 | Scaling frontier — degrades ~25% at 10M. |

### 1.3 The competitor architectures (steal the best, fix the rest)

| System | Pattern | Temporal model | Conflict handling | Take this | Beat this |
|---|---|---|---|---|---|
| **Mem0 / Mem0g** | Vector (+Neo4j), multi-signal fusion | timestamps (weak) | LLM ADD/UPDATE/DELETE | multi-signal retrieval fusion | LLM-on-every-turn cost; weak temporal |
| **Zep / Graphiti** | Temporal KG (3-tier) | **bi-temporal (4 stamps)** | non-destructive invalidation | **the bi-temporal model — best in field** | LLM-heavy writes; small models can't read its graph |
| **Letta / MemGPT** | OS paging + self-edit blocks + sleep-time | none formal | agent self-edits | **sleep-time consolidation** | no conflict/temporal guarantees |
| **MIRIX** | Multi-agent, 6 typed memories | episodic ts | meta-manager routing | **typed memory taxonomy** | 8-agent orchestration cost |
| **MemoryOS** | OS hierarchy STM/MTM/LPM + heat paging | recency decay | FIFO/heat eviction | **heat-based tiering** | fixed queue sizes; surface metrics |
| **Cognee** | ECL hybrid graph+vector | none formal | background re-sync | ECL pipeline shape | thin/low-credibility eval |
| **A-MEM** | Zettelkasten notes + dynamic links | timestamps | memory evolution (rewrites) | atomic-note linking | evolution silently corrupts old notes |
| **MemoryBank** | summaries + forgetting curve | **Ebbinghaus decay** | none | **salience decay (nobody did it well)** | crude impl; near-bottom scores now |
| **Generative Agents** | memory stream | recency | none | recency+importance+relevance score | O(n) scan; no scaling |
| **HIPPOCAMPUS** | binary signatures + wavelet matrix | — | — | **linear-scaling compressed index** | research-stage |

### 1.4 What's broken in the field (our openings)

1. **No memory system beats full-context on *accuracy*.** They win on cost/latency only. *Beating
   full-context on accuracy is the single unclaimed prize.* → **Strategic Bet A.**
2. **Every vendor self-marks on a different harness.** Mem0 appears as 58% / 66% / 92% across sources;
   Mem0-paper, Zep-paper, and MIRIX-paper give three contradictory orderings. → **Strategic Bet D.**
3. **LOCOMO is partly discredited:** ~6.4% of its answer key is wrong; its gpt-4o-mini judge accepts
   ~63% of intentionally-wrong-but-adjacent answers; conversations are only 16–26K tokens (full-context
   just wins). We treat LOCOMO as *necessary but not sufficient*, fix its keys, and use a stronger judge.
4. **Multi-hop (~51%) and multi-session reasoning (~83%)** are weak everywhere. → **Strategic Bet B.**
5. **Identity/multi-session resolution and staleness** are admitted-open gaps (even Mem0's own blog).

---

## 2. Strategic bets (how we actually win)

These are the differentiators. Every design decision must serve at least one.

- **Bet A — Beat full-context on accuracy, not just cost.** Retrieval must be precise enough that the
  *filtered* context outperforms the *noisy full* window (removing distractors raises accuracy). We
  verify this on LongMemEval/BEAM, reporting accuracy **and** tokens **and** latency together.
- **Bet B — Win the hard categories with a multi-hop query planner.** Decompose multi-hop / multi-session
  questions into sub-queries, walk the graph, aggregate. This is the field's soft spot; we make it our
  strength.
- **Bet C — Bi-temporal + cheap conflict detection as core primitives.** Adopt Zep's `valid_at/invalid_at`
  + transaction-time model, but detect contradictions *without an LLM call per fact* (slot model +
  embedding/NLI heuristics, escalate to LLM only when ambiguous). Non-destructive invalidation + full
  provenance/audit trail. This nails knowledge-updates and temporal categories.
- **Bet D — The reproducible-harness moat.** One fixed, open, neutral eval pipeline across all benchmarks;
  corrected LOCOMO keys; strong judge; report tokens+latency+accuracy together; publish it and invite
  replication. In a field where every number is contested, *being the trustworthy scoreboard* is power.
- **Bet E — Linear scaling + salience decay so we hold up at 10M+ tokens** where everyone else degrades.
- **Bet F — Async/sleep-time consolidation** keeps the read path sub-100ms while graph-building, dedup,
  and conflict resolution happen off the critical path.

**Principle: compose, don't pick.** No single pattern wins. We compose typed memory (MIRIX) + temporal
graph (Zep) + heat-tiered paging (MemoryOS) + multi-signal fusion (Mem0-2026) + salience decay
(MemoryBank) + sleep-time consolidation (Letta) + linear-scaling index (HIPPOCAMPUS). The gaps are in
the *seams* between these — that's where we build.

---

## 3. Architecture

Dual-process, exactly like the human-inspired System-1 / System-2 split (and like Hunyuan), but with a
bi-temporal typed-memory core and a multi-hop read path.

```
                      ┌─────────────────────────────────────────────────────────────┐
   add(messages) ───▶ │  SYSTEM-1  (hot write path, no LLM on critical path, <50ms)  │
                      │  • append lossless Episode (append-only log)                  │
                      │  • identity resolution (user/entity across sessions/devices)  │
                      │  • light embedding + enqueue for consolidation                │
                      └───────────────┬─────────────────────────────────────────────┘
                                      │ (async queue)
                      ┌───────────────▼─────────────────────────────────────────────┐
                      │  SYSTEM-2  (async / sleep-time consolidation, seconds)        │
                      │  • extract atomic Facts from episodes                         │
                      │  • build BI-TEMPORAL knowledge graph (entities + relations)   │
                      │  • cheap conflict detect → non-destructive invalidate         │
                      │    (supersedes chain + valid_at/invalid_at + provenance)      │
                      │  • salience scoring + decay/reinforcement (forgetting)        │
                      │  • hierarchical abstraction: session summary → profile →      │
                      │    mental models;  community clustering                       │
                      └───────────────┬─────────────────────────────────────────────┘
                                      │ writes into TYPED MEMORY
   ┌──────────────────────────────────▼──────────────────────────────────────────────┐
   │  TYPED MEMORY (each type = its own store + retrieval policy)                       │
   │  Episodic │ Semantic (bi-temporal graph) │ Profile/Identity │ Procedural │ Working │
   │  ── backed by pluggable VectorStore + GraphStore + DocStore, heat-tiered hot/warm/cold │
   └──────────────────────────────────▲──────────────────────────────────────────────┘
                                      │
                      ┌───────────────┴─────────────────────────────────────────────┐
   search(query) ───▶ │  READ PATH  (hybrid retrieval, <100ms target)                 │
                      │  1. query understanding + MULTI-HOP decomposition (planner)   │
                      │  2. parallel retrieve: dense vec + BM25 lexical + graph n-hop │
                      │     + recency/salience scoring                                │
                      │  3. fusion: Reciprocal Rank Fusion + cross-encoder rerank     │
                      │  4. BI-TEMPORAL "as-of" filtering (what we believed at T)     │
                      │  5. ABSTENTION gate (answer not in memory → say so)           │
                      │  6. assemble context: dedup, provenance-tagged, token-budgeted│
                      └───────────────────────────────────────────────────────────────┘
```

### 3.1 Core data model (bi-temporal from day one)

- **Episode** — a raw, lossless turn/event. `event_time` (world time) + `ingested_at` (transaction time).
- **Fact** — an atomic `(subject, predicate, object)` claim with text, embedding, `salience`,
  `confidence`, `provenance` (source episode ids), and **two time axes**:
  - *valid time* `valid_at` / `invalid_at` — when the fact is true in the world.
  - *transaction time* `created_at` / `expired_at` — when we learned / retracted it.
  - `supersedes` — pointer to the fact this one replaces (the evolution chain).
- **Entity / Relation** — graph nodes/edges; edges carry the same bi-temporal stamps.

This model is what makes knowledge-updates, temporal reasoning, and "as-of" queries first-class instead
of bolted-on. **Never hard-delete a contradicted fact — invalidate it** (set `invalid_at`), keep history.

### 3.2 Conflict resolution (cheap, then escalate)

When a new fact arrives for an existing `(subject, predicate)` slot with a different object:
1. **Slot match** (exact) → likely update; **embedding distance / NLI** → contradiction vs. elaboration.
2. If clearly contradictory and temporally ordered → invalidate the old (`old.invalid_at = new.valid_at`),
   set `new.supersedes = old.id`. No LLM call.
3. Only ambiguous cases escalate to an LLM adjudicator. This is the cost win over "LLM on every fact".

### 3.3 Retrieval scoring

```
score(item | query) = w_sem·cos(q, item)            # dense semantic
                     + w_lex·bm25(q, item)            # lexical / exact terms
                     + w_graph·graph_proximity(item)  # n-hop from query entities
                     + w_rec·exp(-Δt / τ)             # recency decay
                     + w_sal·salience(item)           # importance / reinforcement
```
Weights are config-driven and tuned per benchmark on the harness (never hand-waved). Fusion across
retrievers uses Reciprocal Rank Fusion, then an optional cross-encoder rerank for the top-k.

---

## 4. Evaluation discipline (Bet D — this is how we earn the #1 claim)

**A number we cannot reproduce does not exist.** Rules:

1. **One harness, in-repo** (`eval/`). Same ingestion, same answer-prompt, same judge, declared backbone.
2. **Report the triple:** accuracy **+** total tokens **+** p50/p95 latency. Never accuracy alone.
3. **Always include the full-context baseline** in every results table. If we don't beat it on accuracy,
   we say so.
4. **Fix LOCOMO's known-bad keys** and use a strong (non-gameable) judge; document every deviation.
5. **Run multiple backbones** (a small open model + a frontier model). Memory quality must not depend on
   one model's ability to read our graph.
6. **Publish the harness and the raw run logs.** Invite replication. This is the moat.

Target benchmarks, in order: LongMemEval_S → LongMemEval_M → LOCOMO (corrected) → PersonaMem-v2 → BEAM.

---

## 5. Repository structure

```
.
├── CLAUDE.md                  # this charter (source of truth)
├── README.md                  # short public-facing intro
├── LICENSE                    # Apache-2.0
├── pyproject.toml             # packaging; core has ZERO hard deps, backends are extras
├── engram/                    # the library
│   ├── __init__.py            # public API surface
│   ├── config.py              # settings + pluggable backend selection
│   ├── types.py               # bi-temporal dataclasses (Episode, Fact, Entity, Relation, ...)
│   ├── memory.py              # Memory facade: add() / search() / consolidate() / as_of()
│   ├── embed/                 # Embedder interface + zero-dep hashing fallback
│   ├── llm/                   # LLM interface + rule-based offline fallback
│   ├── store/                 # VectorStore / GraphStore / DocStore interfaces + in-memory impls
│   ├── ingest/                # System-1 fast write path
│   ├── consolidate/           # System-2: extractor, graph_builder, conflict, decay, summarizer
│   └── retrieve/              # hybrid, fusion, planner (multi-hop), temporal (as-of), abstention
├── eval/                      # the reproducible harness (Bet D) + tiny built-in synthetic set
├── examples/                  # quickstart.py — end-to-end runnable demo
└── tests/                     # smoke + unit tests
```

**Zero-setup invariant:** `python examples/quickstart.py` and `pytest` MUST run with **no API keys and no
third-party services**, using the hashing embedder + rule-based extractor + in-memory stores. Real
backends (LanceDB/Qdrant/pgvector, Kuzu/Neo4j, LiteLLM for any LLM, BGE embeddings/reranker) are optional
extras that slot in behind the same interfaces. We never break the zero-setup demo.

---

## 6. Tech stack & backends

- **Language:** Python ≥ 3.10. Core is pure-stdlib. Hot path (retrieval/index) may later drop to Rust if
  the harness shows it's the bottleneck — measure first.
- **Embeddings:** interface + hashing fallback (offline); production via BGE-m3 / OpenAI / local.
- **LLM:** interface + rule-based fallback (offline); production via LiteLLM (Kimi, DeepSeek, GPT, Qwen,
  local — model-agnostic so we report on multiple backbones).
- **Vector store:** in-memory (default) → LanceDB (embedded, zero-server) → Qdrant / pgvector.
- **Graph store:** in-memory (default) → Kuzu (embedded) → Neo4j / FalkorDB.
- **Lexical:** in-memory BM25 → tantivy / bm25s.
- **Serving (later):** FastAPI server, Python SDK, OpenAI-memory-compatible API, and an **MCP server**
  (high-leverage for adoption in 2026 agent stacks).
- **License:** Apache-2.0 (matches every major competitor; maximally permissive for adoption).

---

## 7. Roadmap (milestones tied to numbers, not vibes)

- **M0 — Skeleton + runnable loop (this milestone).** Zero-dep end-to-end `add → consolidate → search`
  with bi-temporal facts, conflict/invalidation, multi-hop planner stub, as-of query, and a tiny in-repo
  eval that produces a score. *Proves the architecture runs.*
- **M1 — Real backends + real eval (IN PROGRESS).** Done: real local embeddings (BGE via
  sentence-transformers, no key), LLM extractor (LiteLLM: DeepSeek/Qwen/etc.), LLM judge, and a real
  LongMemEval runner (`eval/longmemeval.py`) with a full-context baseline on the same items.
  - **Full 500-question LongMemEval *oracle* (DeepSeek extract+answer, Qwen judge, 0 errors): Engram
    58.4% vs full-context 50.8%** — beats the baseline by **+7.6** on the stable full set. Wins
    multi-session (55.6/35.3), single-session-user (88.6/75.7), temporal (35.3/29.3); loses
    knowledge-update (67.9/75.6); single-session-preference near-zero (6.7%) = a real bug to fix. (An
    18-item slice earlier read 83.3% — small samples were optimistic, exactly as cautioned.)
  - **Validated finding (load-bearing):** pure fact-extraction *loses recall* (38.9%); the read path MUST
    be **hybrid = consolidated facts + retrieved raw session chunks** (jumped to 83.3%). Facts add
    conflict-resolved/bi-temporal signal; chunks restore detail. Never ship facts-only QA.
  - **Caveats before claiming #1:** absolute is deflated by the Qwen judge + DeepSeek answerer
    (full-context here is only 50.8 vs ~60 reported with GPT-4o) — so NOT comparable to 85.2/95.4 yet;
    oracle (not _S — needle-in-haystack retrieval + token-leanness not yet shown). The **+7.6 over
    full-context** is the real, fair, within-harness signal.
  - **Remaining for M1→M2:** full 500 + GPT-4o-class judge for a comparable number; run _S to show
    accuracy held at ~2% of full-context tokens; tune retrieval-fusion weights on the harness. **Goal:
    clear Hunyuan's 85.2 on the full set.**
- **M2 — Win the hard categories.** Multi-hop planner + bi-temporal conflict tuned. **Goal: LongMemEval_S
  90+, beat Mem0/Zep on multi-hop & temporal; beat full-context on accuracy.**
- **M3 — Scale.** Heat-tiered paging + linear-scaling index. **Goal: LongMemEval_M (plant the SOTA flag,
  no competitor has one) + BEAM-1M/10M without the ~25% cliff.**
- **M4 — Credibility + adoption.** Publish harness + raw logs; MCP server + SDK; corrected-LOCOMO report;
  PersonaMem-v2. **Goal: be the trusted open scoreboard and the open-source #1.**

---

## 8. Coding conventions & working principles

- **Interfaces first, backends behind them.** Every external dependency (LLM, embedder, store) sits behind
  an abstract interface with a zero-dep in-memory/offline fallback. Never import a heavy dep at module top
  level in core — gate it behind the backend that needs it.
- **Measure before optimizing.** Performance claims come from the harness, not intuition. No premature Rust,
  no premature caching.
- **No silent memory corruption.** Contradictions invalidate (with provenance), never overwrite-and-forget.
  Every fact can answer "where did this come from?" and "what did it replace?".
- **Determinism where possible.** The offline fallbacks (hashing embedder, rule-based extractor) are
  deterministic so tests and the demo are reproducible without network.
- **Small, composable modules.** Match the §5 tree. A reviewer should map any file to a box in the §3 diagram.
- **Don't add abstraction for hypothetical futures.** Build for the next milestone, not M4-imagined needs.
- **Every benchmark claim ships with the command to reproduce it.** (Bet D, applied to ourselves.)
- **Comments explain *why*, not *what*.** The bi-temporal invariants and conflict rules are the kind of
  non-obvious thing that deserves a comment; getters do not.

---

## 9. Glossary

- **System-1 / System-2** — fast online write vs. slow async consolidation (dual-process memory).
- **Bi-temporal** — tracking both *valid time* (true in the world) and *transaction time* (when we knew it).
- **As-of query** — "what did we believe was true at time T?" — enabled by bi-temporal stamps.
- **Salience / decay** — importance-weighted retention; unreinforced memories fade (Ebbinghaus), so the
  store stays small and fast.
- **Multi-hop** — a question whose answer requires chaining ≥2 facts (the field's weak spot, our target).
- **Full-context baseline** — stuff the entire history into the prompt. The accuracy ceiling we must beat.
- **The harness** — our in-repo, reproducible evaluation pipeline; the basis for every number we publish.
```
