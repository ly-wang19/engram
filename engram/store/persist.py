"""Safe JSONL persistence for the in-memory reference stores.

The format deliberately stores plain records, not Python objects. That keeps loads non-executable while
preserving the bi-temporal fields that make Engram a memory system rather than an overwrite cache.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import fields
from typing import Any, Iterable

from ..ingest import IdentityResolver
from ..types import Conflict, Entity, Episode, Fact, Relation, WorkingMemory
from ..util import now
from .memory_store import InMemoryDocStore, InMemoryGraphStore

SCHEMA_VERSION = 1
COLLECTIONS = ("episodes", "facts", "entities", "relations", "working", "conflicts")


class PersistenceError(Exception):
    """Base class for store persistence errors."""


class IncompatibleStoreError(PersistenceError):
    """The store was written by a schema this version cannot read."""


class DimensionMismatchError(PersistenceError):
    """The stored embedding dimension does not match the configured embedder."""


class EmbedderMismatchError(PersistenceError):
    """The stored embedding model identity does not match the configured embedder."""


class StoreFormatError(PersistenceError):
    """The path or manifest is not a valid Engram JSONL store."""


def _embedder_id(embedder: Any) -> str:
    name = getattr(embedder, "model_name", None) or getattr(embedder, "model", None)
    return str(name or embedder.__class__.__name__)


def _embedder_dim(embedder: Any) -> int | None:
    dim = getattr(embedder, "dim", None)
    return int(dim) if dim is not None else None


def _record(obj: Any) -> dict[str, Any]:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def _from_record(cls: type, record: dict[str, Any]):
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in record.items() if k in allowed})


def _json_dump(obj: Any, fh) -> None:
    fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    fh.write("\n")


@contextmanager
def _lock(path: str):
    os.makedirs(path, exist_ok=True)
    lock_path = os.path.join(path, ".lock")
    with open(lock_path, "a", encoding="utf-8") as fh:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover - fcntl exists on supported Linux/macOS targets.
            yield


def _write_jsonl(path: str, name: str, rows: Iterable[dict[str, Any]]) -> int:
    tmp = os.path.join(path, f"{name}.jsonl.tmp")
    final = os.path.join(path, f"{name}.jsonl")
    count = 0
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in rows:
            _json_dump(row, fh)
            count += 1
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    return count


def _read_jsonl(path: str, name: str, limit: int | None = None) -> list[dict[str, Any]]:
    file_path = os.path.join(path, f"{name}.jsonl")
    if not os.path.exists(file_path):
        if limit:
            raise StoreFormatError(f"{name}.jsonl is missing {limit} committed record(s)")
        return []
    out: list[dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if limit is not None and len(out) >= limit:
                break
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise StoreFormatError(
                    f"{name}.jsonl has malformed JSON inside the committed prefix at line {line_no}"
                ) from exc
    if limit is not None and len(out) < limit:
        raise StoreFormatError(
            f"{name}.jsonl has {len(out)} committed record(s), manifest requires {limit}"
        )
    return out


def _manifest_count(counts: dict[str, Any], name: str) -> int:
    try:
        count = int(counts[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreFormatError(f"manifest missing valid count for {name}") from exc
    if count < 0:
        raise StoreFormatError(f"manifest count for {name} must be non-negative")
    return count


def _memory_state(mem: Any) -> dict[str, Any]:
    return {
        "resolver_parent": dict(getattr(mem.resolver, "_parent", {})),
        "persona_cache": dict(getattr(mem, "_persona_cache", {})),
        "focus": getattr(mem, "focus", {"track": [], "mute": []}),
        "policy": getattr(mem, "policy", {}),
        "identity": dict(getattr(mem, "_identity", {})),
        "aliases": {k: sorted(v) for k, v in getattr(mem, "_aliases", {}).items()},
    }


def save_memory(mem: Any, path: str, backend: str = "durable") -> None:
    """Persist a Memory instance to a schema-versioned JSONL directory."""
    if os.path.exists(path) and not os.path.isdir(path):
        raise StoreFormatError(f"store path exists and is not a directory: {path}")

    with _lock(path):
        episodes = sorted(mem.episodes_doc.values(), key=lambda e: e.id)
        hot_ids = {f.id for f in mem.fact_store.values()}
        facts = []
        for fact in sorted(mem.fact_store.values(), key=lambda f: f.id):
            row = _record(fact)
            row["_store"] = "hot"
            facts.append(row)
        for fact in sorted(mem.cold_store.values(), key=lambda f: f.id):
            if fact.id in hot_ids:
                continue
            row = _record(fact)
            row["_store"] = "cold"
            facts.append(row)
        counts = {
            "episodes": _write_jsonl(path, "episodes", (_record(e) for e in episodes)),
            "facts": _write_jsonl(path, "facts", facts),
            "entities": _write_jsonl(
                path, "entities", (_record(e) for e in sorted(mem.graph.entities.values(), key=lambda e: e.id))
            ),
            "relations": _write_jsonl(
                path, "relations", (_record(r) for r in sorted(mem.graph.relations(), key=lambda r: r.id))
            ),
            "working": _write_jsonl(
                path, "working", (_record(w) for w in sorted(mem.working_mem.values(), key=lambda w: w.id))
            ),
            "conflicts": _write_jsonl(
                path, "conflicts", (_record(c) for c in sorted(mem.conflicts.values(), key=lambda c: c.id))
            ),
        }
        for name in COLLECTIONS:
            counts.setdefault(name, 0)

        created_at = now()
        manifest_path = os.path.join(path, "manifest.json")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as fh:
                    created_at = float(json.load(fh).get("created_at", created_at))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "engram_version": "0.0.1",
            "embedder_id": _embedder_id(mem.embedder),
            "embedding_dim": _embedder_dim(mem.embedder),
            "backend": backend,
            "created_at": created_at,
            "updated_at": now(),
            "counts": counts,
            "state": _memory_state(mem),
        }
        tmp = os.path.join(path, "manifest.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, manifest_path)


def load_memory(mem: Any, path: str) -> bool:
    """Load JSONL store contents into an existing Memory instance.

    Returns False when no manifest exists, which means "start empty".
    """
    if os.path.exists(path) and not os.path.isdir(path):
        raise StoreFormatError(
            f"{path} is not an Engram JSONL store directory; run the explicit pickle migration first"
        )
    manifest_path = os.path.join(path, "manifest.json")
    if not os.path.exists(manifest_path):
        return False
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    schema = int(manifest.get("schema_version", 0))
    if schema > SCHEMA_VERSION:
        raise IncompatibleStoreError(
            f"store schema_version={schema} is newer than supported schema_version={SCHEMA_VERSION}"
        )
    stored_dim = manifest.get("embedding_dim")
    current_dim = _embedder_dim(mem.embedder)
    if stored_dim is not None and current_dim is not None and int(stored_dim) != int(current_dim):
        raise DimensionMismatchError(
            f"store embedding_dim={stored_dim} does not match current embedder dim={current_dim}"
        )
    stored_embedder = manifest.get("embedder_id")
    current_embedder = _embedder_id(mem.embedder)
    if stored_embedder is not None and str(stored_embedder) != current_embedder:
        raise EmbedderMismatchError(
            f"store embedder_id={stored_embedder!r} does not match current embedder_id={current_embedder!r}"
        )

    counts = manifest.get("counts") or {}
    expected_counts = {name: _manifest_count(counts, name) for name in COLLECTIONS}

    episodes = [_from_record(Episode, row) for row in _read_jsonl(path, "episodes", expected_counts["episodes"])]
    facts = [
        (row.get("_store", "hot"), _from_record(Fact, row))
        for row in _read_jsonl(path, "facts", expected_counts["facts"])
    ]
    entities = [_from_record(Entity, row) for row in _read_jsonl(path, "entities", expected_counts["entities"])]
    relations = [
        _from_record(Relation, row)
        for row in _read_jsonl(path, "relations", expected_counts["relations"])
    ]
    working_items = [
        _from_record(WorkingMemory, row)
        for row in _read_jsonl(path, "working", expected_counts["working"])
    ]
    conflict_items = [
        _from_record(Conflict, row)
        for row in _read_jsonl(path, "conflicts", expected_counts["conflicts"])
    ]

    def clear_vector(store):
        for payload in list(store.values()):
            key = getattr(payload, "id", None)
            if key is not None:
                store.delete(key)

    mem.episodes_doc = InMemoryDocStore()
    clear_vector(mem.episodes_vec)
    clear_vector(mem.fact_store)
    clear_vector(mem.cold_store)
    clear_vector(mem.summary_vec)
    mem.graph = InMemoryGraphStore()

    for ep in episodes:
        mem.episodes_doc.put(ep.id, ep)
        mem.episodes_vec.upsert(ep.id, ep.embedding or [], ep)
        if ep.summary_embedding is not None:
            mem.summary_vec.upsert(ep.id, ep.summary_embedding, ep)

    for tier, fact in facts:
        target = mem.cold_store if tier == "cold" else mem.fact_store
        target.upsert(fact.id, fact.embedding or [], fact)

    for entity in entities:
        mem.graph.upsert_entity(entity)
    for relation in relations:
        mem.graph.add_relation(relation)

    mem.working_mem = {}
    for item in working_items:
        mem.working_mem[item.id] = item
    mem.conflicts = {}
    for item in conflict_items:
        mem.conflicts[item.id] = item

    state = manifest.get("state") or {}
    mem.resolver = IdentityResolver()
    mem.resolver._parent.update(state.get("resolver_parent") or {})
    mem._persona_cache = state.get("persona_cache") or {}
    mem.focus = state.get("focus") or {"track": [], "mute": []}
    mem.policy = state.get("policy") or {
        "extract_instruction": "",
        "extract_system": "",
        "summary_system": "",
        "persona_system": "",
    }
    mem._identity = state.get("identity") or {}
    mem._aliases = {k: set(v) for k, v in (state.get("aliases") or {}).items()}
    return True
