# Contract: Adoption Candidate

This contract defines when a reference pattern is ready to move from research into a later Spec-Kit
feature, task group, or benchmark experiment.

## Required Fields

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Candidate name |
| `source_patterns` | Yes | Patterns and sources that inspired it |
| `engram_native_form` | Yes | The Engram primitive or surface it changes |
| `strategic_bets` | Yes | Bets A-F served by the candidate |
| `affected_surface` | Yes | Write path, consolidation, graph store, read path, profile memory, procedural memory, workspace export, runtime profile, or harness |
| `minimum_scope` | Yes | Smallest useful version to evaluate |
| `benchmark_target` | Yes | Benchmark, category, or synthetic slice that tests the behavior |
| `metrics` | Yes | Expected direction for accuracy, tokens, and p50/p95 latency |
| `ablation_plan` | Yes | How to compare with and without the candidate |
| `rollback_criterion` | Yes | Evidence that means the candidate should not ship |
| `clean_room_notes` | Yes | License and implementation boundary |
| `messaging_impact` | Yes | None, internal-only, or public after Engram-owned reproduction |

## Promotion Rules

- `benchmark_target`, `metrics`, and `ablation_plan` must be concrete before `/speckit-tasks`.
- Candidates touching facts must preserve provenance, bi-temporal stamps, and `supersedes` chains.
- Candidates touching the read path must keep raw evidence plus consolidated memory unless an
  Engram-owned ablation proves another path is better.
- Candidates requiring optional services, models, or stores must preserve the zero-setup quickstart and
  tests.
- Public messaging impact remains internal-only until raw Engram logs prove the result.

## Example

```text
name: Chain-aware retrieval
source_patterns: Hy-Memory evolution chains; Graphiti temporal updates; Engram supersedes model
engram_native_form: Retrieve current fact plus bounded supersedes chain with provenance
strategic_bets: Bet B, Bet C, Bet D
affected_surface: read path, temporal filtering, context assembly
minimum_scope: Expand retrieved facts to include one-hop superseded/superseding facts under a token budget
benchmark_target: Knowledge-update and temporal slices from LongMemEval/LOCOMO plus synthetic update tests
metrics: Accuracy up, tokens neutral/slightly up, p95 latency within read-path target
ablation_plan: Compare baseline retrieval vs chain-aware retrieval on the same items and answerer
rollback_criterion: No accuracy gain outside variance, prompt bloat hurts latency/tokens, or provenance ambiguity
clean_room_notes: Implement from Engram's existing fact model; do not copy external code
messaging_impact: Internal-only until benchmarked in Engram harness
```
