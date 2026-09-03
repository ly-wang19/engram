# Engram Algorithm Architecture

中文全链路架构报告见
[`docs/engram-full-architecture-report.zh-CN.md`](engram-full-architecture-report.zh-CN.md)。中文架构优化地图见
[`docs/architecture-optimization-map.zh-CN.md`](architecture-optimization-map.zh-CN.md)。前者说明完整数据流、流程、
模块边界和验收入口；后者记录 AI/人类每次算法改动落在架构的哪个模块、优化了什么、验收日志在哪里。

Engram's algorithmic bet is architectural, not a single retriever trick. The system is built so each
memory layer contributes a different kind of evidence, and every claim can be checked in the same eval
harness.

## North Star

The read path should beat full-context by removing distractors while preserving the facts, raw details,
temporal order, and provenance needed to answer. Cost and latency matter, but the first test is accuracy
on hard multi-session, multi-hop, and knowledge-update questions.

## Planes

1. Hot ingest plane
   - Append lossless episodes with event time and transaction time.
   - Resolve identity early enough that first-person facts land on the same subject across sessions.
   - Keep the critical write path deterministic and no-LLM by default.
   - A second entry, `engram/connectors/watch.py`, batch-ingests the agent transcripts already on disk
     (Claude Code, Codex): parse, drop tool noise, redact secrets, store as episodes, then close the
     session so it is distilled. Idempotent by content fingerprint; schedulable (`--install`).

2. Sleep-time consolidation plane
   - Extract atomic facts.
   - Build the bi-temporal graph.
   - Detect cheap conflicts first, then invalidate old facts non-destructively.
   - Score salience and let unreinforced incidental memories decay.
   - At session close, distil the whole session into decision / finding / lesson / open_question facts
     in one LLM call (`engram/consolidate/outcomes.py`). Per-turn extraction yields attributes; a
     session yields conclusions. They are ordinary facts (subject = session id) so they inherit
     supersession, provenance and retrieval, and they stay out of the entity graph.

3. Typed memory plane
   - Episodic memory preserves raw detail.
   - Semantic memory stores fact and graph evidence.
   - Profile and identity memory gives stable user-level state.
   - Working memory stays session-scoped and never silently becomes durable.
   - Session conclusions are the memory unit for working sessions, kept alongside attributes.

4. Evidence retrieval plane
   - Understand the question shape before retrieval: lookup, current-state, temporal, aggregation,
     preference, multi-hop, or abstention-sensitive.
   - Retrieve in parallel from dense vectors, BM25 lexical evidence, graph n-hop evidence, recency, and
     salience.
   - Fuse only positive evidence from semantic, lexical, and graph signals. Recency and salience are
     priors, not proof. A zero lexical or graph score must not receive a fake RRF contribution.
   - Add raw chunks after facts, because facts give structure while chunks recover detail that extraction
     can lose.

5. Evaluation feedback plane
   - Every public number must come from a committed JSONL log.
   - Every table reports accuracy, tokens, and p50/p95 latency together.
   - Every benchmark comparison includes the full-context baseline.
   - Cross-log comparison measures backbone complementarity on shared scored qids before adding routing.

## Read Path Contract

For a query `q`, Engram assembles context in this order:

1. classify evidence need for `q`;
2. expand multi-hop subqueries when needed, including bridge-entity questions such as "my colleague's
   company" or "where does my sister live";
3. retrieve live facts with as-of filtering;
4. apply slot-head filtering for single-valued predicates;
5. fuse positive evidence signals plus priors;
6. page cold facts back only when hot retrieval misses;
7. add graph paths, current-state tables, supersession history, timelines, or preference records according
   to the evidence plan;
8. add summaries and raw chunks under the token budget;
9. keep provenance and dates in the final context;
10. abstain when memory does not contain enough evidence.

## Architectural Invariants

- No hard-delete on contradiction. Superseded facts remain auditable.
- No LLM dependency for zero-setup correctness. LLMs can improve extraction or judging, but the engine
  must still run deterministically offline.
- No fallback that corrupts. A zero-dependency fallback may degrade, never silently produce wrong
  memory: without an LLM, agent transcripts are stored but not rule-extracted; when the hashing
  embedder cannot tokenize what a namespace holds, the audit says so and names the migration.
- One store-level delete, three guards. `clear-slot` is the only hard-delete on the write side; it
  requires the count the owner approved, touches live facts only, and never removes what the owner typed.
- No facts-only QA. The default read path combines consolidated facts with raw session chunks.
- No history amnesia. Previous-value and update questions surface the supersession chain, not just the
  current live slot head.
- No prior-as-evidence leakage. Recency and salience can break ties, but they cannot turn a zero evidence
  hit into a relevant fact.
- No benchmark-only branches. Query planning uses evidence shape, not dataset labels.
- No public claim without raw logs and validation.

## Parameters That Carry Weight

The fusion weights, thresholds and budgets the harness proved load-bearing are tabulated, with the
algorithm each belongs to, in
[`architecture-optimization-map.zh-CN.md` § 算法流程与承重参数](architecture-optimization-map.zh-CN.md).
That table is the source of truth; this document states the contracts.

## Where To Improve Next

- Broader deterministic multi-hop planning for relation chains with multiple bridge entities or nested
  constraints.
- Better temporal interval reasoning over explicit invalid_at spans and duration evidence.
- Learned or harness-tuned fusion weights per evidence shape, with fixed validation splits.
- Backward-compatible richer fact predicates for constraints, goals, and procedural memory.
- Cold-tier indexes that preserve recall when the memory grows beyond a model context window.
