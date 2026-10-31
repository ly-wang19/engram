"""Small transactional product state beside the existing memory snapshots.

SQLite is in the standard library. Each operation owns a connection, allowing HTTP workers and
separate MCP processes to share status without putting user content in a global in-memory cache.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


class ManagementStore:
    def __init__(self, data_dir: str) -> None:
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / ".management.sqlite3"
        if self.path.is_symlink():
            raise ValueError("management database must not be a symlink")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS records (
                namespace TEXT NOT NULL, collection TEXT NOT NULL, key TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(namespace, collection, key))""")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _encode(value: dict) -> str:
        if not isinstance(value, dict):
            raise TypeError("management records must be JSON objects")
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    def get(self, namespace: str, collection: str, key: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT payload FROM records WHERE namespace=? AND collection=? AND key=?",
                             (namespace, collection, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, namespace: str, collection: str, key: str, value: dict) -> dict:
        encoded = self._encode(value)
        with self._connection() as db:
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?)",
                       (namespace, collection, key, encoded))
        return json.loads(encoded)

    def delete(self, namespace: str, collection: str, key: str) -> bool:
        with self._connection() as db:
            return db.execute("DELETE FROM records WHERE namespace=? AND collection=? AND key=?",
                              (namespace, collection, key)).rowcount > 0

    def list(self, namespace: str, collection: str) -> list[dict]:
        with self._connection() as db:
            rows = db.execute("SELECT payload FROM records WHERE namespace=? AND collection=? ORDER BY key",
                              (namespace, collection)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def clear_namespace(self, namespace: str) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM records WHERE namespace=?", (namespace,))

    def update(self, namespace: str, collection: str, key: str,
               fn: Callable[[dict | None], dict | None]) -> dict | None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM records WHERE namespace=? AND collection=? AND key=?",
                             (namespace, collection, key)).fetchone()
            value = fn(json.loads(row[0]) if row else None)
            if value is None:
                db.execute("DELETE FROM records WHERE namespace=? AND collection=? AND key=?",
                           (namespace, collection, key))
                return None
            encoded = self._encode(value)
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?)",
                       (namespace, collection, key, encoded))
            return json.loads(encoded)

    def scan(self, collection: str) -> list[tuple[str, str, dict]]:
        """Internal recovery only; never exposed through a tenant HTTP endpoint."""
        with self._connection() as db:
            rows = db.execute("SELECT namespace, key, payload FROM records WHERE collection=? ORDER BY namespace, key",
                              (collection,)).fetchall()
        return [(namespace, key, json.loads(payload)) for namespace, key, payload in rows]
