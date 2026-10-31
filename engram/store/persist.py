"""Safe JSONL persistence for the in-memory reference stores.

The format deliberately stores plain records, not Python objects. That keeps loads non-executable while
preserving the bi-temporal fields that make Engram a memory system rather than an overwrite cache.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from contextlib import contextmanager
from dataclasses import fields
from typing import Any, Iterable

from .. import __version__, filelock
from ..ingest import IdentityResolver
from ..types import Conflict, Entity, Episode, Fact, Relation, WorkingMemory
from ..util import now
from .memory_store import InMemoryDocStore, InMemoryGraphStore

SCHEMA_VERSION = 1
COLLECTIONS = ("episodes", "facts", "entities", "relations", "working", "conflicts")
SNAPSHOT_FILES = tuple(f"{name}.jsonl" for name in COLLECTIONS) + ("manifest.json",)
_BACKUP_DIR = ".snapshot-backup"


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


# Every float parsed from JSON is its own 24-byte object, so a loaded 256-dim vector cost ~8KB and
# vectors were a third of a real-scale process. Hashing vectors repeat a handful of values (mostly 0.0),
# so the loader hands out one object per distinct number and a vector costs its 2KB of pointers — same
# list, same values, same speed (an array('d') saved as much but made every similarity scan box its
# floats, 3x slower recall). Sharing at parse time, not after: floats created and then dropped leave
# their memory with the process.
_FLOATS: dict[str, float] = {}
_FLOATS_MAX = 1 << 20  # dense model vectors rarely repeat; stop growing the table rather than hoard them


def _parse_float(text: str) -> float:
    value = _FLOATS.get(text)
    if value is None:
        value = float(text)
        if len(_FLOATS) < _FLOATS_MAX:
            _FLOATS[text] = value
    return value


@contextmanager
def _lock(path: str):
    os.makedirs(path, exist_ok=True)
    lock_path = os.path.join(path, ".lock")
    with open(lock_path, "a", encoding="utf-8") as fh:
        filelock.lock(fh.fileno())
        try:
            yield
        finally:
            filelock.unlock(fh.fileno())


def _replace(source: str, target: str, attempts: int = 6) -> None:
    """os.replace, retried on Windows where antivirus or an indexer briefly holding the target makes
    it fail with PermissionError."""
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if os.name != "nt" or attempt == attempts - 1:
                raise
            time.sleep(0.05 * (2 ** attempt))


def _sync_dir(path: str) -> None:
    if os.name == "nt":
        return  # a directory cannot be opened for fsync on Windows; NTFS journals the rename itself
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy_synced(source: str, target: str) -> None:
    with open(source, "rb") as src, open(target, "wb") as dst:
        if hasattr(os, "fchmod"):  # absent on Windows, where the mode bits mean little anyway
            os.fchmod(dst.fileno(), stat.S_IMODE(os.fstat(src.fileno()).st_mode))
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())


def _recover_snapshot(path: str) -> None:
    backup = os.path.join(path, _BACKUP_DIR)
    ready = os.path.join(backup, "ready.json")
    if not os.path.exists(ready):
        return
    try:
        with open(ready, "r", encoding="utf-8") as fh:
            marker = json.load(fh)
        # A list is the original format: every snapshot file was backed up. The dict form backs up only
        # the files a save replaces; a file it appended to or left alone is not in `rewrite` and must not
        # be touched here (an append is undone by the restored manifest's committed count instead).
        if isinstance(marker, list):
            present, rewrite = marker, list(SNAPSHOT_FILES)
        elif isinstance(marker, dict):
            present, rewrite = marker.get("present"), marker.get("rewrite")
        else:
            raise ValueError("invalid snapshot backup marker")
        if not isinstance(present, list) or not isinstance(rewrite, list) \
                or any(name not in SNAPSHOT_FILES for name in [*present, *rewrite]) \
                or any(name not in rewrite for name in present):
            raise ValueError("invalid snapshot backup file list")
        # Check all recovery inputs before changing the live snapshot. Keep backups until recovery
        # completes, so another interruption during recovery itself can safely retry the same work.
        if any(not os.path.isfile(os.path.join(backup, name)) for name in present):
            raise ValueError("snapshot backup is incomplete")
    except (ValueError, TypeError) as exc:
        raise StoreFormatError("cannot recover unfinished snapshot: invalid backup") from exc
    for name in rewrite:
        target = os.path.join(path, name)
        if name in present:
            tmp = target + ".recover"
            _copy_synced(os.path.join(backup, name), tmp)
            os.replace(tmp, target)
        elif os.path.exists(target):
            os.unlink(target)
    _sync_dir(path)
    os.unlink(ready)
    _sync_dir(backup)


@contextmanager
def _snapshot_transaction(path: str, rewrite: Iterable[str] = SNAPSHOT_FILES):
    """Make the replacement of `rewrite` (plus the manifest) atomic. Only those files are backed up: on a
    large store the episodes log is appended to, not replaced, and copying it before every save wrote
    twice the store's size per write."""
    _recover_snapshot(path)
    rewrite = [name for name in SNAPSHOT_FILES if name in set(rewrite) or name == "manifest.json"]
    backup = os.path.join(path, _BACKUP_DIR)
    # An unmarked backup is either an interrupted preparation or a completed transaction.
    if os.path.exists(backup):
        shutil.rmtree(backup)
    os.mkdir(backup, mode=0o700)
    present = []
    for name in rewrite:
        source = os.path.join(path, name)
        if os.path.exists(source):
            _copy_synced(source, os.path.join(backup, name))
            present.append(name)
    _sync_dir(backup)
    ready = os.path.join(backup, "ready.json")
    ready_tmp = ready + ".tmp"
    with open(ready_tmp, "w", encoding="utf-8") as fh:
        json.dump({"present": present, "rewrite": rewrite}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(ready_tmp, ready)
    _sync_dir(backup)
    _sync_dir(path)
    try:
        yield
        _sync_dir(path)
    except Exception:
        _recover_snapshot(path)
        raise
    # The backup marker is the commit boundary for all collection replacements, not just the final
    # manifest rename. A process exit before this point is recovered by the next locked load/save.
    os.unlink(ready)
    _sync_dir(backup)
    # Keep a committed save successful even if best-effort removal of its obsolete backup fails.
    shutil.rmtree(backup, ignore_errors=True)


def _write_jsonl(path: str, name: str, rows: Iterable[dict[str, Any]]) -> int:
    return _write_jsonl_sized(path, name, rows)[0]


def _write_jsonl_sized(path: str, name: str, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Replace `<name>.jsonl` with `rows`; returns (row count, byte size)."""
    tmp = os.path.join(path, f"{name}.jsonl.tmp")
    final = os.path.join(path, f"{name}.jsonl")
    count = 0
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            _json_dump(row, fh)
            count += 1
        fh.flush()
        os.fsync(fh.fileno())
        size = fh.tell()
    _replace(tmp, final)
    return count, size


def _append_jsonl(path: str, name: str, committed_bytes: int, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Append `rows` after the committed prefix of `<name>.jsonl` (bytes past it are an interrupted
    append, cut first). Returns (rows appended, new byte size). Readers stop at the manifest's committed
    row count, so until the manifest is replaced the appended rows do not exist."""
    final = os.path.join(path, f"{name}.jsonl")
    count = 0
    with open(final, "r+b") as fh:
        fh.truncate(committed_bytes)
        if committed_bytes:
            fh.seek(committed_bytes - 1)
            if fh.read(1) != b"\n":  # a committed last row written without its newline
                fh.write(b"\n")
        fh.seek(0, os.SEEK_END)
        for row in rows:
            fh.write((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                     .encode("utf-8"))
            count += 1
        fh.flush()
        os.fsync(fh.fileno())
        size = fh.tell()
    return count, size


def _read_jsonl(path: str, name: str, limit: int | None = None) -> list[dict[str, Any]]:
    return _read_jsonl_sized(path, name, limit)[0]


def _read_jsonl_sized(path: str, name: str, limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    """The committed rows of `<name>.jsonl` and the byte length of that committed prefix."""
    file_path = os.path.join(path, f"{name}.jsonl")
    if not os.path.exists(file_path):
        if limit:
            raise StoreFormatError(f"{name}.jsonl is missing {limit} committed record(s)")
        return [], 0
    out: list[dict[str, Any]] = []
    consumed = 0
    with open(file_path, "rb") as fh:
        for line_no, raw in enumerate(fh, start=1):
            if limit is not None and len(out) >= limit:
                break
            try:
                out.append(json.loads(raw.decode("utf-8"), parse_float=_parse_float))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise StoreFormatError(
                    f"{name}.jsonl has malformed JSON inside the committed prefix at line {line_no}"
                ) from exc
            consumed += len(raw)
    if limit is not None and len(out) < limit:
        raise StoreFormatError(
            f"{name}.jsonl has {len(out)} committed record(s), manifest requires {limit}"
        )
    return out, consumed


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
        "cold_pages_out": dict(getattr(mem, "cold_pages_out", {})),
        "cold_pages_in": dict(getattr(mem, "cold_pages_in", {})),
        # idempotent-import fingerprints (content hashes only — no memory content), so re-imports stay
        # deduplicated across restarts
        "import_seen": sorted(getattr(mem, "_import_seen", ())),
    }


def _episode_sig(ep: Any) -> int:
    """Cheap change detector for one episode within this process (str hashes are cached on the object,
    so this does not rehash a transcript that did not change)."""
    return hash((
        ep.content, ep.user_id, ep.session_id, ep.speaker, ep.event_time, ep.ingested_at, ep.consolidated,
        ep.summary, tuple(ep.embedding or ()), tuple(ep.summary_embedding or ()),
        json.dumps(ep.metadata, sort_keys=True, default=str),
    ))


def _episodes_plan(mem: Any, path: str, manifest: dict[str, Any] | None):
    """Decide how to persist episodes: ("skip"|"append"|"rewrite", rows to append, new sigs).

    Append when the on-disk log is exactly what this Memory last loaded or saved and nothing was
    deleted: new episodes and changed ones (a grown agent session) go on the end, and the loader lets a
    later row for an id win. Rewrite on deletes, when superseded rows pass a quarter of the log, or when
    the log is not ours (another process saved, a forget was installed, a first save)."""
    episodes = list(mem.episodes_doc.values())
    sigs = {ep.id: _episode_sig(ep) for ep in episodes}
    cache = getattr(mem, "_persist_episodes", None)
    sizes = (manifest or {}).get("sizes") or {}
    counts = (manifest or {}).get("counts") or {}
    log = os.path.join(path, "episodes.jsonl")
    if (cache is None or cache.get("path") != os.path.abspath(path) or manifest is None
            or sizes.get("episodes") != cache.get("bytes") or counts.get("episodes") != cache.get("rows")
            or not os.path.exists(log) or os.path.getsize(log) < cache["bytes"]):
        return "rewrite", episodes, sigs
    old = cache["sigs"]
    if any(ident not in sigs for ident in old):
        return "rewrite", episodes, sigs
    changed = [ep for ep in episodes if old.get(ep.id) != sigs[ep.id]]
    if not changed:
        return "skip", [], sigs
    superseded = cache.get("dead", 0) + sum(1 for ep in changed if ep.id in old)
    if superseded > max(64, (cache["rows"] + len(changed)) // 4):
        return "rewrite", episodes, sigs
    return "append", sorted(changed, key=lambda e: e.id), sigs


def _read_manifest(path: str) -> dict[str, Any] | None:
    manifest_path = os.path.join(path, "manifest.json")
    if not os.path.exists(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def save_memory(mem: Any, path: str, backend: str = "durable") -> None:
    """Persist a Memory instance to a schema-versioned JSONL directory."""
    if os.path.exists(path) and not os.path.isdir(path):
        raise StoreFormatError(f"store path exists and is not a directory: {path}")

    with _lock(path):
        _recover_snapshot(path)
        prior = _read_manifest(path)
        mode, ep_rows, sigs = _episodes_plan(mem, path, prior)
        small = [f"{name}.jsonl" for name in COLLECTIONS if name != "episodes"]
        rewrite = small + (["episodes.jsonl"] if mode == "rewrite" else [])
        with _snapshot_transaction(path, rewrite):
            _save_locked(mem, path, backend, prior, mode, ep_rows, sigs)


def _save_locked(mem: Any, path: str, backend: str, prior: dict[str, Any] | None, mode: str,
                 ep_rows: list, sigs: dict[str, int]) -> None:
    cache = getattr(mem, "_persist_episodes", None) or {}
    if mode == "rewrite":
        ep_count, ep_bytes = _write_jsonl_sized(
            path, "episodes", (_record(e) for e in sorted(ep_rows, key=lambda e: e.id)))
        dead = 0
    elif mode == "append":
        appended, ep_bytes = _append_jsonl(path, "episodes", cache["bytes"], (_record(e) for e in ep_rows))
        ep_count = cache["rows"] + appended
        dead = cache.get("dead", 0) + sum(1 for e in ep_rows if e.id in cache["sigs"])
    else:
        ep_count, ep_bytes, dead = cache["rows"], cache["bytes"], cache.get("dead", 0)
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
        "episodes": ep_count,
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
    if prior is not None:
        try:
            created_at = float(prior.get("created_at", created_at))
        except (ValueError, TypeError):
            pass
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "engram_version": __version__,
        "embedder_id": _embedder_id(mem.embedder),
        "embedding_dim": _embedder_dim(mem.embedder),
        "backend": backend,
        "created_at": created_at,
        "updated_at": now(),
        "counts": counts,
        # committed byte length of the episodes log, so the next save can append after exactly it
        "sizes": {"episodes": ep_bytes},
        "state": _memory_state(mem),
    }
    tmp = os.path.join(path, "manifest.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    _replace(tmp, manifest_path)
    mem._persist_episodes = {"path": os.path.abspath(path), "rows": ep_count, "bytes": ep_bytes,
                             "dead": dead, "sigs": sigs}


def load_memory(mem: Any, path: str, allow_mismatch: bool = False) -> bool:
    """Load JSONL store contents into an existing Memory instance.

    Returns False when no manifest exists, which means "start empty". `allow_mismatch=True` skips the
    embedder identity/dimension guards — ONLY for the explicit re-embedding migration
    (`Memory.reembed()`), which replaces every stored vector right after loading.
    """
    if os.path.exists(path) and not os.path.isdir(path):
        raise StoreFormatError(
            f"{path} is not an Engram JSONL store directory; run the explicit pickle migration first"
        )
    if not os.path.isdir(path):
        return False
    # Readers must not observe any of the intermediate collection replacements of a writer.
    with _lock(path):
        _recover_snapshot(path)
        return _load_memory(mem, path, allow_mismatch)


def _load_memory(mem: Any, path: str, allow_mismatch: bool) -> bool:
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
    if not allow_mismatch and stored_dim is not None and current_dim is not None \
            and int(stored_dim) != int(current_dim):
        raise DimensionMismatchError(
            f"store embedding_dim={stored_dim} does not match current embedder dim={current_dim}; "
            "re-attach the original embedder, or migrate with Memory.open(path, allow_mismatch=True, ...)"
            ".reembed() (service: ENGRAM_REEMBED_ON_MISMATCH=1)"
        )
    stored_embedder = manifest.get("embedder_id")
    current_embedder = _embedder_id(mem.embedder)
    # A store written by the pre-CJK hashing embedder is the same vector space for every ASCII text; only
    # items holding CJK hashed to near-empty vectors. Open it and let Memory.open re-embed just those.
    legacy_hashing = _is_hashing_upgrade(stored_embedder, current_embedder)
    if not allow_mismatch and stored_embedder is not None and str(stored_embedder) != current_embedder \
            and not legacy_hashing:
        raise EmbedderMismatchError(
            f"store embedder_id={stored_embedder!r} does not match current embedder_id={current_embedder!r}; "
            "re-attach the original embedder, or migrate with Memory.open(path, allow_mismatch=True, ...)"
            ".reembed() (service: ENGRAM_REEMBED_ON_MISMATCH=1)"
        )

    counts = manifest.get("counts") or {}
    expected_counts = {name: _manifest_count(counts, name) for name in COLLECTIONS}

    episode_rows, episode_bytes = _read_jsonl_sized(path, "episodes", expected_counts["episodes"])
    episodes = [_from_record(Episode, row) for row in episode_rows]
    del episode_rows
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
    mem.cold_pages_out = {k: int(v) for k, v in (state.get("cold_pages_out") or {}).items()}
    mem.cold_pages_in = {k: int(v) for k, v in (state.get("cold_pages_in") or {}).items()}
    mem._import_seen = set(state.get("import_seen") or ())
    mem._stale_cjk_vectors = legacy_hashing
    # What is on disk now, so the next save can append instead of rewriting. A later row for an id
    # superseded an earlier one (an appended grown session); those dead rows count toward compaction.
    loaded = mem.episodes_doc.values()
    mem._persist_episodes = {"path": os.path.abspath(path), "rows": len(episodes), "bytes": episode_bytes,
                             "dead": len(episodes) - len(loaded),
                             "sigs": {ep.id: _episode_sig(ep) for ep in loaded}}
    return True


def _is_hashing_upgrade(stored: Any, current: str) -> bool:
    from ..embed.hashing import HASHING_MODEL, LEGACY_HASHING_IDS

    return stored is not None and str(stored) in LEGACY_HASHING_IDS and current == HASHING_MODEL
