# Engram — an open-source long-term memory engine for LLM agents

> **Project codename:** Engram (an *engram* is the physical trace a memory leaves in the brain).
> The name is provisional — rename freely. The package is `engram`, the repo is "Super-Memory".

This file is the single source of truth for *what we are building and why*. Read it fully before
writing code. It is written for any agent or human contributor walking in cold.

---

## 0. The mission (non-negotiable)

**Build a best-in-class long-term memory system for LLM agents, fully open source, with strong results
that anyone can reproduce.**

- **Open and self-hostable.** Dual-licensed — AGPL-3.0 (open source) + a commercial license for
  proprietary use; reproducible, self-hostable, no lock-in.
- **The win condition is not a single number.** It is: *high accuracy on a neutral, reproducible harness,
  at a fraction of the tokens and latency of the full-context baseline, with results anyone can replicate.*

We do not ship marketing numbers. We ship a harness that anyone can run to reproduce our numbers.
That discipline is itself the point (see §4).

---

## 1. The opportunity

Long-term memory is the missing layer for LLM agents: today they forget across sessions, and the common
workaround — replaying the entire history into the prompt — is expensive, slow, and *less* accurate as
the history grows (distractors pile up). Two gaps are wide open:

1. **Accuracy.** Most memory systems win on cost/latency but still lose to the full-context baseline on
   accuracy. Beating full-context *on accuracy* — a precisely retrieved slice that outperforms the noisy
   full window — is the real prize. → Bet A.
2. **Reproducibility.** Memory benchmarks are reported on inconsistent, non-reproducible harnesses; the
   same system shows wildly different numbers across sources. A single neutral, in-repo harness with the
   official judge and published raw logs is itself a differentiator. → Bet D.

The hardest categories across the field are **multi-hop and multi-session reasoning** and **temporal /
knowledge-update** handling — exactly where a bi-temporal graph plus a multi-hop planner should win. → Bets B, C.

---

## 2. Strategic bets (how we actually win)

These are the differentiators. Every design decision must serve at least one.

- **Bet A — Beat full-context on accuracy, not just cost.** Retrieval must be precise enough that the
  *filtered* context outperforms the *noisy full* window (removing distractors raises accuracy). We
  verify this on LongMemEval/BEAM, reporting accuracy **and** tokens **and** latency together.
- **Bet B — Win the hard categories with a multi-hop query planner.** Decompose multi-hop / multi-session
  questions into sub-queries, walk the graph, aggregate. This is the field's soft spot; we make it our
  strength.
- **Bet C — Bi-temporal + cheap conflict detection as core primitives.** A bi-temporal model
  (`valid_at`/`invalid_at` + transaction time) that detects contradictions *without an LLM call per fact*
  (exact-slot model + embedding-similarity + content-subsumption heuristics; optional LLM adjudication
  only when ambiguous). Non-destructive invalidation + full provenance/audit trail. This nails
  knowledge-updates and temporal categories.
- **Bet D — The reproducible-harness moat.** One fixed, open, neutral eval pipeline across all benchmarks;
  a strong (non-gameable) judge; report tokens+latency+accuracy together; publish it and invite
  replication. In a field where every number is contested, *being the trustworthy scoreboard* is power.
- **Bet E — Linear scaling + salience decay so we hold up at 10M+ tokens** where naive approaches degrade.
- **Bet F — Async/sleep-time consolidation** keeps the read path sub-100ms while graph-building, dedup,
  and conflict resolution happen off the critical path.

**Principle: compose, don't pick.** No single pattern wins. We compose typed memory + a bi-temporal
graph + heat-tiered paging + multi-signal retrieval fusion + salience decay + sleep-time consolidation +
a linear-scaling index. The gaps are in the *seams* between these — that's where we build.

---

## 3. Architecture

