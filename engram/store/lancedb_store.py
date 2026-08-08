"""Optional LanceDB-backed VectorStore.

This module is intentionally not imported by the default path. LanceDB is a scale backend, not a core
dependency; zero-setup users should never pay its import or install cost.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import is_dataclass
from typing import Any, Optional

from ..types import Episode, Fact
from ..util import cosine
from .base import Predicate, VectorStore
from .persist import _from_record, _record

_TYPES = {"Episode": Episode, "Fact": Fact}
_OWNER_FILE = ".engram-lancedb.json"
_OWNER_SCHEMA = 1
_TABLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


class LanceDBPathError(ValueError):
    """The configured LanceDB root is not safe to adopt or reuse."""


def _path_binding(path: str) -> str:
    canonical = os.path.realpath(path)
    return "path:" + hashlib.sha256(os.fsencode(canonical)).hexdigest()


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _restrict_directory(directory_fd: int, label: str) -> None:
    try:
        os.fchmod(directory_fd, 0o700)
    except OSError as exc:
        raise LanceDBPathError(f"{label} could not be restricted to mode 0700") from exc
    if stat.S_IMODE(os.fstat(directory_fd).st_mode) != 0o700:
        raise LanceDBPathError(f"{label} could not be restricted to mode 0700")


def _open_secure_root(path: str) -> tuple[str, int]:
    """Create/validate the final Lance root and keep a no-follow directory fd as an anchor.

    `data_path` is an administrator-controlled location, so this validates the final component rather
    than rejecting platform paths with a symlinked ancestor (for example macOS' `/var`). The final root
    itself must be an owner-controlled real directory and is normalized to mode 0700.
    """

    try:
        raw_path = os.fspath(path)
    except TypeError as exc:
        raise LanceDBPathError("LanceDB path must be a filesystem path") from exc
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise LanceDBPathError("LanceDB path must not be empty")
    root = os.path.abspath(os.path.expanduser(raw_path))
    if not os.path.lexists(root):
        try:
            os.makedirs(root, mode=0o700, exist_ok=False)
        except FileExistsError:
            # A concurrent creator won the race; the lstat/open checks below still decide whether it is
            # safe. In particular, a symlink planted at the final component is never followed.
            pass
        except OSError as exc:
            raise LanceDBPathError(f"cannot create LanceDB directory: {root}") from exc

    try:
        before = os.lstat(root)
    except OSError as exc:
        raise LanceDBPathError(f"cannot inspect LanceDB directory: {root}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise LanceDBPathError("LanceDB root must not be a symlink")
    if not stat.S_ISDIR(before.st_mode):
        raise LanceDBPathError("LanceDB root must be a directory")
    if hasattr(os, "geteuid") and before.st_uid != os.geteuid():
        raise LanceDBPathError("LanceDB root must be owned by the current user")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise LanceDBPathError("cannot securely open LanceDB root") from exc
    try:
        opened = os.fstat(root_fd)
        if not stat.S_ISDIR(opened.st_mode) or not _same_file(before, opened):
            raise LanceDBPathError("LanceDB root changed while it was being opened")
        if hasattr(os, "geteuid") and opened.st_uid != os.geteuid():
            raise LanceDBPathError("LanceDB root must be owned by the current user")
        # Do not chmod an accidentally broad user-supplied directory (home, cwd, a documents folder)
        # before proving it is empty or already marked as Engram-owned.
        entries = os.listdir(root_fd)
        if entries and _OWNER_FILE not in entries:
            raise LanceDBPathError(
                "refusing to adopt a non-empty unmarked LanceDB directory; use a fresh data_path"
            )
        # Empty roots are safe to claim immediately. Marked roots are chmodded only after the marker's
        # schema/binding validates, avoiding mutation of a broad directory containing a coincidental file.
        if not entries:
            _restrict_directory(root_fd, "LanceDB root")
    except Exception:
        os.close(root_fd)
        raise
    return root, root_fd


def _open_secure_child(parent_fd: int, name: str, label: str) -> int:
    """Open one direct child without following a symlink at that path component."""

    if not name or name in {".", ".."} or os.sep in name or (os.altsep and os.altsep in name):
        raise LanceDBPathError(f"unsafe {label} directory name")
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise LanceDBPathError(f"cannot create {label} directory") from exc
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise LanceDBPathError(f"cannot inspect {label} directory") from exc
    if stat.S_ISLNK(before.st_mode):
        raise LanceDBPathError(f"{label} directory must not be a symlink")
    if not stat.S_ISDIR(before.st_mode):
        raise LanceDBPathError(f"{label} path must be a directory")
    if hasattr(os, "geteuid") and before.st_uid != os.geteuid():
        raise LanceDBPathError(f"{label} directory must be owned by the current user")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        child_fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise LanceDBPathError(f"cannot securely open {label} directory") from exc
    try:
        opened = os.fstat(child_fd)
        if not stat.S_ISDIR(opened.st_mode) or not _same_file(before, opened):
            raise LanceDBPathError(f"{label} directory changed while it was being opened")
        if hasattr(os, "geteuid") and opened.st_uid != os.geteuid():
            raise LanceDBPathError(f"{label} directory must be owned by the current user")
        _restrict_directory(child_fd, f"{label} directory")
    except Exception:
        os.close(child_fd)
        raise
    return child_fd


def _read_owner(root_fd: int) -> dict[str, Any] | None:
    try:
        metadata = os.stat(_OWNER_FILE, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LanceDBPathError("cannot inspect LanceDB ownership marker") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise LanceDBPathError("LanceDB ownership marker must be a single-link regular file")
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise LanceDBPathError("LanceDB ownership marker must be owned by the current user")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        marker_fd = os.open(_OWNER_FILE, flags, dir_fd=root_fd)
    except OSError as exc:
        raise LanceDBPathError("cannot securely open LanceDB ownership marker") from exc
    try:
        opened = os.fstat(marker_fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or not _same_file(metadata, opened):
            raise LanceDBPathError("LanceDB ownership marker changed while it was being opened")
        if opened.st_size > 4096:
            raise LanceDBPathError("LanceDB ownership marker is unexpectedly large")
        os.fchmod(marker_fd, 0o600)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(marker_fd, remaining)
            if not chunk:
                raise LanceDBPathError("LanceDB ownership marker was truncated while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(marker_fd)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LanceDBPathError("LanceDB ownership marker is malformed") from exc
    if not isinstance(value, dict):
        raise LanceDBPathError("LanceDB ownership marker must be an object")
    return value


def _write_owner(root_fd: int, binding_id: str) -> None:
    payload = json.dumps(
        {"schema": _OWNER_SCHEMA, "binding_id": binding_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        marker_fd = os.open(_OWNER_FILE, flags, 0o600, dir_fd=root_fd)
    except OSError as exc:
        raise LanceDBPathError("cannot create LanceDB ownership marker") from exc
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(marker_fd, remaining)
            if written <= 0:
                raise LanceDBPathError("could not finish writing LanceDB ownership marker")
            remaining = remaining[written:]
        os.fsync(marker_fd)
    finally:
        os.close(marker_fd)
    os.fsync(root_fd)


def _bind_root(root_fd: int, binding_id: str) -> None:
    marker = _read_owner(root_fd)
    if marker is None:
        # Silently adopting a pre-existing database recreates the exact cross-snapshot contamination this
        # marker prevents. Empty roots are safe to claim; legacy roots must be migrated via a fresh path.
        if os.listdir(root_fd):
            # Another constructor can publish the marker between the first read and directory listing.
            # Re-read once; foreign non-empty roots still have no valid marker and remain fail-closed.
            marker = _read_owner(root_fd)
            if marker is None:
                raise LanceDBPathError(
                    "refusing to adopt a non-empty unmarked LanceDB directory; use a fresh data_path"
                )
        else:
            try:
                _write_owner(root_fd, binding_id)
                return
            except LanceDBPathError:
                # Another constructor may have claimed the empty root after our read. Only accept that
                # race when the now-published marker proves it chose this exact namespace.
                marker = _read_owner(root_fd)
                if marker is None:
                    raise
    if marker.get("schema") != _OWNER_SCHEMA:
        raise LanceDBPathError("unsupported LanceDB ownership marker schema")
    if marker.get("binding_id") != binding_id:
        raise LanceDBPathError("LanceDB directory is bound to a different Engram namespace")


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _encode_payload(payload: Any) -> str:
    if is_dataclass(payload):
        return json.dumps(
            {"type": payload.__class__.__name__, "data": _record(payload)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return json.dumps({"type": "json", "data": payload}, ensure_ascii=False, sort_keys=True)


def _decode_payload(raw: str) -> Any:
    obj = json.loads(raw)
    typ = obj.get("type")
    if typ in _TYPES:
        return _from_record(_TYPES[typ], obj.get("data") or {})
    return obj.get("data")


class LanceDBVectorStore(VectorStore):
    """A persistent VectorStore using one LanceDB table per logical Engram index."""

    def __init__(
        self,
        path: str,
        table: str = "vectors",
        *,
        binding_id: str | None = None,
        namespace_base: str | None = None,
    ) -> None:
        if not _TABLE_NAME.fullmatch(table):
            raise LanceDBPathError("LanceDB table name contains unsafe characters")
        import lancedb  # noqa: PLC0415 - optional dependency, lazy by design.

        self._root_fd = None
        self._base_fd = None
        try:
            if namespace_base is not None:
                self.base_path, self._base_fd = _open_secure_root(namespace_base)
                _bind_root(self._base_fd, "base:" + _path_binding(self.base_path).removeprefix("path:"))
                _restrict_directory(self._base_fd, "LanceDB data_path base")
                candidate = os.path.abspath(os.path.expanduser(os.fspath(path)))
                namespace_parent = os.path.join(self.base_path, "namespaces")
                if os.path.dirname(candidate) != namespace_parent:
                    raise LanceDBPathError(
                        "LanceDB namespace root must be a direct child of data_path/namespaces"
                    )
                namespaces_fd = _open_secure_child(self._base_fd, "namespaces", "LanceDB namespaces")
                try:
                    self._root_fd = _open_secure_child(
                        namespaces_fd,
                        os.path.basename(candidate),
                        "LanceDB namespace",
                    )
                finally:
                    os.close(namespaces_fd)
                self.path = candidate
            else:
                self.path, self._root_fd = _open_secure_root(path)
            self.table_name = table
            binding = binding_id or _path_binding(self.path)
            if not isinstance(binding, str) or not binding or len(binding) > 256:
                raise LanceDBPathError(
                    "LanceDB binding_id must be a non-empty string of at most 256 characters"
                )
            _bind_root(self._root_fd, binding)
            _restrict_directory(self._root_fd, "LanceDB root")
            self._db = lancedb.connect(self.path)
            current = os.lstat(self.path)
            if not _same_file(current, os.fstat(self._root_fd)):
                raise LanceDBPathError("LanceDB root changed while the database was connecting")
            self._table = None
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for name in ("_root_fd", "_base_fd"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def _open(self):
        if self._root_fd is None:
            raise RuntimeError("LanceDBVectorStore is closed")
        if self._table is not None:
            return self._table
        tables = self._db.list_tables()
        names = getattr(tables, "tables", tables)
        if self.table_name in names:
            self._table = self._db.open_table(self.table_name)
        return self._table

    def _ensure(self, vector: list[float], key: str, payload: Any):
        table = self._open()
        if table is None:
            self._table = self._db.create_table(
                self.table_name,
                data=[{"key": key, "vector": vector, "payload": _encode_payload(payload)}],
                mode="overwrite",
            )
            return None
        return table

    def upsert(self, key: str, vector: list[float], payload: Any) -> None:
        table = self._ensure(vector, key, payload)
        if table is None:
            return
        table.delete(f"key = {_quote(key)}")
        table.add([{"key": key, "vector": vector, "payload": _encode_payload(payload)}])

    def search(
        self, vector: list[float], top_k: int, where: Optional[Predicate] = None
    ) -> list[tuple[float, Any]]:
        table = self._open()
        if table is None or top_k <= 0:
            return []
        # Python predicates are part of the VectorStore contract, so filtered searches must consider every
        # row before ranking. Otherwise a tenant/user filter can miss valid hits hidden beyond LanceDB's
        # nearest unfiltered rows.
        rows = table.to_arrow().to_pylist() if where is not None else table.search(vector).limit(top_k).to_list()
        scored: list[tuple[float, Any]] = []
        for row in rows:
            payload = _decode_payload(row["payload"])
            if where is not None and not where(payload):
                continue
            scored.append((cosine(vector, row["vector"]), payload))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def get(self, key: str) -> Any | None:
        table = self._open()
        if table is None:
            return None
        rows = table.to_arrow().to_pylist()
        for row in rows:
            if row.get("key") == key:
                return _decode_payload(row["payload"])
        return None

    def delete(self, key: str) -> None:
        """Remove a row from the live table.

        LanceDB deletion is logical. It does not prove that old fragments, filesystem snapshots,
        backups, cloud-sync history, or SSD flash-translation layers no longer contain prior bytes.
        """
        table = self._open()
        if table is not None:
            table.delete(f"key = {_quote(key)}")

    def values(self) -> list[Any]:
        table = self._open()
        if table is None:
            return []
        return [_decode_payload(row["payload"]) for row in table.to_arrow().to_pylist()]
