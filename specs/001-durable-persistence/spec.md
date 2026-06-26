# Feature Specification: Durable Persistence Backend

**Feature Branch**: `001-durable-persistence`

**Created**: 2026-06-25

**Status**: Draft

**Input**: User description: "Durable persistence backend: add LanceDB as the first real vector store behind the VectorStore interface and replace fragile pickle snapshots with a safe, append-only, schema-versioned on-disk format; in-memory stays the zero-setup default"

## Why this feature *(context)*

Engram today persists state with pickle snapshots. That is the next load-bearing wall to remove:
pickle is **unsafe to load** (arbitrary code execution on untrusted files), **brittle** (any dataclass
change can break old snapshots), **not crash-safe** (a whole-store rewrite can be interrupted), and it
**does not scale** past what fits in one in-memory blob. This feature gives Engram a durable, safe,
embedded persistence layer while keeping the zero-setup in-memory path as the default — directly serving
Constitution Principle II (zero-setup), III (interfaces-first), and IV (no silent memory corruption).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Survive a restart with zero data loss (Priority: P1)

An operator self-hosting Engram adds memories over a session, stops the process, and starts it again.
Every episode, fact, entity, and relation comes back intact — including bi-temporal stamps, provenance,
and supersedes chains. Nothing was silently dropped or mutated.

**Why this priority**: Durability across restarts is the entire point of a memory engine. Without it the
other stories have nothing to stand on. This story alone is a viable MVP (a safe, durable store replacing
pickle), independent of which vector backend is used.

**Independent Test**: Add a fixed corpus, snapshot in-memory state, restart the store from disk, and
assert the reloaded state is byte-for-byte equal on all entities and their temporal/provenance fields.

**Acceptance Scenarios**:

1. **Given** a store with N episodes/facts/relations, **When** the process restarts and reloads from
   disk, **Then** all N are recovered with identical `valid_at`/`invalid_at`/`created_at`/`expired_at`,
   provenance, and supersedes pointers.
2. **Given** an invalidated (superseded) fact, **When** the store reloads, **Then** the fact is still
   present and still marked invalid with its supersedes chain intact (no hard-delete on reload).
3. **Given** a store directory that does not exist yet, **When** the store opens it, **Then** it starts
   empty without error.

---

### User Story 2 - LanceDB as the first real vector backend (Priority: P2)

A user installs the `lancedb` extra and selects it in config. Embeddings persist to an embedded columnar
store on disk; semantic search returns the same top-k as the in-memory store (within documented backend
tolerance) and survives restart, scaling past available RAM. With no extra installed, nothing changes —
in-memory is still the default.

**Why this priority**: This is the charter's stated next backend (embedded, zero-server) and unlocks
scale (M3), but it builds on the durable-store contract from US1. It is independently testable behind the
existing VectorStore interface.

**Independent Test**: Run the same query set against the in-memory store and the LanceDB-backed store on
identical data; assert top-k overlap meets the agreed threshold, then restart and assert results persist.

**Acceptance Scenarios**:

1. **Given** the `lancedb` extra is installed and selected, **When** facts are added and the process
   restarts, **Then** vector search returns the same results as before the restart.
2. **Given** no optional extra is installed, **When** `quickstart.py` and `pytest` run, **Then** they
   pass using the in-memory default (zero-setup invariant holds).
3. **Given** a stored embedding dimension that differs from the configured embedder, **When** the store
   opens, **Then** it fails with a clear dimension-mismatch error rather than returning wrong neighbors.

---

### User Story 3 - Safe one-shot migration off pickle (Priority: P3)

An operator with an existing pickle snapshot upgrades. They run a one-shot migration that reads the old
snapshot, reports counts in a dry-run, then writes the new safe format. Normal operation never loads the
old pickle again.

**Why this priority**: Protects existing users' data, but only matters once US1's format exists. Lowest
priority because new installs don't need it.

**Independent Test**: Take a known pickle snapshot, run migration in dry-run (asserts reported counts),
run it for real, then load via the new format and assert entity parity with the original.

**Acceptance Scenarios**:

1. **Given** a valid pickle snapshot, **When** migration runs in dry-run, **Then** it reports per-entity
   counts and writes nothing.
2. **Given** the same snapshot, **When** migration runs for real, **Then** the new-format store contains
   every entity with all temporal/provenance fields preserved.

