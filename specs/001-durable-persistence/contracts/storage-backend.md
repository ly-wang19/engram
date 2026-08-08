# Contract — Durable Store Backends

A durable backend is **not a new API**: it implements the existing ABCs in
[`engram/store/base.py`](../../../engram/store/base.py). This contract restates the obligations a durable
implementation MUST honor beyond the in-memory reference.

## VectorStore (LanceDBVectorStore)
| Method | Durable obligation |
|---|---|
| `upsert(key, vector, payload)` | persist `(key, vector, payload)` so it survives process restart; idempotent on `key` |
| `search(vector, top_k, where)` | return top_k by similarity; `where` predicate semantics MUST match the in-memory store (post-filter acceptable in v1) |
| `get(key)` | return the persisted payload or `None` |
| `delete(key)` | remove from the live table (used by decay / forget); this is logical deletion, not proof of physical fragment/media erasure |
| `values()` | iterate all payloads (used by save + consolidation); MUST stream — never materialize the whole store as one blob |

**Parity gate (SC-005 / FR-009):** for identical data + query, reloaded-LanceDB `search` top-k matches the
in-memory store within a documented tolerance (ties + float error only).

**Lazy-import gate (FR-007):** `import lancedb` happens **inside** `lancedb_store.py`, never at `engram/`
core import time. `tests/test_zero_setup_default.py` asserts the default import graph is lancedb-free.

**Path/privacy gate:** the explicit base and final Lance root are current-user-owned real directories forced
to `0700`; their single-link owner markers are `0600`. Symlink/non-directory paths, unsafe table names, a
mismatched namespace binding, and non-empty unmarked bases/roots are rejected before `lancedb.connect`.
`Memory.open` derives a stable canonical-snapshot subnamespace below the base, so separate snapshots cannot
silently reuse the same tables.

## DocStore / GraphStore (durable form, v1)
v2 persists episodes (`DocStore`) and entities + relations (`GraphStore`) through the **snapshot
serializer** (`persist.py`), not a live embedded graph DB — the in-memory dict stores stay in RAM and are
saved/loaded in canonical SQLite rows. (A native embedded graph backend, e.g. Kuzu, is a separate later spec.)

**Contract:** a `save → open` cycle reconstructs identical `DocStore.values()`, `GraphStore.relations()`,
and `GraphStore.neighbors(entity_id, as_of=…, direction=…)` results (INV-2).

## Backend selection (config)
`engram/config.py` exposes `storage = "memory" | "durable" | "lancedb"` plus a `data_path`:
- `"memory"` — **default**, changes nothing (Constitution II).
- `"durable"` — canonical SQLite snapshot for all stores (US1).
- `"lancedb"` — canonical SQLite snapshot plus a rebuildable LanceDB vector index (US2).

The public surface is unchanged: callers keep using `Memory.open(path)` / `mem.save()`; only the on-disk
representation and the optional vector backend differ.

LanceDB does not supply application-level encryption here. Owner-only modes are access control, not
encryption; personal deployments must use FileVault, LUKS/dm-crypt, or an equivalent encrypted volume and
apply the same policy to backups/snapshots. Logical deletes do not establish that old Lance fragments,
SSD translation layers, filesystem/cloud snapshots, backups, or sync history have been physically erased.
