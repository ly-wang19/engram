# Feature Specification: Memory Reference Radar

**Feature Branch**: `002-memory-reference-radar`

**Created**: 2026-06-29

**Status**: Draft

**Input**: User description: "Record a Spec-Kit document for the global memory-system references Engram should study, judge each at the architecture level, and turn useful patterns into a staged path toward a leading long-term memory infrastructure."

## Why this feature *(context)*

Engram's mission is to become a best-in-class open, reproducible long-term memory engine for LLM
agents. The field is moving quickly: memory layers, temporal graphs, GraphRAG systems, lifelong
memory compression, reflection systems, and coding-agent memory servers are all converging on the same
problem from different angles.

This feature creates an internal, Spec-Kit governed reference radar so maintainers can study the best
public work, convert useful ideas into Engram-native capability candidates, and keep all adoption
decisions tied to reproducible evaluation. It also protects the project from two failure modes:
copying code or claims we cannot own, and scattering architecture ideas across chat history instead of
turning them into testable work.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Maintain a current architecture radar (Priority: P1)

A maintainer wants to understand which memory systems, GraphRAG systems, knowledge-compilation
systems, and agent-learning systems are most relevant to Engram. They open one feature folder and see
a categorized radar with each source, its role, its strongest architecture pattern, what Engram may
learn from it, and what must not be copied or claimed without evidence.

**Why this priority**: Engram needs a disciplined way to stand on public work without drifting into
random feature chasing. This story alone gives the team a shared map before implementation begins.

**Independent Test**: A new contributor can open the radar and identify the top candidate sources for
raw evidence retrieval, temporal graph retrieval, hierarchical consolidation, reflection, and memory
OS-style product surfaces without asking for chat context.

**Acceptance Scenarios**:

1. **Given** a contributor asks "which systems should we study for temporal graph memory?", **When**
   they read the radar, **Then** they can find the relevant systems, links, useful patterns, and
   Engram-specific next questions.
2. **Given** a maintainer adds a new memory project to the radar, **When** they fill the entry, **Then**
   it includes category, link, capability signal, adoption candidate, evidence status, and licensing
   review status.
3. **Given** an external project publishes a benchmark claim, **When** it is recorded in the radar,
   **Then** it is marked as an external claim until reproduced in Engram's harness.

---

### User Story 2 - Convert reference patterns into Engram-native capability candidates (Priority: P2)

A maintainer wants to decide what to build next. They use the radar to map public patterns into
Engram's own architecture: raw episodes, atomic facts, bi-temporal graph, profile memory, procedural
memory, hybrid retrieval, multi-hop planning, salience decay, and reproducible evaluation. Each
candidate states the expected benefit and the benchmark category it should improve.

**Why this priority**: The reference radar only matters if it produces focused, measurable Engram work.
This story keeps architecture thinking connected to the roadmap.

**Independent Test**: Pick any top-priority pattern and verify it has a proposed Engram capability, a
bounded scope, a target benchmark behavior, and a measurable success criterion before any code is
started.

**Acceptance Scenarios**:

1. **Given** a source suggests a useful pattern, **When** it is promoted to an adoption candidate,
   **Then** the candidate names the Engram memory type or read/write-path behavior it would affect.
2. **Given** a proposed candidate cannot name a benchmark category or user-visible behavior it should
   improve, **When** it is reviewed, **Then** it remains research-only and is not moved into planning.
3. **Given** two references solve the same problem differently, **When** the radar is reviewed, **Then**
   their tradeoffs are captured as alternatives rather than merged into one vague requirement.

---

### User Story 3 - Keep public positioning honest while learning aggressively (Priority: P3)

A contributor preparing public copy or benchmark notes wants to be ambitious without making
unsupported claims. They can use the radar to learn internally while keeping public messaging focused
on Engram's reproducible results, not competitor comparisons or borrowed narratives.

**Why this priority**: The project can learn from the field, but Engram's credibility comes from open
evaluation and clean ownership of its implementation.

**Independent Test**: Review a proposed README or landing-page change against the radar guardrails and
verify it does not name-drop competitors, repeat unreproduced external numbers, or claim "SOTA" without
a committed Engram results log.

**Acceptance Scenarios**:

1. **Given** a public-facing document mentions Engram's position, **When** it is reviewed, **Then** any
   performance claim links to Engram-owned raw logs and includes accuracy, tokens, and latency.
2. **Given** a contributor wants to adapt an external idea, **When** it enters the radar, **Then** the
   entry records clean-room constraints and license-review status before implementation work begins.
