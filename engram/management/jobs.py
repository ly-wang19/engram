"""Durable session distillation jobs, shared by synchronous and asynchronous clients.

Queue transitions are SQLite transactions. A process-held file lock fences execution and lets a
new process distinguish a live writer from an interrupted one without a guessed timeout.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


STATES = frozenset({"pending", "running", "succeeded", "failed"})
ACTIVE = frozenset({"pending", "running"})
_COLLECTION = "distillation_jobs"


class JobConflict(ValueError):
    """The requested transition would conflict with a live session job."""


class JobCancelled(RuntimeError):
    """The job was removed/cancelled before acquiring the memory write lock."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reason(result: dict) -> tuple[str, str]:
    status = result.get("outcomes_status")
    if status == "completed" and result.get("ok") is True:
        return "succeeded", "completed"
    return "failed", {
        "unavailable": "model_unavailable", "disabled": "outcomes_disabled",
        "no_episodes": "no_episodes", "error": "extraction_error",
        "cancelled": "cancelled",
    }.get(status, "invalid_result")


def _safe_result(result: dict) -> dict:
    # Never persist provider exception messages: they can contain keys or transcript text.
    fields = ("ok", "session_id", "episodes", "pending_consolidated", "facts_added", "outcomes",
              "duplicates", "invalidated", "summaries", "reflected", "working_cleared")
    safe = {k: result[k] for k in fields if k in result}
    status = result.get("outcomes_status")
    safe["outcomes_status"] = status if status in {
        "completed", "unavailable", "disabled", "no_episodes", "error", "cancelled",
    } else "error"
    error = result.get("outcomes_error")
    safe["outcomes_error"] = (error if isinstance(error, str) and
                              re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,70}Error", error)
                              else "ExtractionError") if error else None
    return safe


