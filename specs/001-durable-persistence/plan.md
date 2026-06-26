# Implementation Plan: Durable Persistence Backend

**Branch**: `001-durable-persistence` | **Date**: 2026-06-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-durable-persistence/spec.md`

## Summary

Replace the whole-store **pickle** snapshot (`Memory.save`/`Memory.open` in `engram/memory.py:178-223`)
with a **safe, schema-versioned, crash-resilient on-disk format** (per-namespace directory of JSON Lines
record files + a `manifest.json`), and add **LanceDB** as the first real durable `VectorStore` behind the
existing `engram/store/base.py` interface. The in-memory stores (`engram/store/memory_store.py`) stay the
zero-setup default and the correctness reference; the durable format and LanceDB are optional,
config-selected backends. A one-shot `pickle → JSONL` migration protects existing users.

Two clean layers, both behind code that already exists:
1. **Durable snapshot format** — a serializer in `engram/store/persist.py` that streams each entity type
   to its own append-only `.jsonl` and gates loads on a `manifest.json` (schema version + embedder id +
   dimension + counts). This is what removes pickle (US1).
2. **Durable vector backend** — `LanceDBVectorStore` in `engram/store/lancedb_store.py` implementing the
   `VectorStore` ABC with a lazy `lancedb` import, so vectors persist and scale past RAM (US2).

## Technical Context

**Language/Version**: Python ≥ 3.10; `engram/` core stays pure-stdlib (Constitution III).

**Primary Dependencies**: stdlib only for US1 (`json`, `os`, `tempfile`, `io`; an advisory lock file for
single-writer). `lancedb` (+ its `pyarrow`) is an **optional extra** used only by `lancedb_store.py` (US2).

**Storage**: one directory per namespace — `manifest.json`, `episodes.jsonl`, `facts.jsonl`,
`entities.jsonl`, `relations.jsonl`; LanceDB tables live under the same dir when the backend is enabled.

**Testing**: pytest. US1/US3 tests run fully offline with **no extras**; US2 tests are skip-gated on
`lancedb` being importable.

**Target Platform**: self-hosted Linux/macOS (single process).

**Project Type**: library (`engram` package) + in-repo eval harness.

**Performance Goals**: append is O(1) per record + fsync; restart load is O(n) **streamed** (never a single
whole-blob load); the LanceDB search latency/scale target is **measured on the harness, not asserted here**
(Constitution V). The read-path <100ms target from the charter must not regress.

**Constraints**: zero-setup invariant holds (Constitution II); loads never execute stored code (no
pickle/`eval`); writes are crash-safe (torn tail recoverable); **single writer per namespace** in v1.

**Scale/Scope**: US2 targets a corpus that exceeds comfortable RAM; the exact N and the p50/p95 read
latency are set as a harness task, not claimed in this plan.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| **I. Reproducibility** | This plan asserts **zero** performance numbers; any latency/scale claim is deferred to a harness task with a committed log. | ✅ PASS |
| **II. Zero-setup (NON-NEGOTIABLE)** | In-memory default unchanged; durable format + LanceDB are optional/config-selected; a test asserts the default path pulls **no** heavy import; `quickstart.py` + offline `pytest` pass with no extras. | ✅ PASS |
| **III. Interfaces-first** | `LanceDBVectorStore` implements the `VectorStore` ABC; `lancedb` is imported **lazily inside** `lancedb_store.py`; `engram/` core never imports it at module top level. | ✅ PASS |
| **IV. No silent corruption** | The serializer round-trips **every** bi-temporal stamp, provenance list, and supersedes pointer; invalidated facts persist as invalid (no hard-delete). A round-trip equality test is a release gate. | ✅ PASS |
| **V. Measure-first** | Format is the simplest safe thing (JSONL + manifest); no premature indexing/caching. Perf work is a separate, measured task. | ✅ PASS |
| **VI. Compose, don't pick** | The durable backend composes **behind** existing interfaces; it does not replace or fork the in-memory reference. | ✅ PASS |
| **VII. Honest messaging** | Migration + single-writer limit are documented plainly; no over-claim. | ✅ PASS |

**No violations → Complexity Tracking left empty.**

## Project Structure

### Documentation (this feature)

```text
specs/001-durable-persistence/
├── plan.md              # This file
├── research.md          # Phase 0 — format/backend/crash-safety decisions
├── data-model.md        # Phase 1 — persisted record + manifest schemas
├── quickstart.md        # Phase 1 — runnable validation scenarios
├── contracts/           # Phase 1 — store-backend + on-disk-format contracts
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
engram/store/
├── base.py            # (existing) VectorStore / DocStore / GraphStore ABCs — unchanged
├── memory_store.py    # (existing) in-memory reference impls — unchanged, stays the default
├── persist.py         # NEW: safe JSONL+manifest serialize/deserialize; atomic write, torn-tail recovery
├── lancedb_store.py   # NEW: LanceDBVectorStore (optional, lazy import) implementing VectorStore
└── migrate.py         # NEW: one-shot pickle -> JSONL migration (--dry-run reports counts, then apply)

engram/memory.py       # CHANGED: Memory.save/open delegate to store.persist; pickle path removed
engram/config.py       # CHANGED: storage backend selection (memory | durable | lancedb) + data path
pyproject.toml         # CHANGED: [project.optional-dependencies] lancedb = ["lancedb"]

tests/
├── test_persist_roundtrip.py    # US1 / SC-001 / FR-002: add->save->open equality on ALL fields
├── test_persist_safety.py       # US1 / SC-002 / FR-003,FR-004: no code-exec on load; version-skew errors
├── test_persist_crash.py        # US1 / SC-003 / FR-005: torn-write recovery keeps committed prefix
├── test_zero_setup_default.py   # II / SC-004 / FR-006: default path heavy-import-free; quickstart ok
├── test_lancedb_store.py        # US2 / SC-005 / FR-009: restart persistence + in-memory parity (gated)
└── test_migrate_pickle.py       # US3 / FR-008: dry-run counts + post-migrate entity parity

eval/                  # harness task to measure LanceDB read latency + scale (SC-006) — committed log, no asserted number
```

**Structure Decision**: Single-project library layout. The feature is contained almost entirely within
`engram/store/` (three new modules behind the existing ABCs) plus a focused change to `Memory.save/open`
and `config.py`. No new top-level project, no change to the read/write paths' public API — callers keep
calling `Memory.open(path)` / `mem.save()`; only the on-disk representation and the optional backend change.

## Complexity Tracking

> No constitution violations — section intentionally empty.
