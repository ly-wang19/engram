# Implementation Plan: Memory Reference Radar

**Branch**: `002-memory-reference-radar` | **Date**: 2026-06-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-memory-reference-radar/spec.md`

## Summary

Create an internal architecture radar that lets Engram study the strongest public memory,
GraphRAG, knowledge-compilation, and agent-learning systems, then promote only the patterns that can
be cleanly translated into Engram-native capabilities with reproducible evaluation. The plan keeps
this work as governance and design first: reference sources are recorded, capability patterns are
mapped to Engram strategic bets, and adoption candidates must pass clean-room, provenance,
zero-setup, and harness-backed ablation gates before implementation.

The first planning-ready candidates are:

1. Chain-aware retrieval for `supersedes` evolution chains.
2. Raw evidence fusion hardening across chunks, facts, and graph paths.
3. Derived memory layers for summaries, mental models, intent/procedure records, and optional
   human-readable workspace views.
4. Graph proximity retrieval for multi-hop expansion.
5. Reflection/experience memory for procedural lessons.
6. Runtime profiles with measured accuracy/tokens/latency tradeoffs.

## Technical Context

**Language/Version**: Markdown documentation and Spec-Kit artifacts; future implementation remains
Python >=3.10 per Engram constitution.

**Primary Dependencies**: None for this feature. External repositories are research inputs only.

**Storage**: Repository-tracked Markdown files under `specs/002-memory-reference-radar/`; no runtime
storage changes.

**Testing**: Documentation validation through checklist review, link checks, placeholder scans, and
future `/speckit-tasks` acceptance checks. Any promoted algorithmic change must later run Engram's
benchmark harness and report accuracy, tokens, and p50/p95 latency.

**Target Platform**: Internal maintainers and contributors working in the Engram repository.

**Project Type**: Documentation/governance feature that feeds later library and harness work.

**Performance Goals**: Not applicable to this documentation feature. Promoted candidates must define
their own benchmark hypotheses before implementation.

**Constraints**: Preserve zero-setup invariant, raw-chunk plus fact retrieval, bi-temporal provenance,
non-destructive invalidation, clean-room implementation boundaries, and honest public messaging.

**Scale/Scope**: Initial radar covers direct memory systems, graph retrieval systems,
knowledge-compilation systems, lifelong memory systems, reflection/experience systems, product
surfaces, and evaluation sources. It does not implement runtime behavior in this feature.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Reproducibility Is Non-Negotiable**: PASS. The radar labels external claims as external-only
  and requires Engram-owned logs before public claims.
- **II. Zero-Setup Invariant**: PASS. This feature adds documentation only; future dependencies are
  explicitly optional until separately specified.
- **III. Interfaces First, Backends Behind Them**: PASS. Adoption candidates must map to Engram
  surfaces before implementation and cannot import third-party code from the radar.
- **IV. No Silent Memory Corruption**: PASS. Candidate promotion requires provenance,
  `supersedes`/invalidation preservation where facts are affected.
- **V. Measure Before Optimizing**: PASS. Every promoted candidate needs an ablation hypothesis and
  accuracy/tokens/latency reporting.
- **VI. Compose, Don't Pick**: PASS. The radar explicitly composes raw chunks, facts, temporal graph,
  hierarchy, reflection, and runtime profiles rather than selecting a single external pattern.
- **VII. Honest, Open Public Messaging**: PASS. Public messaging guardrails are a first-class
  requirement; competitor claims stay internal unless reproduced by Engram.

**Post-design re-check**: PASS. `data-model.md`, `contracts/`, and `quickstart.md` preserve the same
gates and add validation steps rather than implementation shortcuts.

## Project Structure

### Documentation (this feature)

```text
specs/002-memory-reference-radar/
├── plan.md
├── spec.md
├── research.md
├── technical-report.zh-CN.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
└── contracts/
    ├── adoption-candidate.md
    └── radar-entry.md
```

### Source Code (repository root)

```text
AGENTS.md                 # Project charter/context copy used by Codex sessions
CLAUDE.md                 # Project charter/context file configured for Spec-Kit agent-context
.specify/
├── feature.json          # Active feature pointer
└── memory/
    └── constitution.md   # Governance gates used by this plan
specs/
└── 002-memory-reference-radar/
    └── ...               # This feature's planning artifacts
```

**Structure Decision**: This feature is documentation-only. It does not add code paths under
`engram/`, tests under `tests/`, or benchmark logs under `results/`. Later implementation features
will be split out from the adoption candidates and planned separately.

## Complexity Tracking

No constitution violations or extra complexity exceptions are required.
