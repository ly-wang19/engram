"""Optional LanceDB-backed VectorStore.

This module is intentionally not imported by the default path. LanceDB is a scale backend, not a core
dependency; zero-setup users should never pay its import or install cost.
"""
from __future__ import annotations

import json
import os
from dataclasses import is_dataclass
from typing import Any, Optional

from ..types import Episode, Fact
from ..util import cosine
from .base import Predicate, VectorStore
from .persist import _from_record, _record

_TYPES = {"Episode": Episode, "Fact": Fact}


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

    def __init__(self, path: str, table: str = "vectors") -> None:
        import lancedb  # noqa: PLC0415 - optional dependency, lazy by design.

        self.path = os.path.expanduser(path)
        self.table_name = table
        os.makedirs(self.path, exist_ok=True)
        self._db = lancedb.connect(self.path)
        self._table = None

    def _open(self):
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
        table = self._open()
        if table is not None:
            table.delete(f"key = {_quote(key)}")

    def values(self) -> list[Any]:
        table = self._open()
        if table is None:
            return []
        return [_decode_payload(row["payload"]) for row in table.to_arrow().to_pylist()]
