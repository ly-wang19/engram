# Engram Constitution

> Engram is an open-source, self-hostable long-term memory engine for LLM agents.
> This constitution distills the non-negotiables from [`CLAUDE.md`](../../CLAUDE.md) (the project
> charter and source of truth) into spec-checkable principles. Every spec, plan, and task produced
> through Spec-Kit is validated against the principles below. When this file and `CLAUDE.md` appear
> to disagree, `CLAUDE.md` wins and this file must be amended to match.

## Core Principles

### I. Reproducibility Is Non-Negotiable (Bet D)
A number we cannot reproduce does not exist.
- Every published number traces to a committed `results/*.jsonl` log **and** the exact command that
  produced it.
- Always report the **triple**: accuracy **+** total tokens **+** p50/p95 latency. Never accuracy alone.
- The **full-context baseline** appears in every results table. If we do not beat it on accuracy, we
  say so plainly.
- Use a strong, non-gameable judge; document every deviation from a benchmark's official defaults.
- One harness, in-repo (`eval/`): same ingestion, same answer prompt, same judge, declared backbone.
- Numbers stay consistent across README ×2 + RESULTS.md + the landing page. Cross-harness numbers are
  never lifted into our results tables as head-to-head.

### II. The Zero-Setup Invariant (NON-NEGOTIABLE)
`python examples/quickstart.py` and `pytest` MUST run with **no API keys and no third-party services**,
using the hashing embedder + rule-based extractor + in-memory stores.
- Real backends (LanceDB/Qdrant/pgvector, Kuzu/Neo4j, LiteLLM, BGE embeddings/reranker) are **optional
  extras** that slot in behind the same interfaces.
- A change that breaks the zero-setup demo or the offline test suite is rejected, no exceptions.

### III. Interfaces First, Backends Behind Them
Every external dependency (LLM, embedder, vector/graph/doc store, lexical index) sits behind an abstract
interface with a zero-dep, **deterministic**, offline fallback.
- Never import a heavy dependency at module top level in `engram/` core — gate it behind the backend
  that needs it, imported lazily.
- Offline fallbacks (hashing embedder, rule-based extractor) are deterministic so tests and the demo
  reproduce without network.

### IV. No Silent Memory Corruption
Memory is bi-temporal from day one: *valid time* (`valid_at`/`invalid_at`) **and** *transaction time*
(`created_at`/`expired_at`).
- Contradictions **invalidate** with provenance (`invalid_at` + a `supersedes` chain); they never
  overwrite-and-forget. Hard-deleting a contradicted fact is forbidden.
- Every fact can answer "where did this come from?" (provenance → source episode ids) and "what did it
  replace?" (supersedes chain).
- Conflict resolution is cheap-first (slot match → embedding similarity → subsumption); only ambiguous
  cases escalate to an LLM adjudicator. No LLM call per fact.

### V. Measure Before Optimizing
Performance and accuracy claims come from the harness, not intuition.
- No premature Rust, no premature caching, no hand-waved fusion weights — the harness decides the
  bottleneck and whether a change helps.
- An intervention whose net effect is **within run-to-run variance** is not a win; report it as such
  rather than claiming it. (The answerer's variance can exceed a retrieval gain — measure the floor.)

### VI. Compose, Don't Pick
No single pattern wins; the gaps are in the seams between patterns.
- We compose typed memory + a bi-temporal graph + heat-tiered paging + multi-signal retrieval fusion
  (RRF, optional rerank) + salience decay + sleep-time consolidation + a linear-scaling index.
- The read path is **hybrid**: consolidated facts **+** retrieved raw session chunks. Facts-only QA is
  forbidden — it loses recall (a load-bearing M1 finding). Facts add conflict-resolved/bi-temporal
  signal; chunks restore detail.
- Build for the next milestone, not for M4-imagined futures. Don't add abstraction for hypotheticals.

### VII. Honest, Open Public Messaging
What we ship in public must survive a skeptic reading our logs.
- No name-dropping competitors; no unbenchmarked claims ("SOTA", "#1", "1M") in public copy.
- No AI-attribution trailers in commit messages.
- Never commit real personal data, names, or secrets — synthetic placeholders only.
- Dual-licensed: AGPL-3.0 (open) + commercial. Copyleft is deliberate; derivatives stay open.

## Architecture Constraints
- **Dual-process core.** System-1 = hot write path, no LLM on the critical path (<50ms target).
  System-2 = async/sleep-time consolidation (extract facts, build the bi-temporal graph, detect
  conflicts, score salience/decay, summarize). The read path targets <100ms.
- **Language.** Python ≥ 3.10; `engram/` core is pure-stdlib. Dropping to Rust requires harness
  evidence that it is the bottleneck (Principle V).
- **Module shape.** Small, composable modules that match the repository tree in `CLAUDE.md` §5; a
  reviewer must be able to map any file to a box in the §3 architecture diagram.
- **Comments explain *why*, not *what*** — the bi-temporal invariants and conflict rules deserve
  comments; getters do not.

## Development Workflow
- **Spec-driven.** Features flow constitution → `/speckit-specify` → (`/speckit-clarify`) →
  `/speckit-plan` → `/speckit-tasks` → (`/speckit-analyze`) → `/speckit-implement`. Each artifact is
  checked against this constitution; `/speckit-analyze` is the consistency gate before implementation.
- **Every benchmark claim ships with the command to reproduce it** (Bet D, applied to ourselves).
- **Numbers, public messaging, commits, and privacy follow [`CONTRIBUTING.md`](../../CONTRIBUTING.md)**
  — this applies to agents and humans alike.
- A spec that would break Principle II (zero-setup) or Principle IV (provenance/invalidation) is not
  "needs revision" — it is rejected until redesigned.

## Governance
- This constitution and `CLAUDE.md` (the charter) together are the source of truth. This file distills
  the charter's non-negotiables into principles that specs can be checked against; it must never
  contradict the charter. `CLAUDE.md` remains the runtime development guidance.
- **Amendments** require editing both this file and (where affected) `CLAUDE.md`, with a version bump
  and a one-line rationale in the PR description. Semantic versioning:
  - **MAJOR** — a principle is removed or redefined in a backward-incompatible way.
  - **MINOR** — a new principle/section is added or materially expanded.
  - **PATCH** — clarifications and wording that do not change meaning.
- All specs, plans, and PRs verify compliance. Complexity must be justified against Principle VI
  (compose, don't pick) and Principle V (measure first); unjustified complexity is removed.

**Version**: 1.0.0 | **Ratified**: 2026-06-25 | **Last Amended**: 2026-06-25