class DistillationJobs:
    """One domain instance per MemoryService; start/close are idempotent lifecycle hooks."""

    def __init__(self, service) -> None:
        self.service = service
        self.store = service.management_store
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lifecycle = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._closed = False

    @staticmethod
    def _validate_session(session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512:
            raise ValueError("session_id must contain 1–512 characters")

    def _users(self) -> list[str]:
        # scan is an internal store capability; no route exposes cross-namespace queue discovery.
        return sorted({namespace for namespace, _, _ in self.store.scan(_COLLECTION)})

    def _rows(self, user: str) -> list[dict]:
        return (self.store.get(user, _COLLECTION, "queue") or {}).get("jobs", [])

    def _update(self, user: str, transform) -> None:
        def apply(state):
            rows = (state or {}).get("jobs", [])
            transform(rows)
            return {"jobs": rows} if rows else None
        self.store.update(user, _COLLECTION, "queue", apply)

    @staticmethod
    def _public(job: dict, *, deduplicated: bool = False) -> dict:
        value = copy.deepcopy(job)
        value.pop("run_token", None)
        return {**value, "deduplicated": deduplicated}

    def get(self, user: str, job_id: str) -> dict:
        for job in self._rows(user):
            if job["id"] == job_id:
                return self._public(job)
        raise KeyError("distillation job not found")

    def list(self, user: str, *, status: Optional[str] = None, session_id: Optional[str] = None,
             limit: int = 20, offset: int = 0) -> dict:
        if status is not None and status not in STATES:
            raise ValueError("invalid job status")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1–100 and offset nonnegative")
        self.recover(user)
        rows = self._rows(user)
        counts = {state: sum(r["status"] == state for r in rows) for state in sorted(STATES)}
        rows = [r for r in rows if (status is None or r["status"] == status)
                and (session_id is None or r["session_id"] == session_id)]
        rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        total = len(rows)
        items = [self._public(r) for r in rows[offset:offset + limit]]
        return {"jobs": items, "counts": counts,
                "page": {"total": total, "limit": limit, "offset": offset,
                         "next_offset": offset + limit if offset + limit < total else None}}

    def enqueue(self, user: str, session_id: str, summarize: bool = True,
                clear_working: bool = True, outcomes: Optional[bool] = None,
                *, start: bool = True) -> dict:
        self._validate_session(session_id)
        if self._closed:
            raise JobConflict("distillation worker is closed")
        options = {"summarize": summarize, "clear_working": clear_working, "outcomes": outcomes}
        selected = {}
        deduplicated = False

        def create(rows):
            nonlocal selected, deduplicated
            for row in rows:
                if row["session_id"] == session_id and row["status"] in ACTIVE:
                    if row["options"] != options:
                        raise JobConflict("this session already has a job with different options")
                    selected, deduplicated = copy.deepcopy(row), True
                    return
            stamp = _now()
            selected = {"id": uuid.uuid4().hex, "session_id": session_id, "status": "pending",
                        "reason": "queued", "attempt": 1, "created_at": stamp,
                        "updated_at": stamp, "started_at": None, "finished_at": None,
                        "options": options, "result": None, "history": []}
            rows.append(selected)

        self._update(user, create)
        if start:
            self.start()
        self._wake.set()
        return self._public(selected, deduplicated=deduplicated)

    def retry(self, user: str, job_id: str, *, start: bool = True) -> dict:
        if self._closed:
            raise JobConflict("distillation worker is closed")
        self.recover(user)
        selected = {}
        deduplicated = False

        def reset(rows):
            nonlocal selected, deduplicated
            row = next((j for j in rows if j["id"] == job_id), None)
            if row is None:
                raise KeyError("distillation job not found")
            if row["status"] in ACTIVE:
                selected, deduplicated = copy.deepcopy(row), True
                return
            if row["status"] != "failed":
                raise JobConflict("only failed jobs can be retried")
            if any(j["id"] != job_id and j["session_id"] == row["session_id"]
                   and j["status"] in ACTIVE for j in rows):
                raise JobConflict("this session already has an active job")
            row["history"].append({k: row[k] for k in (
                "attempt", "status", "reason", "started_at", "finished_at", "result")})
            row.update(status="pending", reason="queued", attempt=row["attempt"] + 1,
                       updated_at=_now(), started_at=None, finished_at=None, result=None)
            row.pop("run_token", None)
            selected = copy.deepcopy(row)

        self._update(user, reset)
        if start:
            self.start()
        self._wake.set()
        return self._public(selected, deduplicated=deduplicated)

    @contextmanager
    def _execution_lock(self, user: str, session_id: str):
        import fcntl

        root = Path(self.service.data_dir) / ".distillation-locks"
        root.mkdir(mode=0o700, exist_ok=True)
        name = hashlib.sha256((user + "\0" + session_id).encode()).hexdigest()
        fd = os.open(root / (name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        locked = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                pass
            yield locked
        finally:
            os.close(fd)

    def recover(self, user: Optional[str] = None) -> dict:
        """Mark orphaned running attempts failed; pending jobs remain queued for start()."""
        recovered = 0
        for namespace in [user] if user is not None else self._users():
            for job in self._rows(namespace):
                if job["status"] != "running":
                    continue
                with self._execution_lock(namespace, job["session_id"]) as acquired:
                    if acquired:
                        recovered += self._finish(namespace, job["id"], job.get("run_token"),
                                                  "failed", "interrupted", None)
        return {"interrupted": recovered}

    def _guard(self, user: str, job_id: str, token: str) -> None:
        row = next((r for r in self._rows(user) if r["id"] == job_id), None)
        if not row or row["status"] != "running" or row.get("run_token") != token:
            raise JobCancelled("distillation job no longer owns this attempt")

    def _finish(self, user: str, job_id: str, token: Optional[str], status: str,
                reason: str, result: Optional[dict]) -> int:
        changed = 0

        def finish(rows):
            nonlocal changed
            for row in rows:
                if row["id"] == job_id and row["status"] == "running" and row.get("run_token") == token:
                    stamp = _now()
                    row.update(status=status, reason=reason, result=result,
                               updated_at=stamp, finished_at=stamp)
                    row.pop("run_token", None)
                    changed = 1

        self._update(user, finish)
        self._wake.set()
        return changed

    def _execute(self, user: str, job_id: str) -> None:
        try:
            initial = self.get(user, job_id)
        except KeyError:
            return
        with self._execution_lock(user, initial["session_id"]) as acquired:
            if not acquired:
                return
            claimed = {}
            token = uuid.uuid4().hex

            def claim(rows):
                nonlocal claimed
                for row in rows:
                    if row["id"] == job_id and row["status"] == "pending":
                        stamp = _now()
                        row.update(status="running", reason="executing", started_at=stamp,
                                   updated_at=stamp, run_token=token)
                        claimed = copy.deepcopy(row)

            self._update(user, claim)
            if not claimed:
                return
            try:
                result = self.service._close_session_impl(
                    user, claimed["session_id"], **claimed["options"],
                    _job_guard=lambda: self._guard(user, job_id, token),
                )
                status, reason = _reason(result)
                result = _safe_result(result)
            except JobCancelled:
                status, reason, result = "failed", "cancelled", None
            except Exception:
                status, reason, result = "failed", "execution_error", None
            # A forget operation can run after the core writer releases its lock. Check/finalize
            # under that same lock so neither a receipt nor a connection event resurrects its data.
            with self.service.write_lock(user):
                changed = self._finish(user, job_id, token, status, reason, result)
                tracker = getattr(self.service, "connections", None)
                if changed and tracker is not None:
                    from .connections import session_source

                    try:
                        tracker.record(
                            user, session_source(claimed["session_id"]), status, "distill",
                            session_id=claimed["session_id"],
                            error=(result or {}).get("outcomes_error") or
                                  (None if status == "succeeded" else "DistillationError"),
                            counts={"facts_added": (result or {}).get("facts_added", 0) +
                                                   (result or {}).get("outcomes", 0)},
                        )
                    except Exception:
                        # Connection telemetry cannot overwrite the actual distillation receipt.
                        pass

    def run_sync(self, user: str, session_id: str, summarize: bool = True,
                 clear_working: bool = True, outcomes: Optional[bool] = None) -> dict:
        """Compatibility path: wait for the shared job and return the original close payload."""
        job = self.enqueue(user, session_id, summarize, clear_working, outcomes, start=False)
        while True:
            self._execute(user, job["id"])
            try:
                current = self.get(user, job["id"])
            except KeyError:
                raise JobCancelled("distillation job removed") from None
            if current["status"] not in ACTIVE:
                result = current["result"] or {
                    "ok": False, "session_id": session_id, "outcomes_status": "error",
                    "outcomes_error": current["reason"], "outcomes": 0,
                }
                return {**result, "job_id": current["id"], "job_status": current["status"],
                        "job_reason": current["reason"]}
            self.recover(user)
            time.sleep(0.05)

    def start(self) -> None:
        with self._lifecycle:
            if self._closed:
                raise JobConflict("distillation worker is closed")
            if self._worker and self._worker.is_alive():
                return
            self.recover()
            self._worker = threading.Thread(target=self._consume, name="engram-distillation", daemon=True)
            self._worker.start()

    def _consume(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                for user in self._users():
                    for job in self._rows(user):
                        if self._stop.is_set():
                            return
                        if job["status"] == "pending":
                            self._execute(user, job["id"])
            except Exception:
                # A transient database failure must not kill the worker or invent a success receipt.
                pass
            self._wake.wait(0.5)

    def close(self, timeout: float = 5.0) -> None:
        with self._lifecycle:
            self._closed = True
            self._stop.set()
            self._wake.set()
            worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=timeout)

    def cancel(self, user: str, session_ids: Optional[list[str]] = None) -> int:
        """Fence selected active attempts; destructive callers must hold service.write_lock(user)."""
        selected = None if session_ids is None else set(session_ids)
        changed = 0

        def cancel(rows):
            nonlocal changed
            for row in rows:
                if row["status"] in ACTIVE and (selected is None or row["session_id"] in selected):
                    stamp = _now()
                    row.update(status="failed", reason="cancelled", result=None,
                               updated_at=stamp, finished_at=stamp)
                    row.pop("run_token", None)
                    changed += 1

        self._update(user, cancel)
        return changed

    def scrub(self, user: str, session_ids: Optional[list[str]] = None) -> int:
        """Erase job receipts/history and fence late writes. Call inside the memory write lock."""
        selected = None if session_ids is None else set(session_ids)
        removed = 0

        def scrub(rows):
            nonlocal removed
            retained = [r for r in rows if selected is not None and r["session_id"] not in selected]
            removed = len(rows) - len(retained)
            rows[:] = retained

        self._update(user, scrub)
        return removed
