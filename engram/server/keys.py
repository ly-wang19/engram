"""Runtime-issued API keys, stored hashed.

The server authenticated only through a static `ENGRAM_API_KEYS` env map — edit and restart to add a
tenant — or open mode, where anyone is a tenant. A hosted deployment needs to mint keys while running,
revoke them immediately, and never hold the secret in a form that a leaked file would expose.

  * a key is minted as `sk-engram-<random>` and returned exactly once;
  * only its SHA-256 digest is persisted, so the file cannot be replayed as credentials;
  * revocation drops the digest from the lookup index and takes effect on the next request.

State is one JSON file next to the data dir — inspectable, and not a pickle. It is written atomically and
with owner-only permissions, because it lists which tenants exist even though it holds no secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import threading
import time
from typing import Optional

__all__ = ["KeyStore", "KeyStoreError"]

KEY_PREFIX = "sk-engram-"


class KeyStoreError(RuntimeError):
    """The key file exists but could not be read. Deliberately fatal — see KeyStore._load."""


def _digest(token: str) -> str:
    """Only this is persisted. A leaked key file must not be replayable as credentials."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class KeyStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}  # key_id -> record, including the digest, never the secret
        self._by_digest: dict[str, str] = {}  # digest -> key_id, live keys only
        self._load()

    def _load(self) -> None:
        """Read the store, or fail loudly.

        A corrupt file must not be treated as an empty one. Starting empty would silently reject every
        previously issued key, and then the first `issue()` would rewrite the file and destroy the
        records that were merely unreadable. Refusing to start is recoverable; overwriting is not.
        """
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as exc:
            raise KeyStoreError(f"cannot read API key store at {self.path}: {exc}") from exc
        except ValueError as exc:
            raise KeyStoreError(
                f"API key store at {self.path} is not valid JSON; refusing to start rather than "
                "overwrite it — restore it from backup or move it aside"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("keys", []), list):
            raise KeyStoreError(f"API key store at {self.path} has an unexpected shape")

        for rec in data["keys"]:
            if not isinstance(rec, dict) or "id" not in rec or "hash" not in rec:
                raise KeyStoreError(f"API key store at {self.path} has a malformed record")
            self._records[rec["id"]] = rec
            if not rec.get("revoked"):
                self._by_digest[rec["hash"]] = rec["id"]

    def _save(self) -> None:
        """Atomic replace, owner-only. Caller holds the lock."""
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"keys": list(self._records.values())}, fh, ensure_ascii=False, indent=2)
        # Set the mode before the swap so the file is never briefly world-readable. It holds no secrets,
        # but it does enumerate the tenants on this deployment.
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self.path)

    def issue(self, user: str, label: str = "") -> dict:
        """Mint a key for `user`. The plaintext is in the return value and is never stored."""
        if not user or not user.strip():
            raise ValueError("a key must belong to a tenant")
        token = KEY_PREFIX + secrets.token_hex(24)
        record = {
            "id": "key_" + secrets.token_hex(8),
            "user": user.strip(),
            "label": label,
            "hash": _digest(token),
            "created_at": time.time(),
            "revoked": False,
            "last_used_at": None,
        }
        with self._lock:
            self._records[record["id"]] = record
            self._by_digest[record["hash"]] = record["id"]
            self._save()
        issued = self._public(record)
        issued["key"] = token  # shown once; there is no way to recover it later
        return issued

    def resolve(self, token: str) -> Optional[str]:
        """The tenant a presented token belongs to, or None if unknown or revoked."""
        if not token:
            return None
        with self._lock:
            key_id = self._by_digest.get(_digest(token))
            if key_id is None:
                return None
            record = self._records.get(key_id)
            if record is None or record.get("revoked"):
                return None
            # In memory only: flushing on every request would turn each read into a file write.
            record["last_used_at"] = time.time()
            return record["user"]

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            record = self._records.get(key_id)
            if record is None or record.get("revoked"):
                return False
            record["revoked"] = True
            self._by_digest.pop(record["hash"], None)
            self._save()
            return True

    def list(self, user: Optional[str] = None) -> list[dict]:
        """Key records, newest first. Never includes the secret or its digest."""
        with self._lock:
            records = [
                self._public(rec)
                for rec in self._records.values()
                if user is None or rec["user"] == user
            ]
        return sorted(records, key=lambda rec: rec["created_at"], reverse=True)

    @staticmethod
    def _public(record: dict) -> dict:
        """Strip the digest. Publishing it would let anyone verify a guessed key offline."""
        return {k: v for k, v in record.items() if k != "hash"}
