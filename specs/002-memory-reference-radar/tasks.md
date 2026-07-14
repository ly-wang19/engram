# Tasks: Memory Reference Radar

**Input**: Design documents from `specs/002-memory-reference-radar/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: This is a documentation/governance feature. Test tasks are validation tasks using
[quickstart.md](quickstart.md), link checks, and contract completeness checks rather than runtime unit
tests.

**Organization**: Tasks are grouped by user story to enable independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare the Spec-Kit feature folder and active feature pointer.

- [x] T001 Confirm `.specify/feature.json` points to `specs/002-memory-reference-radar`
- [x] T002 Create `specs/002-memory-reference-radar/plan.md` from the Spec-Kit plan template
- [x] T003 [P] Create `specs/002-memory-reference-radar/contracts/radar-entry.md`
- [x] T004 [P] Create `specs/002-memory-reference-radar/contracts/adoption-candidate.md`
- [x] T005 [P] Create `specs/002-memory-reference-radar/data-model.md`
- [x] T006 [P] Create `specs/002-memory-reference-radar/quickstart.md`
- [x] T007 Update `AGENTS.md` Spec-Kit managed block to point at `specs/002-memory-reference-radar/plan.md`
- [x] T008 Update `CLAUDE.md` Spec-Kit managed block to point at `specs/002-memory-reference-radar/plan.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the governance rules every story relies on.

**CRITICAL**: No user story work should be promoted until this phase is complete.

- [x] T009 Add Phase 0 decisions to `specs/002-memory-reference-radar/research.md` covering internal-use scope, Engram-native candidates, raw evidence plus consolidated memory, clean-room status, and benchmark-prioritized promotion
- [x] T010 Define Reference Source, Capability Pattern, Adoption Candidate, Evidence Record, and Messaging Guardrail entities in `specs/002-memory-reference-radar/data-model.md`
- [x] T011 Define required radar-entry fields and acceptance checks in `specs/002-memory-reference-radar/contracts/radar-entry.md`
- [x] T012 Define required adoption-candidate fields and promotion rules in `specs/002-memory-reference-radar/contracts/adoption-candidate.md`
- [x] T013 Add validation commands for artifact presence, placeholder scans, governance keyword checks, and link checks in `specs/002-memory-reference-radar/quickstart.md`

**Checkpoint**: Foundation ready. The radar can now be populated and candidates can be promoted using consistent gates.

---

## Phase 3: User Story 1 - Maintain a Current Architecture Radar (Priority: P1) MVP

**Goal**: A contributor can open the feature folder and understand the highest-value memory, graph,
knowledge-workspace, learning, and benchmark references without chat context.

**Independent Test**: Read `research.md` and identify at least five high-priority reference sources,
their architecture signals, evidence status, license/clean-room status, and Engram learning within 10
minutes.

### Validation for User Story 1

- [x] T014 [P] [US1] Verify all public reference links in `specs/002-memory-reference-radar/research.md` using the link-check command in `specs/002-memory-reference-radar/quickstart.md`
- [x] T015 [P] [US1] Verify every source in `specs/002-memory-reference-radar/research.md` has evidence status and clean-room/license status matching `specs/002-memory-reference-radar/contracts/radar-entry.md`

### Implementation for User Story 1

- [x] T016 [US1] Add or update the Review Status by Source table in `specs/002-memory-reference-radar/research.md`
- [x] T017 [US1] Add or update the P0 References table in `specs/002-memory-reference-radar/research.md` for direct memory systems
- [x] T018 [US1] Add or update the P1 References table in `specs/002-memory-reference-radar/research.md` for algorithm, graph, and knowledge-compilation sources
- [x] T019 [US1] Add or update the P2 References table in `specs/002-memory-reference-radar/research.md` for product surfaces and deployment patterns
- [x] T020 [US1] Add or update the Benchmark and Radar Sources table in `specs/002-memory-reference-radar/research.md`
- [x] T021 [US1] Ensure `specs/002-memory-reference-radar/spec.md` includes knowledge-compilation systems in the radar scope

