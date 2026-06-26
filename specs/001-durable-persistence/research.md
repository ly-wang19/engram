# Phase 0 — Research & Decisions: Durable Persistence

All decisions honor the constitution: stdlib-only default (II), behind the existing interfaces (III),
preserve every field (IV), and assert **no** performance numbers (V — those are harness tasks).

## D1. On-disk format → JSON Lines (one file per entity type) + `manifest.json`
- **Decision**: serialize each collection to its own append-friendly `.jsonl` (one JSON object per line);
  a `manifest.json` header carries schema version, embedder identity, and committed counts.
- **Rationale**: pure stdlib (`json`) keeps the durable path zero-dep (II); append-friendly → crash-safe
  (FR-005); safe to load — `json.loads` cannot execute code (FR-003); human-inspectable for
  provenance/debugging; streamable record-by-record → no whole-blob load (unlike pickle). The stores
  already hold plain dataclasses, so the mapping is mechanical.
- **Alternatives**: *pickle* (status quo) — unsafe + brittle + rewrites the whole store each save;
  *SQLite* — solid but adds a schema/migration surface and still needs JSON for nested fields (embeddings,
  provenance); revisit when we need secondary indexes, not for the snapshot; *LMDB* — C dependency, breaks
  the stdlib default; *Parquet* — needs pyarrow, columnar is overkill for doc/graph records.

## D2. Crash safety → append + fsync, manifest written last (atomic), torn-tail recovery
- **Decision**: append a record as one line, `flush()` + `os.fsync()`; write `manifest.json` **last** via
  `manifest.json.tmp` + `os.replace()` (atomic). On load, parse the manifest-declared committed prefix for
  each `.jsonl`; missing/malformed records inside that prefix fail loudly, while truncated/garbled records
  after the manifest count are ignored as an uncommitted torn tail → a crash leaves a recoverable
  **last-committed prefix** without silently dropping committed memory (FR-005, SC-003).
- **Rationale**: atomic rename + fsync is the standard durable-without-a-DB recipe; `os.replace` is atomic
  on POSIX and Windows.
- **Alternatives**: *whole-file-then-rename per save* — loses O(1) append, rewrites everything (what pickle
  did); *WAL/journal* — overkill for an append log.

## D3. Safe load + version gate → manifest `schema_version`, explicit reconstruction
- **Decision**: `manifest.json` carries `schema_version` (int) + `engram_version`. If `schema_version`
  exceeds what the reader supports → raise a clear `IncompatibleStoreError` (never a partial/wrong load,
  FR-004). Records become dataclasses via **explicit field mapping**, never `eval`/`__dict__` injection.
- **Rationale**: FR-003 + FR-004 — forward-incompatible stores fail loudly.
- **Alternatives**: unversioned (silent mis-load — rejected); pickle protocol versions (still unsafe).

## D4. First durable vector backend → LanceDB
- **Decision**: `LanceDBVectorStore` (embedded, on-disk, native ANN) as the first real `VectorStore`,
  config-selected, `lancedb` imported lazily inside the module.
- **Rationale**: the charter's stated path; embedded + zero-server fits self-host; memory-mapped → scales
  past RAM (SC-006); Apache-2.0.
- **Alternatives**: *Chroma* — heavier tree, more server-ish (it is Mem0's default in our eval, not ours);
  *sqlite-vec* — promising but newer/less proven; *Qdrant/pgvector* — need a server → later extras behind
  the same ABC.

## D5. Concurrency → single-writer advisory lock (v1)
- **Decision**: an advisory lock file per namespace dir (`.lock` via `fcntl.flock`); a second writer fails
  fast with a clear error. Multi-writer is **out of scope v1**, documented.
- **Rationale**: matches `MemoryService`'s per-namespace LRU ownership (`engram/service.py`); prevents
  interleaved corruption without a DB.
- **Alternatives**: portalocker (extra dep); MVCC (overkill).

## D6. Format stays orthogonal to the vector backend
- **Decision**: fact embeddings travel in `facts.jsonl`, so the durable snapshot is backend-independent. On
  load with the in-memory backend, embeddings repopulate the dict; with LanceDB, load upserts them into the
  LanceDB table. US1 (format) is therefore testable without US2 (LanceDB), as the spec requires.
- **Rationale**: keeps the two user stories independently shippable/testable; the snapshot stays portable
  across vector backends.

**All NEEDS CLARIFICATION resolved — no open unknowns block Phase 1.**
