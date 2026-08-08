# Tasks: Durable Persistence Backend

**Input**: Design documents from `/specs/001-durable-persistence/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: Required by the feature spec success criteria (round-trip, safety, crash recovery, zero-setup, backend parity, migration).

**Organization**: Tasks are grouped by user story so each story can be implemented and verified independently.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare storage modules and dependency metadata without changing default runtime behavior.

- [x] T001 Add `lancedb` optional extra to `pyproject.toml`
- [x] T002 [P] Create persistence error types and schema constants in `engram/store/persist.py`
- [x] T003 [P] Export persistence helpers from `engram/store/__init__.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared JSON-safe dataclass serialization utilities used by US1, US2, and US3.

**CRITICAL**: No user story work can begin until this phase is complete.

- [x] T004 Implement explicit dataclass-to-record serialization for `Episode`, `Fact`, `Entity`, `Relation`, `WorkingMemory`, and `Conflict` in `engram/store/persist.py`
- [x] T005 Implement explicit record-to-dataclass reconstruction with forward-tolerant field handling in `engram/store/persist.py`
- [x] T006 Implement manifest creation, parsing, schema validation, and embedding-dimension validation in `engram/store/persist.py`
- [x] T007 [P] Add shared persistence fixture helpers in `tests/test_persist_roundtrip.py`

**Checkpoint**: Serializer can transform every persisted dataclass without pickle or `eval`.

---

## Phase 3: User Story 1 - Survive a Restart With Zero Data Loss (Priority: P1) MVP

**Goal**: Replace pickle snapshots with a safe JSONL+manifest store that reloads all current memory collections losslessly.

**Independent Test**: Add a fixed corpus, consolidate it, save to a store directory, reopen it, and assert all episodes, facts, graph entities, relations, working memory, conflicts, policy, focus, identity, aliases, and persona cache are preserved.

### Tests for User Story 1

- [x] T008 [P] [US1] Add round-trip equality tests for all persisted collections in `tests/test_persist_roundtrip.py`
- [x] T009 [P] [US1] Add no-code-exec and incompatible-version tests in `tests/test_persist_safety.py`
- [x] T010 [P] [US1] Add torn-tail recovery test in `tests/test_persist_crash.py`
- [x] T011 [P] [US1] Add zero-setup import and quickstart guard tests in `tests/test_zero_setup_default.py`

### Implementation for User Story 1

- [x] T012 [US1] Implement atomic JSONL+manifest save protocol with advisory locking in `engram/store/persist.py`
- [x] T013 [US1] Implement streaming JSONL load protocol with malformed trailing-line recovery in `engram/store/persist.py`
- [x] T014 [US1] Wire `Memory.save()` and `Memory.open()` to JSONL+manifest persistence in `engram/memory.py`
- [x] T015 [US1] Preserve backward-compatible legacy pickle reading only through the explicit migration path, not normal `Memory.open()`, in `engram/memory.py`
- [x] T016 [US1] Update persistence documentation in `specs/001-durable-persistence/quickstart.md`

**Checkpoint**: User Story 1 is fully functional and independently testable with no optional extras.

### Schema-v2 incremental storage follow-up

- [x] Replace new full JSONL snapshots with stdlib SQLite transaction UPSERT/DELETE.
- [x] Keep schema-v1 JSONL readable and migrate it idempotently on first successful open.
- [x] Recover a DB-committed/manifest-lagging generation; reject a manifest ahead of the DB.
- [x] Enforce owner-only paths, `secure_delete`, and remove migrated plaintext JSONL copies.
- [x] Cover incremental writes, rollback recovery, migration interruption, deletion, and permissions in
  `tests/test_persist_sqlite.py`.

---

## Phase 4: User Story 2 - LanceDB as the First Real Vector Backend (Priority: P2)

**Goal**: Add an optional persistent LanceDB vector store behind `VectorStore` while keeping in-memory as the default.

**Independent Test**: Populate in-memory and LanceDB stores with identical facts, restart LanceDB, and assert top-k parity within documented tolerance.

### Tests for User Story 2