**Checkpoint**: User Story 1 is complete when the radar is current, categorized, link-checked, and marked with evidence and license status.

---

## Phase 4: User Story 2 - Convert Reference Patterns Into Engram-Native Capability Candidates (Priority: P2)

**Goal**: A maintainer can use the radar to promote public patterns into Engram-native candidates with
scope, affected surface, strategic bets, benchmark target, and ablation plan.

**Independent Test**: Pick any top-priority pattern and verify it has an adoption-candidate shape that
names the Engram surface, benchmark category, accuracy/tokens/latency hypothesis, clean-room boundary,
and rollback criterion.

### Validation for User Story 2

- [x] T022 [P] [US2] Verify each row in the Capability Patterns table in `specs/002-memory-reference-radar/research.md` maps to at least one strategic bet described in `specs/002-memory-reference-radar/plan.md`
- [x] T023 [P] [US2] Verify every item in the Initial Priority Queue in `specs/002-memory-reference-radar/research.md` can satisfy `specs/002-memory-reference-radar/contracts/adoption-candidate.md`

### Implementation for User Story 2

- [x] T024 [US2] Add or update the Capability Patterns to Absorb table in `specs/002-memory-reference-radar/research.md`
- [x] T025 [US2] Add or update the Staged Assimilation Path in `specs/002-memory-reference-radar/research.md`
- [x] T026 [US2] Add or update the Promotion Checklist for Any Candidate in `specs/002-memory-reference-radar/research.md`
- [x] T027 [US2] Add or update the Initial Priority Queue in `specs/002-memory-reference-radar/research.md` with chain-aware retrieval, raw evidence fusion hardening, derived memory layers, memory workspace diagnostics, graph proximity retrieval, reflection/experience memory, and runtime profiles
- [x] T028 [US2] Add chain-aware retrieval as the first planning-ready candidate example in `specs/002-memory-reference-radar/contracts/adoption-candidate.md`
- [x] T029 [US2] Ensure `specs/002-memory-reference-radar/plan.md` names the first planning-ready candidates and their high-level evaluation gate

**Checkpoint**: User Story 2 is complete when every priority candidate can move into a later Spec-Kit feature without relying on external code or unsupported benchmark claims.

---

## Phase 5: User Story 3 - Keep Public Positioning Honest While Learning Aggressively (Priority: P3)

**Goal**: Contributors can use the radar internally while keeping README, landing-page, and benchmark
copy tied to Engram-owned reproducible evidence.

**Independent Test**: Review a proposed public message and verify it contains no unreproduced
competitor comparisons, hidden borrowed claims, or unbenchmarked "world best" language.

### Validation for User Story 3

- [x] T030 [P] [US3] Verify `specs/002-memory-reference-radar/research.md` labels third-party claims as external-only
- [x] T031 [P] [US3] Verify `specs/002-memory-reference-radar/spec.md` success criteria forbid unreproduced competitor comparisons and unbenchmarked "SOTA", "#1", or "world best" claims

### Implementation for User Story 3

- [x] T032 [US3] Add or update Messaging Guardrail rules in `specs/002-memory-reference-radar/data-model.md`
- [x] T033 [US3] Add public-messaging acceptance checks to `specs/002-memory-reference-radar/contracts/radar-entry.md`
- [x] T034 [US3] Add public-messaging impact requirements to `specs/002-memory-reference-radar/contracts/adoption-candidate.md`
- [x] T035 [US3] Ensure `specs/002-memory-reference-radar/plan.md` records Constitution Check PASS status for reproducibility and honest public messaging

**Checkpoint**: User Story 3 is complete when the radar can be used for internal learning without weakening Engram's reproducibility and messaging discipline.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validate formatting, consistency, and readiness for downstream `/speckit-analyze` or
feature-specific implementation specs.