---

### Edge Cases

- **Torn write / crash mid-append**: recovery skips the incomplete trailing record and loads the
  committed prefix; previously-committed data is never corrupted.
- **Schema/version skew**: a store written by an incompatible (e.g. newer) Engram version fails to load
  with an explicit, actionable error — never a silent partial or wrong load.
- **Untrusted store file**: opening a store never executes code embedded in the file.
- **Concurrent writers** to one store path: v1 defines single-writer behavior (lock or documented
  constraint); it must not silently interleave into corruption.
- **Embedding model change** between writes: detected via the manifest's recorded model id/dimension.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST persist all core entities (Episode, Fact, Entity, Relation) durably such that a
  process restart recovers them with no loss.
- **FR-002**: Persistence MUST preserve every bi-temporal stamp, provenance (source episode ids), and
  supersedes chain exactly (Constitution IV).
- **FR-003**: The on-disk format MUST be safe to load — loading MUST NOT execute arbitrary code (no
  pickle/`eval` of stored data).
- **FR-004**: The format MUST be schema-versioned via a store manifest; loading an incompatible version
  MUST fail with a clear error, never a silent mis-load.
- **FR-005**: Writes MUST be crash-resilient — an interrupted write MUST NOT corrupt committed data; on
  recovery the store loads to the last fully-committed record.
- **FR-006**: In-memory stores MUST remain the zero-setup default; the durable backend and LanceDB MUST
  be optional, config-selected extras (Constitution II). `quickstart.py` and `pytest` MUST pass with no
  extras installed.
- **FR-007**: All persistence backends MUST sit behind the existing VectorStore / GraphStore / DocStore
  interfaces; `engram/` core MUST NOT import a heavy persistence dependency at module top level
  (Constitution III).
- **FR-008**: System MUST provide a one-shot pickle→new-format migration with a dry-run that reports
  per-entity counts before writing.
- **FR-009**: Top-k search over a persisted-then-reloaded store MUST match the in-memory store's results
  for the same data and query, modulo a documented backend tolerance.
- **FR-010**: The durable backend MUST be runnable under the harness so any latency/throughput/scale
  claim is measured, not asserted (Constitution V).

### Key Entities *(persisted forms — attributes, not implementation)*

- **Episode**: raw lossless turn/event; `event_time` + `ingested_at`; the append-only source of truth.
- **Fact**: atomic (subject, predicate, object) claim; text, embedding, salience, confidence,
  provenance, the two time axes, and supersedes pointer.
- **Entity / Relation**: graph nodes/edges carrying the same bi-temporal stamps.
- **Store Manifest**: schema version, embedder id + embedding dimension, store created/updated
  transaction times, and per-entity record counts — the header that makes loads safe and versioned.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After add → restart, **100%** of episodes/facts/relations and their bi-temporal +
  provenance fields are recovered (round-trip equality test).
- **SC-002**: Opening any store file **never** executes code from the file (verified by a crafted-file
  test that would trip on code execution).
- **SC-003**: A simulated crash mid-write loses **at most the single in-flight record** and never the
  committed prefix (torn-write test).
- **SC-004**: The zero-setup invariant holds — the full offline test suite and `quickstart.py` pass with
  **no** optional extras installed.
- **SC-005**: A reloaded store returns **identical top-k** search results to the equivalent in-memory
  store on a fixed query set (within the documented tolerance for the chosen vector backend).
- **SC-006**: The durable backend serves a target corpus **without loading the whole store into RAM**;
  the exact corpus size and the p50/p95 read latency target are set in the plan and measured on the
  harness.

## Assumptions

- **Single-writer per store path** for v1; multi-process concurrent writers are out of scope and
  documented as such.
- **LanceDB is the first real vector backend** (embedded, zero-server) per the charter; Qdrant/pgvector
  remain later extras behind the same interface.
- **The in-memory stores remain the correctness reference** — the durable backend must match their
  observable behavior.
- **A given store is written and read with the same embedder** (model id + dimension recorded in the
  manifest); changing embedder requires re-embedding, out of scope here.
- **Scope is the vector + doc stores plus a safe durable form for the graph**; a native embedded graph
  backend (e.g. Kuzu) is a separate later spec, not this one.