Dual-process, modeled on the human-inspired System-1 / System-2 split, with a bi-temporal typed-memory
core and a multi-hop read path.

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
                      │  3. fusion: Reciprocal Rank Fusion + optional rerank          │
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
1. **Slot match** (exact) → likely update; **embedding similarity** (same attribute under different free-form
   predicate) and **content subsumption** (one claim ⊂ the other) → contradiction vs. elaboration.
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

## 4. Evaluation discipline (Bet D — this is how we earn every claim)

**A number we cannot reproduce does not exist.** Rules:

1. **One harness, in-repo** (`eval/`). Same ingestion, same answer-prompt, same judge, declared backbone.
2. **Report the triple:** accuracy **+** total tokens **+** p50/p95 latency. Never accuracy alone.
3. **Always include the full-context baseline** in every results table. If we don't beat it on accuracy,
   we say so.
4. **Use a strong (non-gameable) judge**; document every deviation from a benchmark's defaults.
5. **Run multiple backbones** (a small open model + a frontier model). Memory quality must not depend on
   one model's ability to read our graph.
6. **Publish the harness and the raw run logs.** Invite replication. This is the moat.

Target benchmarks, in order: LongMemEval_S → LongMemEval_M → LOCOMO → PersonaMem-v2 → BEAM.

---

## 5. Repository structure

```
.
├── CLAUDE.md                  # this charter (source of truth)
├── README.md                  # short public-facing intro
├── LICENSE                    # GNU AGPL-3.0 (the open-source arm of the dual license)
├── COMMERCIAL-LICENSE.md      # commercial license for proprietary/closed-source/SaaS use
├── pyproject.toml             # packaging; core has ZERO hard deps, backends are extras
├── engram/                    # the library
│   ├── __init__.py            # public API surface
│   ├── config.py              # settings + pluggable backend selection
│   ├── types.py               # bi-temporal dataclasses (Episode, Fact, Entity, Relation, ...)
│   ├── memory.py              # Memory facade: add() / search() / consolidate() / as_of()
│   ├── service.py             # MemoryService: multi-tenant core shared by every surface
│   ├── embed/                 # Embedder interface + zero-dep hashing fallback
│   ├── llm/                   # LLM interface + rule-based offline fallback
│   ├── store/                 # VectorStore / GraphStore / DocStore interfaces + in-memory impls
│   ├── ingest/                # System-1 fast write path
│   ├── consolidate/           # System-2: extractor (rule + LLM), graph_builder, conflict + detect, decay, summarizer, classify
│   ├── retrieve/              # hybrid, fusion (RRF), lexical (BM25), planner (multi-hop) + agentic, temporal (as-of), rerank (OPTIONAL, off by default)
│   │                          #   (the abstention gate is inline in memory.py's read path, not a separate module)
│   ├── server/                # FastAPI HTTP API + management console + OpenAI-compatible proxy
│   ├── mcp/                   # MCP server (give any agent a persistent memory)
│   └── connectors/            # batch import (ChatGPT export / OpenAI messages / JSONL / transcript)
├── clients/typescript/        # JS/TS SDK
├── eval/                      # the reproducible harness (Bet D) + tiny built-in synthetic set
├── examples/                  # quickstart.py — end-to-end runnable demo
└── tests/                     # smoke + unit tests
```

**Zero-setup invariant:** `python examples/quickstart.py`, installed `engram-quickstart`, and `pytest`
MUST run with **no API keys and no third-party services**, using the hashing embedder + rule-based
extractor + in-memory stores. Real
backends (LanceDB/Qdrant/pgvector, Kuzu/Neo4j, LiteLLM for any LLM, BGE embeddings/reranker) are optional
extras that slot in behind the same interfaces. We never break the zero-setup demo.

---

## 6. Tech stack & backends

- **Language:** Python ≥ 3.10. Core is pure-stdlib. Hot path (retrieval/index) may later drop to Rust if
  the harness shows it's the bottleneck — measure first.
