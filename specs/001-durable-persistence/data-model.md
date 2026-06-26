# Phase 1 — Data Model: Persisted Records & Manifest

The canonical entity definitions live in [`engram/types.py`](../../engram/types.py); this document
specifies **how they are persisted** and the **invariants** the serializer must hold. The source dataclass
is the single source of truth for fields — the serializer persists the **complete** field set so reload is
lossless (Constitution IV).

## Persisted collections (one `.jsonl` each)

| File | Dataclass | Fields that MUST survive the round-trip |
|---|---|---|
| `episodes.jsonl` | `Episode` | id, content, user_id, session_id, speaker, **event_time**, **ingested_at**, embedding, consolidated, summary, summary_embedding, metadata |
| `facts.jsonl` | `Fact` | id, subject, predicate, object, text, display, user_id, **embedding**, salience, confidence, source, category, sensitive, **valid_at, invalid_at, created_at, expired_at, supersedes, provenance**, last_access, access_count |
| `entities.jsonl` | `Entity` | id, name, type, user_id, embedding, aliases |
| `relations.jsonl` | `Relation` | id, subject_id, predicate, object_id, fact_id, **valid_at, invalid_at** |
| `working.jsonl` | `WorkingMemory` | id, content, user_id, session_id, kind, event_time, created_at, expires_at, consumed, embedding, metadata |
| `conflicts.jsonl` | `Conflict` | id, older, newer, text_older, text_newer, user_id, reason, status, detected_at |

> The persisted set = **every collection `Memory` holds today** (the current pickle dict). `WorkingMemory`
> and `Conflict` are included so session state and the conflict **audit trail** survive a restart too —
> dropping them would be silent loss (Constitution IV).

## Record serialization rules
- One compact JSON object per line, UTF-8, `\n`-terminated.
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
- **INV-4**: per-file committed line counts equal the manifest `counts` after a successful load.

## `manifest.json` schema (new)
```json
{
  "schema_version": 1,
  "engram_version": "0.x.y",
  "embedder_id": "bge-small-en-v1.5 | hashing | ...",
  "embedding_dim": 384,
  "created_at": 1750000000.0,
  "updated_at": 1750000000.0,
  "backend": "durable | lancedb",
  "counts": {"episodes": 0, "facts": 0, "entities": 0, "relations": 0, "working": 0, "conflicts": 0}
}
```
- `embedder_id` + `embedding_dim` guard the model/dimension-mismatch error on open (US2-AC3).
- `schema_version` gates compatibility (D3); `backend` records which vector store wrote the snapshot.
