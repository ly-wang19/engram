# Contract — On-Disk Format (v1, `schema_version = 1`)

## Directory layout (one per namespace)
```
<data_path>/<namespace>/
├── manifest.json      # written LAST, atomically (tmp + os.replace) — the commit point
├── episodes.jsonl
├── facts.jsonl
├── entities.jsonl
├── relations.jsonl
├── working.jsonl
├── conflicts.jsonl
├── .lock              # advisory single-writer lock (fcntl.flock)
└── lancedb/           # present only when backend = "lancedb"
```

## Write protocol (crash-safe — D2)
1. Acquire `.lock`; fail fast if held (single writer — D5).
2. Append/rewrite each `*.jsonl` (append for incremental save; a full rewrite is acceptable for v1
   compaction).
3. `flush()` + `os.fsync()` each file.
4. Write `manifest.json.tmp`, fsync it, then `os.replace()` → `manifest.json` (**atomic commit point**).
5. Release the lock.

## Read protocol (safe + recovering — D2/D3)
1. No `manifest.json` → **empty store**, no error (US1 acceptance scenario 3).
2. Parse the manifest; `schema_version` greater than supported → `IncompatibleStoreError` (FR-004).
3. `embedder_id` or `embedding_dim` differs from the configured embedder → `EmbedderMismatchError` or
   `DimensionMismatchError`, rather than returning neighbors from the wrong vector space (US2 acceptance
   scenario 3).
4. Stream exactly the manifest-declared committed prefix for each `*.jsonl`. Missing records or malformed
   JSON **inside** that prefix raise `StoreFormatError`; malformed bytes/records **after** the manifest
   count are treated as an uncommitted torn tail and ignored (FR-005).
5. Reconstruct dataclasses by explicit field mapping — **no `eval`, no `pickle`** (FR-003).

## Guarantees
- **Safe** — opening any file never executes embedded code (SC-002).
- **Durable** — the committed prefix survives a crash mid-write (SC-003).
- **Vector-space safe** — stores written with a different embedding model id or dimension fail loudly.
- **No silent loss** — corruption inside the manifest-committed prefix fails loudly instead of loading a
  partial memory.
- **Versioned** — incompatible stores fail loudly, never silently (FR-004).
- **Lossless** — every dataclass field round-trips (Constitution IV; INV-1..4 in data-model.md).