- [x] T017 [P] [US2] Add skip-gated LanceDB persistence and restart tests in `tests/test_lancedb_store.py`
- [x] T018 [P] [US2] Add in-memory vs LanceDB top-k parity tests in `tests/test_lancedb_store.py`

### Implementation for User Story 2

- [x] T019 [US2] Implement lazy-import `LanceDBVectorStore` in `engram/store/lancedb_store.py`
- [x] T020 [US2] Add backend selection fields `storage` and `data_path` to `engram/config.py`
- [x] T021 [US2] Wire LanceDB vector store factory selection without importing LanceDB on the default path in `engram/memory.py`
- [x] T022 [US2] Document LanceDB install and selection in `README.md` and `specs/001-durable-persistence/quickstart.md`

**Checkpoint**: User Story 2 works when the optional extra is installed and is invisible to zero-setup users.

---

## Phase 5: User Story 3 - Safe One-Shot Migration Off Pickle (Priority: P3)

**Goal**: Provide an explicit migration tool for existing pickle snapshots with dry-run counts and parity checks.

**Independent Test**: Create a known legacy pickle snapshot, run dry-run migration, run actual migration, then load the new store and assert parity.

### Tests for User Story 3

- [x] T023 [P] [US3] Add dry-run and apply migration tests in `tests/test_migrate_pickle.py`

### Implementation for User Story 3

- [x] T024 [US3] Implement legacy pickle loading and JSONL writing in `engram/store/migrate.py`
- [x] T025 [US3] Add a CLI entry point for migration in `pyproject.toml`
- [x] T026 [US3] Document migration warnings and commands in `README.md` and `specs/001-durable-persistence/quickstart.md`

**Checkpoint**: Existing users can migrate intentionally; normal operation no longer loads pickle.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validate the feature against project invariants and benchmark discipline.

- [x] T027 Run `pytest -q` and `python3 examples/quickstart.py` with no optional services
- [x] T028 [P] Add a durable-backend harness smoke scenario in `eval/`
- [x] T029 [P] Update `RESULTS.md` only if a committed raw log exists for any new persistence performance claim
- [x] T030 Review public copy for honest messaging: no unbenchmarked scale or latency claims in `README.md`, `README.zh-CN.md`, and `docs/index.html`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup; blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational; MVP scope.
- **User Story 2 (Phase 4)**: Depends on Foundational and can reuse US1 persistence metadata.
- **User Story 3 (Phase 5)**: Depends on US1 serializer.
- **Polish (Phase 6)**: Depends on the user stories being implemented for the chosen release slice.

### User Story Dependencies

- **US1 (P1)**: No dependency on US2 or US3; delivers the safe durable format MVP.
- **US2 (P2)**: Can start after Foundational, but final integration should respect US1 manifest validation.
- **US3 (P3)**: Requires US1 save/load helpers to write the new format.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T007 can run in parallel with T004-T006 once test fixture shape is agreed.
- T008-T011 can run in parallel because each targets a separate test file.
- T017 and T018 can run in parallel after `LanceDBVectorStore` shape is known.
- T028-T030 can run in parallel after implementation.

---

## Parallel Example: User Story 1

```bash
Task: "Add round-trip equality tests for all persisted collections in tests/test_persist_roundtrip.py"
Task: "Add no-code-exec and incompatible-version tests in tests/test_persist_safety.py"
Task: "Add torn-tail recovery test in tests/test_persist_crash.py"
Task: "Add zero-setup import and quickstart guard tests in tests/test_zero_setup_default.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Implement US1 save/load behind the existing `Memory.save()` and `Memory.open()` API.
3. Validate with the US1 tests, full offline `pytest -q`, and `python3 examples/quickstart.py`.
4. Stop and review before adding LanceDB or migration.

### Incremental Delivery

1. US1 makes persistence safe and restartable with zero optional dependencies.
2. US2 adds scale-oriented vector persistence as an optional backend.
3. US3 protects existing pickle users through explicit migration.
4. Polish only publishes measured claims backed by raw logs.

### Notes

- `[P]` tasks touch different files or can be completed without depending on unfinished implementation details.
- Every user-story task includes an exact file path for traceability.
- Do not claim persistence latency, scale, or benchmark improvements without a committed harness log.
