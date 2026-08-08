# Phase 1 — Data Model: Persisted Records & Manifest

The canonical entity definitions live in [`engram/types.py`](../../engram/types.py); this document
specifies **how they are persisted** and the **invariants** the serializer must hold. The source dataclass
is the single source of truth for fields — the serializer persists the **complete** field set so reload is
lossless (Constitution IV).

## Persisted collections (SQLite `records` table)

Each object is stored as compact JSON in a row keyed by `(collection, id)`. JSON keeps nested metadata,
embeddings, and provenance portable while SQLite supplies transactional UPSERT/DELETE and crash recovery.

| Collection | Dataclass | Fields that MUST survive the round-trip |
|---|---|---|
| `episodes` | `Episode` | id, content, user_id, session_id, speaker, **event_time**, **ingested_at**, embedding, consolidated, summary, summary_embedding, metadata |
| `facts` | `Fact` | id, subject, predicate, object, text, display, user_id, **embedding**, salience, confidence, source, category, sensitive, **valid_at, invalid_at, created_at, expired_at, supersedes, provenance**, last_access, access_count |
| `entities` | `Entity` | id, name, type, user_id, embedding, aliases |
| `relations` | `Relation` | id, subject_id, predicate, object_id, fact_id, **valid_at, invalid_at, created_at, expired_at** |
| `working` | `WorkingMemory` | id, content, user_id, session_id, kind, event_time, created_at, expires_at, consumed, embedding, metadata |
| `conflicts` | `Conflict` | id, older, newer, text_older, text_newer, user_id, reason, status, detected_at |

> The persisted set = **every collection `Memory` holds today** (the current pickle dict). `WorkingMemory`
> and `Conflict` are included so session state and the conflict **audit trail** survive a restart too —
> dropping them would be silent loss (Constitution IV).

## Record serialization rules
- One compact JSON object per SQLite row, UTF-8 text.
- `float` time fields are written as JSON numbers (epoch seconds — no precision loss at our magnitudes).
- `Optional[...] = None` fields are written explicitly as `null` (absence ≠ default ambiguity).
- `embedding` / `summary_embedding` are JSON arrays of floats, or `null`.
- `metadata` is an arbitrary JSON object (already dict-typed).
- Reconstruction maps fields **explicitly** onto the dataclass constructor: unknown fields from a newer
  minor version are ignored; missing optional fields fall back to the dataclass default (forward-tolerant
  *within* a `schema_version`).

## Bi-temporal & provenance invariants (release gates)
- **INV-1**: every persisted `Fact` reloads with `valid_at / invalid_at / created_at / expired_at /
  supersedes / provenance` **identical** — an invalidated fact stays invalid, its supersedes chain intact
  (no hard-delete on reload).
- **INV-2**: every `Relation` reloads `valid_at / invalid_at` identical (the graph's bi-temporal edges),
  so `neighbors(as_of=…)` returns the same live set after restart.
- **INV-3**: provenance episode-id lists reload identical, so "where did this come from?" still answers.
- **INV-4**: per-collection committed row counts equal manifest `counts` at the same generation.

## `manifest.json` schema (new)
```json
{
  "schema_version": 2,
  "format": "sqlite",
  "engram_version": "0.x.y",
  "embedder_id": "bge-small-en-v1.5 | hashing | ...",
  "embedding_dim": 384,
  "created_at": 1750000000.0,
  "updated_at": 1750000000.0,
  "backend": "durable | lancedb",
  "store_id": "stable-random-id-for-this-store",
  "commit_id": "fresh-random-id-for-generation-42",
  "generation": 42,
  "counts": {"episodes": 0, "facts": 0, "entities": 0, "relations": 0, "working": 0, "conflicts": 0}
}
```
- `embedder_id` + `embedding_dim` guard model-id and dimension mismatch errors on open (US2-AC3), so a
  same-dimension but different embedding model cannot silently reuse stale neighbors.
- `schema_version` gates compatibility (D3); `backend` records which vector store wrote the snapshot.
- `generation` coordinates process-local caches with the atomic SQLite commit. Private `state` is stored
  in SQLite metadata and is intentionally absent from the manifest.
- `store_id` is stable for the store lifetime and rejects DB/manifest swaps. `commit_id` changes on every
  committed generation and rejects same-generation mix-and-match; both are duplicated in SQLite metadata.