- [x] T036 [P] Run placeholder scan from `specs/002-memory-reference-radar/quickstart.md` against `specs/002-memory-reference-radar/spec.md`, `specs/002-memory-reference-radar/plan.md`, `specs/002-memory-reference-radar/research.md`, `specs/002-memory-reference-radar/data-model.md`, and `specs/002-memory-reference-radar/contracts/`
- [x] T037 [P] Run governance keyword check from `specs/002-memory-reference-radar/quickstart.md` against `specs/002-memory-reference-radar/`
- [x] T038 [P] Run `git diff --check -- AGENTS.md CLAUDE.md .specify/feature.json specs/002-memory-reference-radar`
- [x] T039 Confirm `AGENTS.md` and `CLAUDE.md` have matching Spec-Kit managed blocks pointing to `specs/002-memory-reference-radar/plan.md`
- [x] T040 Update `specs/002-memory-reference-radar/checklists/requirements.md` notes after final validation
- [x] T041 Review `specs/002-memory-reference-radar/tasks.md` for strict checklist format, sequential IDs, story labels, and exact file paths
- [x] T042 [P] Review `specs/002-memory-reference-radar/technical-report.zh-CN.md` for Chinese-first internal documentation, reproducibility discipline, clean-room boundaries, and alignment with `specs/002-memory-reference-radar/research.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational. This is the MVP.
- **User Story 2 (Phase 4)**: Depends on Foundational and can proceed after US1 has established source categories.
- **User Story 3 (Phase 5)**: Depends on Foundational and can proceed in parallel with US1/US2 after messaging entities exist.
- **Polish (Phase 6)**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1**: Independent after Phase 2. It produces the current radar.
- **US2**: Independent after Phase 2, but benefits from US1's source coverage.
- **US3**: Independent after Phase 2. It validates public-facing discipline.

### Parallel Opportunities

- T003-T006 can run in parallel because they create different files.
- T014 and T015 can run in parallel because they validate different aspects of `research.md`.
- T022 and T023 can run in parallel because they validate different candidate gates.
- T030 and T031 can run in parallel because they inspect different files.
- T036-T038 can run in parallel because they are independent validation commands.

---

## Parallel Example: User Story 1

```text
Task: "Verify all public reference links in specs/002-memory-reference-radar/research.md using the link-check command in specs/002-memory-reference-radar/quickstart.md"
Task: "Verify every source in specs/002-memory-reference-radar/research.md has evidence status and clean-room/license status matching specs/002-memory-reference-radar/contracts/radar-entry.md"
```

---

## Parallel Example: User Story 2

```text
Task: "Verify each row in the Capability Patterns table in specs/002-memory-reference-radar/research.md maps to at least one strategic bet described in specs/002-memory-reference-radar/plan.md"
Task: "Verify every item in the Initial Priority Queue in specs/002-memory-reference-radar/research.md can satisfy specs/002-memory-reference-radar/contracts/adoption-candidate.md"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup.
2. Complete Phase 2 foundational contracts and governance rules.
3. Complete Phase 3 architecture radar.
4. Validate US1 independently with link checks and radar-entry contract checks.

### Incremental Delivery

1. Deliver US1 so contributors have a current, categorized radar.
2. Deliver US2 so radar patterns become planning-ready Engram candidates.
3. Deliver US3 so public messaging remains tied to reproducible Engram evidence.
4. Run Phase 6 validation before moving to `/speckit-analyze` or feature-specific implementation specs.

### Next Implementation Specs

After this feature is validated, create separate implementation specs for:

1. Chain-aware retrieval over `supersedes` evolution chains.
2. Raw evidence fusion hardening in context assembly.
3. Graph proximity retrieval with lightweight n-hop/PPR-inspired expansion.

These are intentionally separate from this radar feature so algorithmic changes can be benchmarked,
ablated, and rolled back independently.
