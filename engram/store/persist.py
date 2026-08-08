"""Crash-safe, incremental persistence for the in-memory reference stores.

New stores use a stdlib-only SQLite database as the canonical data file.  Legacy JSONL snapshots remain
readable and are migrated on first successful open.  The public manifest contains only compatibility and
change-detection metadata; memory content and mutable runtime state live inside SQLite.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import fields
from typing import Any
from urllib.parse import quote

from .. import __version__
from ..ingest import IdentityResolver
from ..twin import (
    ActionDecision,
    ActionRecord,
    ActionRequest,
    CapabilityRegistry,
    TwinContract,
)
from ..types import Conflict, Entity, Episode, Fact, Relation, WorkingMemory
from ..util import now
from .memory_store import InMemoryDocStore, InMemoryGraphStore

SCHEMA_VERSION = 2
SQLITE_FILE = "store.sqlite3"
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
    """The path or manifest is not a valid Engram durable store."""


class ConcurrentWriteError(PersistenceError):
    """The in-memory snapshot is older than the canonical store generation."""


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


def _json_text(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_dump(obj: Any, fh) -> None:
    fh.write(_json_text(obj))
    fh.write("\n")


def _lstat(path: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _require_regular(path: str, *, missing_ok: bool = False) -> os.stat_result | None:
    """Reject links and special files before any persistence operation can follow them."""
    st = _lstat(path)
    if st is None:
        if missing_ok:
            return None
        raise StoreFormatError(f"required persistence file is missing: {path}")
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise StoreFormatError(f"persistence file must be a direct regular file: {path}")
    return st


def _secure_directory(path: str) -> None:
    existing = _lstat(path)
    if existing is None:
        try:
            os.makedirs(path, mode=0o700, exist_ok=False)
        except FileExistsError:
            pass  # A concurrent first opener may have created the same direct directory.
        existing = _lstat(path)
    if existing is None or not stat.S_ISDIR(existing.st_mode):
        raise StoreFormatError(f"store path must be a direct directory, not a link or special file: {path}")
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise PersistenceError(f"cannot restrict store directory permissions: {path}") from exc


def _secure_file(path: str) -> None:
    _require_regular(path)
    try:
        os.chmod(path, 0o600)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PersistenceError(f"cannot restrict store file permissions: {path}") from exc


@contextmanager
def _lock(path: str):
    _secure_directory(path)
    lock_path = os.path.join(path, ".lock")
    expected_stat = _require_regular(lock_path, missing_ok=True)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise StoreFormatError(f"cannot safely open persistence lock: {lock_path}") from exc
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise StoreFormatError(f"persistence lock must be a direct regular file: {lock_path}")
        if expected_stat is not None and (st.st_dev, st.st_ino) != (
            expected_stat.st_dev,
            expected_stat.st_ino,
        ):
            raise StoreFormatError("persistence lock changed while opening")
        os.fchmod(fh.fileno(), 0o600)
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover - fcntl exists on supported Linux/macOS targets.
            yield


# Kept as the schema-v1 compatibility reader.  New saves never write JSONL collection files.
def _read_jsonl(path: str, name: str, limit: int | None = None) -> list[dict[str, Any]]:
    file_path = os.path.join(path, f"{name}.jsonl")
    expected_stat = _require_regular(file_path, missing_ok=True)
    if expected_stat is None:
        if limit:
            raise StoreFormatError(f"{name}.jsonl is missing {limit} committed record(s)")
        return []
    out: list[dict[str, Any]] = []
    flags = os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(file_path, flags)
    except OSError as exc:
        raise StoreFormatError(f"cannot safely open legacy collection: {file_path}") from exc
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise StoreFormatError(f"legacy collection must be a direct regular file: {file_path}")
        if (st.st_dev, st.st_ino) != (expected_stat.st_dev, expected_stat.st_ino):
            raise StoreFormatError(f"legacy collection changed while opening: {file_path}")
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
    twin_decisions = getattr(mem, "twin_decisions", {})
    return {
        "resolver_parent": dict(getattr(mem.resolver, "_parent", {})),
        "persona_cache": dict(getattr(mem, "_persona_cache", {})),
        "focus": getattr(mem, "focus", {"track": [], "mute": []}),
        "policy": getattr(mem, "policy", {}),
        "identity": dict(getattr(mem, "_identity", {})),
        "aliases": {k: sorted(v) for k, v in getattr(mem, "_aliases", {}).items()},
        "cold_pages_out": dict(getattr(mem, "cold_pages_out", {})),
        "cold_pages_in": dict(getattr(mem, "cold_pages_in", {})),
        "twin_contract": getattr(mem, "twin_contract", TwinContract()).to_dict(),
        "twin_contract_history": [
            contract.to_dict()
            for contract in getattr(
                mem,
                "twin_contract_history",
                (getattr(mem, "twin_contract", TwinContract()),),
            )
        ],
        "capability_registry": getattr(
            mem,
            "capability_registry",
            CapabilityRegistry(),
        ).to_dict(),
        "twin_decisions": [
            {"request": request.to_dict(), "decision": decision.to_dict()}
            for _, (request, decision) in sorted(twin_decisions.items())
        ],
        "twin_actions": [
            item.to_dict() for item in getattr(mem, "twin_actions", ())
        ],
    }


def _snapshot(mem: Any) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    hot_ids = {f.id for f in mem.fact_store.values()}
    facts: list[dict[str, Any]] = []
    for fact in sorted(mem.fact_store.values(), key=lambda f: f.id):
        row = _record(fact)
        row["_store"] = "hot"
        facts.append(row)
    for fact in sorted(mem.cold_store.values(), key=lambda f: f.id):
        if fact.id not in hot_ids:
            row = _record(fact)
            row["_store"] = "cold"
            facts.append(row)
    records = {
        "episodes": [_record(e) for e in sorted(mem.episodes_doc.values(), key=lambda e: e.id)],
        "facts": facts,
        "entities": [_record(e) for e in sorted(mem.graph.entities.values(), key=lambda e: e.id)],
        "relations": [_record(r) for r in sorted(mem.graph.relations(), key=lambda r: r.id)],
        "working": [_record(w) for w in sorted(mem.working_mem.values(), key=lambda w: w.id)],
        "conflicts": [_record(c) for c in sorted(mem.conflicts.values(), key=lambda c: c.id)],
    }
    return records, _memory_state(mem)


def _connect(path: str, *, create: bool) -> sqlite3.Connection:
    db_path = os.path.join(path, SQLITE_FILE)
    _require_regular(db_path, missing_ok=create)
    # SQLite has no fd-based Python API. URI mode prevents a read path from silently creating a missing
    # database; O_NOFOLLOW-style lstat checks on both sides reject static link/special-file attacks.
    mode = "rwc" if create else "rw"
    uri = f"file:{quote(os.path.abspath(db_path), safe='/')}?mode={mode}"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, timeout=30.0, uri=True)
        _require_regular(db_path)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        _secure_file(db_path)
        return conn
    except PersistenceError:
        if conn is not None:
            conn.close()
        raise
    except (OSError, sqlite3.Error) as exc:
        if conn is not None:
            conn.close()
        raise StoreFormatError(f"cannot open Engram SQLite store: {db_path}") from exc


def _init_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS records ("
        "collection TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, "
        "PRIMARY KEY (collection, id)) WITHOUT ROWID"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL) WITHOUT ROWID"
    )


def _write_sqlite_snapshot(
    path: str,
    records: dict[str, list[dict[str, Any]]],
    state: dict[str, Any],
    store_info: dict[str, Any],
    *,
    expected_generation: int | None,
    expected_store_id: str | None,
) -> tuple[int, dict[str, int]]:
    """Apply one in-memory snapshot as incremental UPSERT/DELETE operations in one transaction."""
    conn = _connect(path, create=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _init_sqlite(conn)
        old_generation_row = conn.execute(
            "SELECT payload FROM metadata WHERE key='generation'"
        ).fetchone()
        old_store_row = conn.execute("SELECT payload FROM metadata WHERE key='store_id'").fetchone()
        old_generation = int(json.loads(old_generation_row[0])) if old_generation_row else None
        old_store_id = str(json.loads(old_store_row[0])) if old_store_row else None
        if expected_generation is None:
            if old_generation is not None or conn.execute("SELECT 1 FROM records LIMIT 1").fetchone():
                raise ConcurrentWriteError(
                    "unbound Memory cannot overwrite an existing canonical store; open it before saving"
                )
        elif old_generation != expected_generation:
            raise ConcurrentWriteError(
                f"stale Memory generation={expected_generation}; canonical generation={old_generation}"
            )
        if expected_store_id is not None and old_store_id != expected_store_id:
            raise ConcurrentWriteError("Memory is bound to a different canonical store")
        new_store_id = str(store_info.get("store_id") or "")
        new_commit_id = str(store_info.get("commit_id") or "")
        if not new_store_id:
            raise StoreFormatError("SQLite commit is missing store_id")
        if not new_commit_id:
            raise StoreFormatError("SQLite commit is missing commit_id")
        if old_store_id is not None and old_store_id != new_store_id:
            raise StoreFormatError("SQLite store_id cannot change across generations")
        conn.execute(
            "CREATE TEMP TABLE current_ids ("
            "collection TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY (collection, id)) WITHOUT ROWID"
        )
        for collection in COLLECTIONS:
            rows = records.get(collection, [])
            encoded = [
                (collection, str(row["id"]), _json_text(row))
                for row in rows
            ]
            if encoded:
                conn.executemany(
                    "INSERT INTO records(collection,id,payload) VALUES(?,?,?) "
                    "ON CONFLICT(collection,id) DO UPDATE SET payload=excluded.payload "
                    "WHERE records.payload != excluded.payload",
                    encoded,
                )
                conn.executemany(
                    "INSERT INTO current_ids(collection,id) VALUES(?,?)",
                    [(collection, row_id) for _, row_id, _ in encoded],
                )
            conn.execute(
                "DELETE FROM records WHERE collection=? AND NOT EXISTS ("
                "SELECT 1 FROM current_ids c WHERE c.collection=records.collection AND c.id=records.id)",
                (collection,),
            )
        generation = (old_generation or 0) + 1
        metadata = {
            "state": state,
            "generation": generation,
            "schema_version": SCHEMA_VERSION,
            "store_info": store_info,
            "store_id": new_store_id,
            "commit_id": new_commit_id,
        }
        conn.executemany(
            "INSERT INTO metadata(key,payload) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
            [(key, _json_text(value)) for key, value in metadata.items()],
        )
        conn.commit()
        counts = {name: len(records.get(name, [])) for name in COLLECTIONS}
        return generation, counts
    except ConcurrentWriteError:
        conn.rollback()
        raise
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        conn.rollback()
        raise StoreFormatError("could not commit Engram SQLite snapshot") from exc
    finally:
        conn.close()


def _read_sqlite(
    path: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], int, dict[str, int], dict[str, Any]]:
    db_path = os.path.join(path, SQLITE_FILE)
    if not os.path.exists(db_path):
        raise StoreFormatError(f"manifest declares SQLite format but {SQLITE_FILE} is missing")
    conn = _connect(path, create=False)
    try:
        integrity = conn.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise StoreFormatError(f"SQLite integrity check failed: {integrity!r}")
        records: dict[str, list[dict[str, Any]]] = {}
        actual_counts: dict[str, int] = {}
        for collection in COLLECTIONS:
            rows = conn.execute(
                "SELECT payload FROM records WHERE collection=? ORDER BY id", (collection,)
            ).fetchall()
            actual_counts[collection] = len(rows)
            try:
                records[collection] = [json.loads(row[0]) for row in rows]
            except (TypeError, json.JSONDecodeError) as exc:
                raise StoreFormatError(f"SQLite {collection} contains malformed JSON") from exc
        meta_rows = dict(conn.execute("SELECT key,payload FROM metadata").fetchall())
        state = json.loads(meta_rows.get("state", "{}"))
        generation = int(json.loads(meta_rows.get("generation", "0")))
        metadata_schema = int(json.loads(meta_rows.get("schema_version", "0")))
        store_info = json.loads(meta_rows.get("store_info", "{}"))
        store_id = str(json.loads(meta_rows.get("store_id", '""')))
        commit_id = str(json.loads(meta_rows.get("commit_id", '""')))
        if not isinstance(state, dict) or not isinstance(store_info, dict):
            raise StoreFormatError("SQLite metadata contains an invalid object")
        if generation < 1:
            raise StoreFormatError("SQLite generation recovery metadata must be positive")
        if metadata_schema != int(store_info.get("schema_version", 0)):
            raise StoreFormatError("SQLite schema recovery metadata is inconsistent")
        if not store_id or store_info.get("store_id") != store_id:
            raise StoreFormatError("SQLite store_id recovery metadata is missing or inconsistent")
        if not commit_id or store_info.get("commit_id") != commit_id:
            raise StoreFormatError("SQLite commit_id recovery metadata is missing or inconsistent")
        return records, state, generation, actual_counts, store_info
    except (TypeError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError) as exc:
        raise StoreFormatError("cannot read Engram SQLite store") from exc
    finally:
        conn.close()


def _write_manifest(path: str, manifest: dict[str, Any]) -> None:
    tmp = os.path.join(path, "manifest.json.tmp")
    manifest_path = os.path.join(path, "manifest.json")
    _require_regular(manifest_path, missing_ok=True)
    stale_tmp = _require_regular(tmp, missing_ok=True)
    if stale_tmp is not None:
        os.unlink(tmp)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp, flags, 0o600)
    except OSError as exc:
        raise StoreFormatError(f"cannot safely create manifest temporary file: {tmp}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _json_dump(manifest, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, manifest_path)
        _secure_file(manifest_path)
        dir_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_manifest(path: str) -> dict[str, Any] | None:
    manifest_path = os.path.join(path, "manifest.json")
    expected_stat = _require_regular(manifest_path, missing_ok=True)
    if expected_stat is None:
        return None
    try:
        fd = os.open(manifest_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise StoreFormatError("manifest.json must be a direct regular file")
            if (st.st_dev, st.st_ino) != (expected_stat.st_dev, expected_stat.st_ino):
                raise StoreFormatError("manifest.json changed while opening")
            manifest = json.load(fh)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StoreFormatError("manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise StoreFormatError("manifest.json must contain an object")
    return manifest


def _created_at(manifest: dict[str, Any] | None) -> float:
    try:
        return float((manifest or {}).get("created_at", now()))
    except (TypeError, ValueError):
        return now()


def _store_info(
    mem: Any,
    backend: str,
    created_at: float,
    *,
    store_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "format": "sqlite",
        "engram_version": __version__,
        "embedder_id": _embedder_id(mem.embedder),
        "embedding_dim": _embedder_dim(mem.embedder),
        "backend": backend,
        "store_id": store_id,
        "commit_id": uuid.uuid4().hex,
        "created_at": created_at,
        "updated_at": now(),
    }


def _manifest(store_info: dict[str, Any], generation: int, counts: dict[str, int]) -> dict[str, Any]:
    return {
        **store_info,
        "generation": generation,
        "counts": counts,
    }


def _remove_legacy_jsonl(path: str) -> None:
    """Best-effort overwrite then unlink of schema-v1 plaintext collection files.

    Overwriting cannot promise physical erasure on copy-on-write filesystems or SSDs; the important
    application invariant is that a completed migration leaves no readable legacy copy behind.
    """
    for name in COLLECTIONS:
        file_path = os.path.join(path, f"{name}.jsonl")
        file_stat = _require_regular(file_path, missing_ok=True)
        if file_stat is None:
            continue
        size = file_stat.st_size
        try:
            flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(file_path, flags)
            with os.fdopen(fd, "r+b", buffering=0) as fh:
                opened = os.fstat(fh.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise StoreFormatError(
                        f"legacy collection must be a direct regular file: {file_path}"
                    )
                if (opened.st_dev, opened.st_ino) != (file_stat.st_dev, file_stat.st_ino):
                    raise StoreFormatError(f"legacy collection changed while opening: {file_path}")
                remaining = size
                zeros = b"\0" * min(1024 * 1024, max(1, size))
                while remaining:
                    chunk = min(remaining, len(zeros))
                    fh.write(zeros[:chunk])
                    remaining -= chunk
                fh.flush()
                os.fsync(fh.fileno())
            os.unlink(file_path)
        except OSError as exc:
            raise PersistenceError(f"could not remove migrated plaintext file: {file_path}") from exc


def _validate_compatibility(mem: Any, manifest: dict[str, Any]) -> None:
    try:
        schema = int(manifest.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise StoreFormatError("manifest schema_version must be an integer") from exc
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


def _validate_artifacts(path: str) -> None:
    """Validate every persistence-owned pathname before reading, opening, replacing, or cleaning it."""
    names = [
        "manifest.json",
        "manifest.json.tmp",
        SQLITE_FILE,
        f"{SQLITE_FILE}-journal",
        f"{SQLITE_FILE}-wal",
        f"{SQLITE_FILE}-shm",
    ]
    names.extend(f"{name}.jsonl" for name in COLLECTIONS)
    for name in names:
        _require_regular(os.path.join(path, name), missing_ok=True)


def _database_exists(path: str) -> bool:
    return _require_regular(os.path.join(path, SQLITE_FILE), missing_ok=True) is not None


def _legacy_artifacts_exist(path: str) -> bool:
    return any(
        _require_regular(os.path.join(path, f"{name}.jsonl"), missing_ok=True) is not None
        for name in COLLECTIONS
    )


def _bind_memory(mem: Any, path: str, generation: int, store_info: dict[str, Any]) -> None:
    mem._persist_generation = generation
    mem._persist_store_id = str(store_info["store_id"])
    mem._persist_commit_id = str(store_info["commit_id"])
    mem._persist_store_path = os.path.realpath(path)


def _canonical_snapshot(
    mem: Any,
    path: str,
    manifest: dict[str, Any] | None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], int, dict[str, int], dict[str, Any]]:
    """Read canonical SQLite and repair only a provably lagging/missing coordination manifest."""
    records, state, generation, actual_counts, store_info = _read_sqlite(path)
    _validate_compatibility(mem, store_info)
    store_id = str(store_info.get("store_id") or "")
    commit_id = str(store_info.get("commit_id") or "")
    if not store_id or not commit_id:
        raise StoreFormatError("canonical SQLite recovery identity is incomplete")
    if store_info.get("format") != "sqlite" or int(store_info.get("schema_version", 0)) < 2:
        raise StoreFormatError("canonical SQLite recovery format is invalid")
    if manifest is None:
        _write_manifest(path, _manifest(store_info, generation, actual_counts))
        return records, state, generation, actual_counts, store_info

    _validate_compatibility(mem, manifest)
    if manifest.get("format") != "sqlite" or int(manifest.get("schema_version", 0)) < 2:
        raise StoreFormatError("canonical SQLite database is paired with a non-canonical manifest")
    manifest_store_id = str(manifest.get("store_id") or "")
    if not manifest_store_id or manifest_store_id != store_id:
        raise StoreFormatError("manifest and SQLite store_id do not match")
    try:
        manifest_generation = int(manifest.get("generation", 0))
    except (TypeError, ValueError) as exc:
        raise StoreFormatError("manifest generation must be an integer") from exc
    if generation < manifest_generation:
        raise StoreFormatError(
            f"SQLite generation={generation} is older than manifest generation={manifest_generation}"
        )
    if generation == manifest_generation:
        counts = manifest.get("counts") or {}
        expected_counts = {name: _manifest_count(counts, name) for name in COLLECTIONS}
        if actual_counts != expected_counts:
            raise StoreFormatError("SQLite record counts do not match the committed manifest")
        if str(manifest.get("commit_id") or "") != commit_id:
            raise StoreFormatError("manifest and SQLite commit_id do not match")
        for key in (
            "schema_version",
            "format",
            "engram_version",
            "embedder_id",
            "embedding_dim",
            "backend",
            "created_at",
        ):
            if manifest.get(key) != store_info.get(key):
                raise StoreFormatError(f"manifest and SQLite {key} do not match")
    else:
        # Same random store identity + newer DB generation proves an interrupted manifest publication.
        _write_manifest(path, _manifest(store_info, generation, actual_counts))
    return records, state, generation, actual_counts, store_info


def save_memory(mem: Any, path: str, backend: str = "durable") -> None:
    """Incrementally persist a Memory instance to a schema-versioned SQLite directory."""
    with _lock(path):
        _validate_artifacts(path)
        old_manifest = _read_manifest(path)
        database_exists = _database_exists(path)
        canonical = None
        if database_exists and (
            old_manifest is None
            or old_manifest.get("format") == "sqlite"
            or int(old_manifest.get("schema_version", 0)) >= 2
        ):
            canonical = _canonical_snapshot(mem, path, old_manifest)
            old_manifest = _read_manifest(path)

        bound_generation = getattr(mem, "_persist_generation", None)
        bound_store_id = getattr(mem, "_persist_store_id", None)
        store_exists = canonical is not None or old_manifest is not None or _legacy_artifacts_exist(path)
        if store_exists and bound_generation is None:
            raise ConcurrentWriteError(
                "unbound Memory cannot overwrite an existing store; use Memory.open(path) first"
            )
        if canonical is None and store_exists:
            raise ConcurrentWriteError("Memory is not bound to this existing store")
        if canonical is not None:
            _, _, current_generation, _, current_info = canonical
            if bound_store_id != current_info["store_id"]:
                raise ConcurrentWriteError("Memory is bound to a different canonical store")
            if bound_generation != current_generation:
                raise ConcurrentWriteError(
                    f"stale Memory generation={bound_generation}; canonical generation={current_generation}"
                )
            expected_generation = current_generation
            store_id = str(current_info["store_id"])
            created_at = _created_at(old_manifest)
        else:
            # Empty destination: this is either a first save or an explicit safe save-as/fork.
            expected_generation = None
            store_id = uuid.uuid4().hex
            created_at = now()
        records, state = _snapshot(mem)
        store_info = _store_info(mem, backend, created_at, store_id=store_id)
        generation, counts = _write_sqlite_snapshot(
            path,
            records,
            state,
            store_info,
            expected_generation=expected_generation,
            expected_store_id=store_id if expected_generation is not None else None,
        )
        # SQLite COMMIT is canonical. Bind immediately so the same instance can retry manifest publication.
        _bind_memory(mem, path, generation, store_info)
        _write_manifest(path, _manifest(store_info, generation, counts))
        _remove_legacy_jsonl(path)


def _read_legacy(path: str, manifest: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    counts = manifest.get("counts") or {}
    expected_counts = {name: _manifest_count(counts, name) for name in COLLECTIONS}
    records = {name: _read_jsonl(path, name, expected_counts[name]) for name in COLLECTIONS}
    state = manifest.get("state") or {}
    if not isinstance(state, dict):
        raise StoreFormatError("legacy manifest state must be an object")
    return records, state


def _restore(mem: Any, records: dict[str, list[dict[str, Any]]], state: dict[str, Any]) -> None:
    episodes = [_from_record(Episode, row) for row in records["episodes"]]
    facts = [(row.get("_store", "hot"), _from_record(Fact, row)) for row in records["facts"]]
    entities = [_from_record(Entity, row) for row in records["entities"]]
    relations = [_from_record(Relation, row) for row in records["relations"]]
    working_items = [_from_record(WorkingMemory, row) for row in records["working"]]
    conflict_items = [_from_record(Conflict, row) for row in records["conflicts"]]

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
    mem.working_mem = {item.id: item for item in working_items}
    mem.conflicts = {item.id: item for item in conflict_items}
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
    mem.twin_contract = TwinContract.from_dict(state.get("twin_contract") or {})
    history_payload = state.get("twin_contract_history")
    if history_payload:
        history = [TwinContract.from_dict(item) for item in history_payload]
        versions = [item.version for item in history]
        expected_versions = list(range(1, len(history) + 1))
        if versions != expected_versions:
            raise StoreFormatError(
                "twin contract history must start at version 1 and contain every revision"
            )
        if any(
            later.updated_at <= earlier.updated_at
            for earlier, later in zip(history, history[1:])
        ):
            raise StoreFormatError("twin contract history timestamps must be strictly increasing")
        if history[-1].to_dict() != mem.twin_contract.to_dict():
            raise StoreFormatError("current twin contract does not match its history")
        mem.twin_contract_history = history
    else:
        if mem.twin_contract.version != 1:
            raise StoreFormatError("versioned twin contract is missing its revision history")
        mem.twin_contract_history = [mem.twin_contract]
    mem.capability_registry = CapabilityRegistry.from_dict(
        state.get("capability_registry") or {}
    )
    grant_ids = [item.id for item in mem.capability_registry.grants]
    if len(grant_ids) != len(set(grant_ids)):
        raise StoreFormatError("capability registry contains duplicate grant ids")
    mem.twin_decisions = {}
    for item in state.get("twin_decisions") or ():
        request = ActionRequest.from_dict(item["request"])
        decision = ActionDecision.from_dict(item["decision"])
        if decision.request_id != request.id:
            raise StoreFormatError("twin decision does not match its action request")
        if decision.id in mem.twin_decisions:
            raise StoreFormatError("twin decision audit contains duplicate decision ids")
        mem.twin_decisions[decision.id] = (request, decision)
    mem.twin_actions = []
    action_ids: set[str] = set()
    recorded_decisions: set[str] = set()
    for item in state.get("twin_actions") or ():
        record = ActionRecord.from_dict(item)
        if record.id in action_ids:
            raise StoreFormatError("twin action audit contains duplicate action record ids")
        canonical = mem.twin_decisions.get(record.decision.id)
        if canonical is None:
            raise StoreFormatError("twin action audit references an unknown decision")
        request, decision = canonical
        if (
            record.request.to_dict() != request.to_dict()
            or record.decision.to_dict() != decision.to_dict()
        ):
            raise StoreFormatError("twin action audit does not match its canonical decision")
        if record.decision.id in recorded_decisions:
            raise StoreFormatError("twin action audit records one decision more than once")
        action_ids.add(record.id)
        recorded_decisions.add(record.decision.id)
        mem.twin_actions.append(record)


def load_memory(mem: Any, path: str) -> bool:
    """Load a durable store, migrating a valid schema-v1 JSONL snapshot on first open."""
    with _lock(path):
        # The first read happens after acquiring the lock. A concurrent first writer may have committed
        # SQLite while this opener waited, including the recoverable DB-committed/manifest-missing edge.
        _validate_artifacts(path)
        manifest = _read_manifest(path)
        if manifest is None:
            if not _database_exists(path):
                return False
            records, state, generation, _, store_info = _canonical_snapshot(mem, path, None)
            _restore(mem, records, state)
            _bind_memory(mem, path, generation, store_info)
            return True
        _validate_compatibility(mem, manifest)
        if manifest.get("format") == "sqlite" or int(manifest.get("schema_version", 0)) >= 2:
            records, state, generation, _, store_info = _canonical_snapshot(mem, path, manifest)
            _remove_legacy_jsonl(path)
        else:
            records, state = _read_legacy(path, manifest)
            if _database_exists(path):
                # A prior migration committed SQLite but stopped before publishing the v2 manifest.
                _, _, previous_generation, _, previous_info = _read_sqlite(path)
                expected_generation = previous_generation
                store_id = str(previous_info["store_id"])
            else:
                expected_generation = None
                store_id = uuid.uuid4().hex
            store_info = {
                **{k: v for k, v in manifest.items() if k not in {"state", "counts", "generation"}},
                "schema_version": SCHEMA_VERSION,
                "format": "sqlite",
                "engram_version": __version__,
                "store_id": store_id,
                "commit_id": uuid.uuid4().hex,
                "updated_at": now(),
            }
            generation, migrated_counts = _write_sqlite_snapshot(
                path,
                records,
                state,
                store_info,
                expected_generation=expected_generation,
                expected_store_id=store_id if expected_generation is not None else None,
            )
            _write_manifest(path, _manifest(store_info, generation, migrated_counts))
            _remove_legacy_jsonl(path)
        _restore(mem, records, state)
        _bind_memory(mem, path, generation, store_info)
    return True
