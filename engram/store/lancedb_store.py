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

# Tenant id lifted out of the JSON payload into its own column. Without it every multi-tenant search
# degrades to a table scan, because a Python predicate is the only way to express "this user's facts"
# and LanceDB cannot see inside one.
_TENANT_COL = "user_id"


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
        self._tenant_col: Optional[bool] = None  # resolved lazily against the real schema

    def _open(self):
        if self._table is not None:
            return self._table
        tables = self._db.list_tables()
        names = getattr(tables, "tables", tables)
        if self.table_name in names:
            self._table = self._db.open_table(self.table_name)
        return self._table

    def _row(self, key: str, vector: list[float], payload: Any) -> dict[str, Any]:
        """One table row. `user_id` is promoted out of the opaque payload into a real column so the
        tenant filter can be pushed into LanceDB's index rather than applied in Python after a scan."""
        return {
            "key": key,
            "vector": vector,
            "payload": _encode_payload(payload),
            _TENANT_COL: getattr(payload, "user_id", None) or "",
        }

    def _has_tenant_column(self, table) -> bool:
        """Tables written before the tenant column existed still load and still work — they just cannot
        prefilter. Detect rather than assume, so an upgrade never corrupts or rejects existing data."""
        if self._tenant_col is None:
            try:
                self._tenant_col = _TENANT_COL in set(table.schema.names)
            except AttributeError:  # pragma: no cover - older/newer client without .schema.names
                self._tenant_col = False
        return self._tenant_col

    def _ensure(self, vector: list[float], key: str, payload: Any):
        table = self._open()
        if table is None:
            self._table = self._db.create_table(
                self.table_name, data=[self._row(key, vector, payload)], mode="overwrite"
            )
            self._tenant_col = True
            return None
        return table

    def upsert(self, key: str, vector: list[float], payload: Any) -> None:
        table = self._ensure(vector, key, payload)
        if table is None:
            return
        table.delete(f"key = {_quote(key)}")
        row = self._row(key, vector, payload)
        if not self._has_tenant_column(table):
            row.pop(_TENANT_COL)  # legacy table: adding an unknown column would be a schema violation
        table.add([row])

    def search(
        self,
        vector: list[float],
        top_k: int,
        where: Optional[Predicate] = None,
        *,
        user_id: Optional[str] = None,
    ) -> list[tuple[float, Any]]:
        table = self._open()
        if table is None or top_k <= 0:
            return []

        if where is None and user_id is not None and self._has_tenant_column(table):
            # The whole point of the tenant column: a prefiltered ANN query, so LanceDB narrows to this
            # tenant inside its own index and returns top_k without materialising the table.
            query = table.search(vector).where(f"{_TENANT_COL} = {_quote(user_id)}", prefilter=True)
            rows = query.limit(top_k).to_list()
        elif where is None and user_id is None:
            rows = table.search(vector).limit(top_k).to_list()
        else:
            # An arbitrary Python predicate is opaque to the backend, so every row must be considered
            # before ranking — otherwise a filter can miss valid hits hidden beyond the nearest
            # unfiltered rows. Same for a tenant filter on a legacy table with no column to filter on.
            rows = table.to_arrow().to_pylist()

        scored: list[tuple[float, Any]] = []
        for row in rows:
            payload = _decode_payload(row["payload"])
            if where is not None and not where(payload):
                continue
            if user_id is not None and getattr(payload, "user_id", None) != user_id:
                continue
            scored.append((cosine(vector, row["vector"]), payload))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def get(self, key: str) -> Any | None:
        table = self._open()
        if table is None:
            return None
        # A filter-only query (no vector) so the key predicate runs inside LanceDB. Materialising the
        # table and scanning it in Python made a single-key read cost the whole store, which turns any
        # id-at-a-time access pattern quadratic.
        rows = table.search().where(f"key = {_quote(key)}").limit(1).to_list()
        if not rows:
            return None
        return _decode_payload(rows[0]["payload"])

    def delete(self, key: str) -> None:
        table = self._open()
        if table is not None:
            table.delete(f"key = {_quote(key)}")

    def values(self) -> list[Any]:
        table = self._open()
        if table is None:
            return []
        return [_decode_payload(row["payload"]) for row in table.to_arrow().to_pylist()]