- **Embeddings:** interface + hashing fallback (offline); production via BGE-m3 / OpenAI / local.
- **LLM:** interface + rule-based fallback (offline); production via LiteLLM (model-agnostic, so we report
  on multiple backbones).
- **Vector store:** in-memory (default) → LanceDB (embedded, zero-server) → Qdrant / pgvector.
- **Graph store:** in-memory (default) → Kuzu (embedded) → Neo4j / FalkorDB.
- **Lexical:** in-memory BM25 → tantivy / bm25s.
- **Serving:** FastAPI server, Python SDK, JS/TS SDK, OpenAI-memory-compatible API, and an **MCP server**.
- **License:** **dual-licensed** — GNU **AGPL-3.0** for open-source use, plus a separate **commercial
  license** for proprietary/closed-source or SaaS use that can't meet AGPL's source-disclosure (§13)
  terms. Copyleft (deliberately *not* permissive): derivatives stay open, and commercial use without AGPL
  compliance requires authorization. Commercial terms live in `COMMERCIAL-LICENSE.md`.

---

## 7. Roadmap (milestones tied to numbers, not vibes)

- **M0 — Skeleton + runnable loop.** Zero-dep end-to-end `add → consolidate → search` with bi-temporal
  facts, conflict/invalidation, multi-hop planner stub, as-of query, and a tiny in-repo eval that produces
  a score. *Proves the architecture runs.* **Done.**
- **M1 — Real backends + real eval.** Real local embeddings (BGE via sentence-transformers, no key), LLM
  extractor + judge, and a real LongMemEval runner (`eval/bench.py`) with a full-context baseline on the
  same items.
  - **Headline (LongMemEval_S, full 500, official judge): `engram_lean` 83.6% vs full-context 73.2%
    (+10.4) at ~8× fewer tokens (9.6k vs 79k), 0 errors.** The headline system answers from a small
    *retrieved* slice — the honest test of the thesis.
  - **Validated finding (load-bearing):** pure fact-extraction *loses recall*; the read path MUST be
    **hybrid = consolidated facts + retrieved raw session chunks**. Facts add conflict-resolved/bi-temporal
    signal; chunks restore detail. Never ship facts-only QA.
  - **Remaining:** tune retrieval-fusion weights on the harness; push the hardest categories (multi-session,
    temporal). **Goal: clear 85%+ on the full set.**
- **M2 — Win the hard categories.** Multi-hop planner + bi-temporal conflict tuned. **Goal: LongMemEval_S
  90+, strong multi-hop & temporal; beat full-context on accuracy.**
- **M3 — Scale.** Heat-tiered paging + linear-scaling index. **Goal: strong results at 1M+ tokens
  (LongMemEval_M, BEAM-1M/10M) without the scaling cliff.**
- **M4 — Credibility + adoption.** Publish harness + raw logs; MCP server + SDK; broaden benchmark
  coverage. **Goal: be a trusted open scoreboard for long-term memory.**

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
- **Documentation language preference.** For project-internal documents generated by agents, prefer
  Chinese when the user has not requested another language; keep code identifiers, API names,
  commands, filenames, benchmark names, and public English-facing artifacts in their appropriate
  original language or bilingual form.
- **Architecture change visibility.** Agent-generated algorithm or architecture changes must update
  [`docs/architecture-optimization-map.zh-CN.md`](docs/architecture-optimization-map.zh-CN.md) so the
  owner can see which memory layer changed, why it changed, and which result logs prove it.
- **Numbers, public messaging, commits, and privacy follow [`CONTRIBUTING.md`](CONTRIBUTING.md)** (applies
  to agents too): every published number traces to a committed `results/*.jsonl` log and stays consistent
  across README ×2 + RESULTS.md + the landing page; don't name-drop competitors or make unbenchmarked
  claims ("1M", "SOTA", "#1") in public copy; no AI-attribution trailers in commit messages; never commit
  real personal data, names, or secrets (use synthetic placeholders).

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

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/002-memory-reference-radar/plan.md
<!-- SPECKIT END -->
