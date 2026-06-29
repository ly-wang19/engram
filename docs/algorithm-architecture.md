# Engram Algorithm Architecture

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

2. Sleep-time consolidation plane
   - Extract atomic facts.
   - Build the bi-temporal graph.
   - Detect cheap conflicts first, then invalidate old facts non-destructively.
   - Score salience and let unreinforced incidental memories decay.

3. Typed memory plane
   - Episodic memory preserves raw detail.
   - Semantic memory stores fact and graph evidence.
   - Profile and identity memory gives stable user-level state.
   - Working memory stays session-scoped and never silently becomes durable.

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
- No facts-only QA. The default read path combines consolidated facts with raw session chunks.
- No history amnesia. Previous-value and update questions surface the supersession chain, not just the
  current live slot head.
- No prior-as-evidence leakage. Recency and salience can break ties, but they cannot turn a zero evidence
  hit into a relevant fact.
- No benchmark-only branches. Query planning uses evidence shape, not dataset labels.
- No public claim without raw logs and validation.

## Where To Improve Next

- Broader deterministic multi-hop planning for relation chains with multiple bridge entities or nested
  constraints.
- Better temporal interval reasoning over explicit invalid_at spans and duration evidence.
- Learned or harness-tuned fusion weights per evidence shape, with fixed validation splits.
- Backward-compatible richer fact predicates for constraints, goals, and procedural memory.
- Cold-tier indexes that preserve recall when the memory grows beyond a model context window.
