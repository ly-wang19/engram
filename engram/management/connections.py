"""Tenant-scoped ingestion observations, including failures that produced no episodes."""
from __future__ import annotations

import re
import time
import uuid


def source_name(value: str) -> str:
    value = str(value).strip().lower()
    return value if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,79}", value) else "other"


def session_source(session_id: str) -> str:
    prefix = str(session_id).split(":", 1)[0].lower()
    return prefix if prefix in {"codex", "claude", "claude-code", "cursor", "console", "openai"} else "manual"


class ConnectionTracker:
    def __init__(self, service) -> None:
        self.service = service
        self.store = service.management_store

    def record(self, user: str, source: str, status: str, operation: str,
               session_id: str | None = None, error: str | None = None, counts: dict | None = None) -> dict:
        source = source_name(source)
        if status not in {"succeeded", "failed", "stored", "deferred"}:
            raise ValueError("unknown connection status")
        safe_error = error if error and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error) else None
        event = {"id": uuid.uuid4().hex, "source": source, "status": status,
                 "operation": source_name(operation), "session_id": session_id,
                 "at": time.time(), "error": safe_error,
                 "counts": {k: int(v) for k, v in (counts or {}).items()
                            if k in {"episodes", "facts_added", "skipped"} and isinstance(v, int)}}
        self.store.put(user, "connection_events", event["id"], event)
        def update(old):
            old = old or {"source": source, "attempts": 0, "failures": 0}
            return {**old, "attempts": old["attempts"] + 1,
                    "failures": old["failures"] + int(status == "failed"),
                    "last_event_at": event["at"], "status": status, "last_error": safe_error,
                    "last_success_at": event["at"] if status != "failed" else old.get("last_success_at")}
        self.store.update(user, "connections", source, update)
        events = sorted(self.store.list(user, "connection_events"), key=lambda row: row["at"], reverse=True)
        for expired in events[500:]:
            self.store.delete(user, "connection_events", expired["id"])
        return event

    def index(self, user: str) -> dict:
        rows = {row["source"]: row for row in self.store.list(user, "connections")}
        mem = self.service.get(user)
        canonical = mem.resolver.resolve(user)
        session_sets = {}
        for ep in mem.episodes_doc.values():
            if ep.user_id != canonical:
                continue
            source = source_name(ep.metadata.get("source") or session_source(ep.session_id))
            row = rows.setdefault(source, {"source": source, "attempts": 0, "failures": 0,
                                           "status": "observed", "last_error": None,
                                           "last_event_at": None, "last_success_at": None})
            row["episodes"] = row.get("episodes", 0) + 1
            row["last_stored_at"] = max(row.get("last_stored_at", 0), ep.ingested_at)
            session_sets.setdefault(source, set()).add(ep.session_id)
        for source, row in rows.items():
            row["episodes"] = row.get("episodes", 0)
            row["sessions"] = len(session_sets.get(source, set()))
        return {"sources": sorted(rows.values(), key=lambda row: row["last_event_at"] or row.get("last_stored_at", 0), reverse=True),
                "observation_scope": "server_ingestion_and_client_reports", "event_retention": 500}

    def events(self, user: str, source: str | None = None, offset: int = 0, limit: int = 30) -> dict:
        rows = self.store.list(user, "connection_events")
        if source:
            rows = [row for row in rows if row["source"] == source]
        rows.sort(key=lambda row: (row["at"], row["id"]), reverse=True)
        return {"items": rows[offset:offset + limit], "total": len(rows), "offset": offset,
                "limit": limit, "has_more": offset + limit < len(rows)}
