# Data Model: Memory Reference Radar

**Feature**: `002-memory-reference-radar`

This data model defines the reviewable records used by the radar. The current representation is
Markdown tables and sections, but the fields below are the contract future tooling or tasks should
preserve.

## Entity: Reference Source

A public repository, product page, paper, benchmark, or technique collection relevant to Engram's
long-term memory strategy.

**Fields**:

- `name`: Stable source name.
- `link`: Canonical public URL.
- `category`: One of direct memory system, temporal graph memory, GraphRAG, knowledge compilation,
  lifelong memory, reflection/experience memory, product surface, benchmark, or radar collection.
- `priority`: P0, P1, P2, or benchmark/radar.
- `architecture_signal`: The strongest observed design pattern.
- `engram_learning`: What Engram may learn at the architecture level.
- `adoption_candidate`: The Engram-native candidate this source informs, or `research-only`.
- `evidence_status`: External-only, reproduced locally, benchmarked in Engram harness, or rejected by
  ablation.
- `clean_room_license_status`: Architecture-only, license pending, license reviewed, or blocked.
- `last_verified`: Date when the link and high-level claim were last checked.

**Validation rules**:

- Every reference source must include a public link.
- Every reference source must explicitly mark evidence status.
- Any source with copyleft, unclear, or missing license information must be marked architecture-only
  until a later plan reviews license implications.
- External benchmark claims may be summarized only as source claims; they cannot be copied into public
  Engram results.

## Entity: Capability Pattern

A reusable architecture idea observed across one or more reference sources.

**Fields**:

- `name`: Short pattern label.
- `source_refs`: One or more reference sources.
- `engram_native_form`: How the pattern maps to Engram primitives.
- `strategic_bets`: One or more Engram bets A-F.
- `expected_benefit`: User-visible or benchmark-visible improvement.
- `risk`: Known risk such as prompt bloat, dependency weight, loss of provenance, or benchmark
  overfitting.

**Validation rules**:

- The pattern must map to Engram primitives instead of naming only an external project.
- The pattern must preserve raw evidence plus consolidated memory unless it is explicitly rejected or
  redesigned.
- The pattern must not require a new mandatory dependency in this feature.

## Entity: Adoption Candidate

A promoted capability pattern that is ready to become a later Spec-Kit feature or task group.

**Fields**:

- `name`: Candidate name.
- `status`: Research-only, planning-ready, planned, implemented, benchmarked, rejected, or archived.
- `source_patterns`: Capability patterns that inspired it.
- `affected_surface`: Write path, consolidation, graph store, read path, profile memory, procedural
  memory, runtime profile, workspace export, or evaluation harness.
- `scope`: The minimum change that proves the candidate.
- `ablation_plan`: How to compare with and without the candidate.
- `benchmark_target`: Benchmark or synthetic slice that tests the intended behavior.
- `metrics`: Accuracy, tokens, and p50/p95 latency direction.
- `rollback_criterion`: Evidence that means the candidate should not ship.
- `messaging_impact`: None, internal-only, or public after Engram-owned reproduction.

**Validation rules**:

- A candidate cannot become planning-ready without a benchmark target and ablation plan.
- A candidate touching facts must preserve provenance, bi-temporal stamps, and non-destructive
  invalidation.
- A candidate adding an external dependency must keep zero-setup behavior unchanged.

## Entity: Evidence Record

The proof status for a claim or candidate.

**Fields**:

- `claim`: The statement being evaluated.
- `source`: External source, Engram run, or local inspection.
- `status`: External-only, reproduced locally, benchmarked in Engram harness, contradicted, or
  insufficient evidence.
- `artifact`: Link or path to proof when available.
- `metrics`: Accuracy, tokens, latency, and error count if benchmarked.
- `notes`: Scope limitations or deviations from benchmark defaults.

**Validation rules**:

- Public performance claims require Engram-owned raw logs.
- Benchmark evidence must include the full-context baseline from the same run when used publicly.
- External-only evidence cannot be used as proof of Engram performance.

## Entity: Messaging Guardrail

A rule that separates internal research from public claims.

**Fields**:

- `rule`: The guardrail text.
- `applies_to`: README, docs, landing page, benchmark report, release notes, or internal planning.
- `reason`: The constitution principle or project risk behind the guardrail.
- `check`: How reviewers verify it.

**Validation rules**:

- Public copy must not name-drop competitors as a benchmark comparison unless Engram reproduced the
  comparison in its harness and the copy includes the full metrics triple.
- Public copy must not say "SOTA", "#1", "world best", or similar without committed Engram evidence.
- Internal documents may name references, but must mark external claims as external-only.

## State Transitions

```text
Reference Source
  discovered -> reviewed -> periodically reverified
              -> archived if stale or irrelevant

Capability Pattern
  observed -> mapped-to-Engram -> candidate-proposed
           -> rejected if it violates invariants or lacks a measurable hypothesis

Adoption Candidate
  research-only -> planning-ready -> planned -> implemented -> benchmarked
                -> rejected/archived if ablation fails or risks outweigh gains

Evidence Record
  external-only -> reproduced locally -> benchmarked in Engram harness
                -> contradicted or insufficient-evidence if checks fail
```