3. **Given** a source's claim cannot be reproduced, **When** the team discusses it publicly, **Then** it
   is not presented as Engram evidence.

### Edge Cases

- **A project changes direction or disappears**: keep the entry, mark its last verified date, and avoid
  relying on stale claims.
- **A source is a website or paper rather than a repository**: record it as research input and require
  extra evidence before implementation work.
- **A source has a restrictive or unclear license**: keep architecture notes, but block code reuse and
  require a clean-room implementation plan before planning.
- **A pattern improves one benchmark while harming another**: mark it as benchmark-specific until an
  ablation shows the net effect.
- **A source overlaps strongly with Engram's existing design**: record confirmation value, but avoid
  duplicating work unless the radar identifies a measurable gap.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The feature MUST maintain a categorized reference radar covering direct memory systems,
  graph retrieval systems, knowledge-compilation systems, lifelong memory systems,
  reflection/experience systems, and evaluation sources.
- **FR-002**: Each reference source MUST include a link, category, architecture signal, potential
  Engram learning, evidence status, and clean-room/licensing status.
- **FR-003**: The radar MUST distinguish external claims from Engram-owned results; external claims MUST
  NOT be treated as benchmark evidence until reproduced in Engram's harness.
- **FR-004**: Each adoption candidate MUST map to at least one Engram strategic bet: accuracy over
  full-context noise, multi-hop reasoning, bi-temporal conflict handling, reproducible harness,
  scaling/salience, or async consolidation.
- **FR-005**: Each adoption candidate MUST name the Engram capability surface it would affect, such as
  write path, consolidation, graph store, read path, profile memory, procedural memory, runtime
  profile, or evaluation harness.
- **FR-006**: Each adoption candidate MUST include a measurable hypothesis before it can move to
  planning, including expected benchmark category, accuracy/tokens/latency direction, and an ablation
  check.
- **FR-007**: The radar MUST preserve Engram's zero-setup invariant by marking any external dependency
  as optional unless a future spec explicitly justifies otherwise.
- **FR-008**: The radar MUST preserve Engram's "facts plus raw chunks" read-path principle; candidates
  that produce facts-only QA MUST be rejected or redesigned.
- **FR-009**: The radar MUST include public-messaging guardrails so internal research does not become
  unsupported competitor comparison, hidden code borrowing, or unbenchmarked "world best" claims.
- **FR-010**: The radar MUST define a staged assimilation path that prioritizes architecture-level
  leverage before implementation details.

### Key Entities

- **Reference Source**: A public repository, product page, paper, benchmark, or technique collection
  relevant to long-term memory systems.
- **Capability Pattern**: A reusable architecture idea observed in one or more sources, such as
  evolution chains, raw evidence retrieval, graph expansion, recursive abstraction, reflection, or
  runtime profiles.
- **Adoption Candidate**: An Engram-native proposal derived from a capability pattern, with scope,
  target memory surface, and evaluation hypothesis.
- **Evidence Record**: The status of proof for a claim: external-only, reproduced locally, benchmarked
  in Engram harness, or rejected by ablation.
- **Messaging Guardrail**: A rule that separates internal learning from public positioning and keeps
  Engram's claims tied to committed raw results.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new contributor can identify at least five high-priority reference sources and their
  Engram-relevant lessons within 10 minutes of opening the feature folder.
- **SC-002**: 100% of adoption candidates include source, Engram-specific rationale, clean-room/license
  note, affected capability surface, and measurable evaluation hypothesis.
- **SC-003**: 100% of benchmark or performance claims in the radar are labeled as external-only or
  Engram-reproduced; no unlabeled numbers appear.
- **SC-004**: Every promoted candidate has an explicit ablation plan that reports accuracy, tokens, and
  latency together before it can affect public messaging.
- **SC-005**: Public-facing copy produced from this work contains zero unreproduced competitor
  comparisons and zero unbenchmarked "SOTA", "#1", "world best", or scaling claims.
- **SC-006**: The staged assimilation path produces at least one planning-ready candidate for each of
  raw evidence retrieval, evolution-chain retrieval, hierarchical consolidation, graph multi-hop
  retrieval, and reflection/experience memory.

## Assumptions

- This is an internal architecture and planning feature, not public marketing copy.
- Referenced systems are studied from public pages, papers, repositories, and benchmark artifacts only.
- Implementation work happens in later Spec-Kit phases and must use Engram-native interfaces, tests,
  and clean-room code.
- External benchmark numbers are useful for triage but never count as Engram results until reproduced
  with committed raw logs.
- The initial radar is intentionally architecture-level; detailed code review of each project happens
  only after a candidate is promoted to planning.
