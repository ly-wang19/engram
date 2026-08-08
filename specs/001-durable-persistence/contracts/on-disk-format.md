# Contract — On-Disk Format (v2, `schema_version = 2`)

## Directory layout (one per namespace)

```text
<data_path>/<namespace>/
├── manifest.json      # compatibility metadata + cross-process generation fingerprint
├── store.sqlite3      # canonical records and private mutable state
└── .lock              # advisory writer/migration lock (fcntl.flock)
```

The directory is owner-only (`0700`); the database, manifest, and lock are `0600`. Core persistence uses
only Python stdlib `sqlite3` and keeps `PRAGMA secure_delete=ON` on Engram write connections.

When the optional LanceDB backend is enabled, vectors are a rebuildable adjunct rather than canonical
state. An explicit Lance base contains one stable subnamespace per canonical snapshot:

```text
<lance-base>/                                      # 0700
├── .engram-lancedb.json                  # base ownership marker, 0600
└── namespaces/store-<sha256(canonical-path)>/  # 0700
    ├── .engram-lancedb.json              # canonical binding, 0600
    └── ...                               # LanceDB-owned tables/fragments
```

The explicit base and final root must be current-user-owned real directories. Symlinks, non-directories,
linked/malformed markers, binding mismatches, and non-empty unmarked bases/roots are rejected rather than
silently adopted.

## Write protocol (incremental + crash-safe)

1. Acquire `.lock` and take `BEGIN IMMEDIATE` in SQLite.
2. Compare the `Memory` object's bound `store_id` and generation with metadata inside that transaction.
   An unbound or stale object raises `ConcurrentWriteError` instead of overwriting committed memory.
3. UPSERT only new or changed `(collection, id)` rows. DELETE rows absent from the current in-memory
   snapshot. All six collections plus private runtime state and recovery metadata share this transaction.
4. Generate a fresh `commit_id`; persist it together with a random, stable `store_id`. Use rollback-journal
   mode (`journal_mode=DELETE`) and `synchronous=FULL`; COMMIT is the canonical data
   commit point. A crash before COMMIT rolls the entire generation back.
5. Write `manifest.json.tmp`, fsync, `os.replace()` to `manifest.json`, then fsync the directory. This
   publishes the committed generation to sibling processes.
6. If SQLite committed but manifest replacement was interrupted, the next open detects a newer database
   generation and reconstructs the lagging manifest from metadata committed in the same transaction. A
   manifest generation ahead of SQLite is rejected as corruption.

## Read protocol

1. Acquire `.lock` before deciding whether the store is empty. No manifest plus no database means empty;
   no manifest plus a valid canonical database rebuilds the manifest from transaction metadata.
2. Parse and version-check the manifest; a newer schema raises `IncompatibleStoreError`.
3. Strictly match manifest/DB `store_id`; at equal generation also match `commit_id`, counts, schema,
   embedder identity/dimension, and backend. This rejects cross-store file swaps.
4. Before opening any store-owned path, require the directory itself to be direct and every database,
   manifest, lock, sidecar, and legacy collection to be a single-link regular file. Reads and cleanup use
   `O_NOFOLLOW` plus `fstat`/inode checks where the stdlib API permits.
5. Run SQLite `quick_check`, parse payload JSON, and only then
   replace the in-memory stores. Failed loads therefore leave an existing `Memory` untouched.
6. Reconstruct dataclasses through explicit field mapping: no `eval` and no implicit pickle loading.

## Schema-v1 JSONL compatibility and migration

Schema-v1 directories remain readable. Their historical contract is the **manifest-declared committed prefix**:
malformed bytes after the manifest count are ignored as a torn tail, while a missing or malformed row
inside that prefix raises `StoreFormatError`.

On the first successful open, Engram validates the full committed prefix, writes the equivalent SQLite
generation, atomically publishes a v2 manifest without private `state`, then overwrites and unlinks all
six legacy JSONL files. If migration stops before manifest publication, the old manifest and JSONL remain
authoritative and retrying is idempotent. If it stops after publication but before cleanup, the next open
finishes cleanup. Completed migration never leaves a readable legacy plaintext copy.

## Guarantees

- **Incremental** — an unchanged save does not update collection rows; one changed object does not rewrite
  every collection file.
- **Recovering** — SQLite transactions recover interrupted writes; a lagging manifest is repairable.
- **No lost update** — optimistic generation checks reject stale full-memory snapshots.
- **Swap-resistant** — random `store_id` and per-generation `commit_id` bind DB and manifest.
- **No-follow** — store-owned links, hardlinks, and non-regular files are rejected before use.
- **Deletion-aware** — transaction DELETE plus `secure_delete` removes stale payload cells from the
  canonical database; no persistent WAL is used.
- **Safe and lossless** — JSON payloads cannot execute code and preserve every dataclass field.
- **Backward-readable** — valid schema-v1 JSONL stores migrate without requiring a third-party package.
- **Vector-space safe** — model identity mismatch raises `EmbedderMismatchError` rather than searching the
  wrong vector space; dimension mismatch raises `DimensionMismatchError`.

## Privacy limit

File modes and `secure_delete` are defense-in-depth access/deletion controls, not application-level
encryption or a physical-media erasure guarantee. LanceDB deletes remove rows from the live table but old
fragments may remain until backend/filesystem reclamation; neither backend can prove removal from SSD FTL,
APFS/cloud snapshots, backups, or sync history. Personal deployments must use FileVault, LUKS/dm-crypt, or
an equivalent encrypted volume and apply explicit retention/deletion policies to every replica.
