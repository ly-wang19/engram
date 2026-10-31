"""Synthetic, offline checks of job transitions, fencing, isolation and HTTP contracts."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from engram.management.jobs import DistillationJobs, JobCancelled, JobConflict
from engram.management.store import ManagementStore


class SyntheticService:
    def __init__(self, path):
        self.data_dir = str(path)
        self.management_store = ManagementStore(self.data_dir)
        self._lock = threading.Lock()
        self.jobs = DistillationJobs(self)
        self.calls = []
        self.status = "completed"
        self.error = None
        self.exception = None
        self.before_lock = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.writes = []

    @contextmanager
    def write_lock(self, user):
        with self._lock:
            yield

    def _close_session_impl(self, user, session_id, summarize=True, clear_working=True,
                            outcomes=None, *, _job_guard=None):
        if self.before_lock:
            self.before_lock()
        with self.write_lock(user):
            if _job_guard:
                _job_guard()
            self.calls.append((user, session_id, summarize, clear_working, outcomes))
            self.entered.set()
            assert self.release.wait(5), "test fixture was not released"
            if self.exception:
                raise self.exception
            self.writes.append(session_id)
            return {"ok": True, "session_id": session_id, "episodes": 1,
                    "pending_consolidated": 1, "facts_added": 2,
                    "outcomes": 3 if self.status == "completed" else 0,
                    "outcomes_status": self.status, "outcomes_error": self.error,
                    "duplicates": 0, "invalidated": 0, "summaries": int(summarize),
                    "reflected": 0, "working_cleared": int(clear_working)}


@pytest.fixture
def service(tmp_path):
    instance = SyntheticService(tmp_path)
    yield instance
    instance.release.set()
    instance.jobs.close()


def wait_status(jobs, user, job_id, expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = jobs.get(user, job_id)
        if job["status"] == expected:
            return job
        time.sleep(0.01)
    pytest.fail(f"job failed to reach {expected}: {job['status']}, {job['reason']}")


def test_pending_is_durable_and_filters_are_before_paging(service):
    for n in range(5):
        service.jobs.enqueue("alpha", f"session-{n}", start=False)
    other = service.jobs.enqueue("beta", "session-0", start=False)
    restarted = SyntheticService(service.data_dir)
    result = restarted.jobs.list("alpha", status="pending", limit=2, offset=2)
    assert result["page"] == {"total": 5, "limit": 2, "offset": 2, "next_offset": 4}
    assert len(result["jobs"]) == 2
    filtered = restarted.jobs.list("alpha", session_id="session-0")
    assert filtered["page"]["total"] == 1
    assert filtered["jobs"][0]["id"] != other["id"]
    with pytest.raises(KeyError):
        restarted.jobs.get("alpha", other["id"])
    with pytest.raises(KeyError):
        restarted.jobs.retry("alpha", other["id"], start=False)


def test_concurrent_enqueue_is_atomic_across_domain_instances(service):
    sibling = SyntheticService(service.data_dir)
    domains = [service.jobs, sibling.jobs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda n: domains[n % 2].enqueue("alpha", "shared", start=False), range(16)))
    assert len({r["id"] for r in records}) == 1
    assert sum(not r["deduplicated"] for r in records) == 1
    assert service.jobs.list("alpha")["page"]["total"] == 1


def test_different_options_cannot_silently_replace_a_live_request(service):
    service.jobs.enqueue("alpha", "session", outcomes=False, start=False)
    with pytest.raises(JobConflict):
        service.jobs.enqueue("alpha", "session", outcomes=True, start=False)


def test_async_running_and_live_lock_survive_another_recovery(service):
    service.release.clear()
    job = service.jobs.enqueue("alpha", "session")
    assert service.entered.wait(2)
    assert service.jobs.get("alpha", job["id"])["status"] == "running"
    sibling = SyntheticService(service.data_dir)
    assert sibling.jobs.recover() == {"interrupted": 0}
    duplicate = sibling.jobs.enqueue("alpha", "session", start=False)
    assert duplicate["id"] == job["id"] and duplicate["deduplicated"]
    service.release.set()
    finished = wait_status(service.jobs, "alpha", job["id"], "succeeded")
    assert finished["result"]["outcomes"] == 3
    assert "run_token" not in finished
    assert len(service.calls) == 1


@pytest.mark.parametrize(("status", "reason"), [
    ("unavailable", "model_unavailable"), ("disabled", "outcomes_disabled"),
    ("no_episodes", "no_episodes"), ("error", "extraction_error"),
    ("unknown", "invalid_result"),
])
def test_ok_true_does_not_misreport_distillation_success(service, status, reason):
    service.status = status
    result = service.jobs.run_sync("alpha", "session", summarize=False, clear_working=False)
    assert result["ok"] is True
    assert result["job_status"] == "failed" and result["job_reason"] == reason
    assert service.jobs.get("alpha", result["job_id"])["reason"] == reason
    assert service.calls == [("alpha", "session", False, False, None)]


def test_retry_reuses_id_and_keeps_previous_failure_receipt(service):
    service.status, service.error = "error", "AuthenticationError"
    failed = service.jobs.run_sync("alpha", "session")
    assert failed["outcomes_error"] == "AuthenticationError"
    service.status, service.error = "completed", None
    retried = service.jobs.retry("alpha", failed["job_id"], start=False)
    assert retried["attempt"] == 2
    assert retried["history"][0]["reason"] == "extraction_error"
    assert service.jobs.retry("alpha", failed["job_id"], start=False)["deduplicated"]
    service.jobs.start()
    job = wait_status(service.jobs, "alpha", failed["job_id"], "succeeded")
    assert job["result"]["outcomes"] == 3
    assert len(service.calls) == 2
    with pytest.raises(JobConflict):
        service.jobs.retry("alpha", job["id"], start=False)


def test_exception_is_failed_without_persisting_exception_text(service):
    service.exception = RuntimeError("synthetic-secret-value should never persist")
    result = service.jobs.run_sync("alpha", "session")
    job = service.jobs.get("alpha", result["job_id"])
    assert job["status"] == "failed" and job["reason"] == "execution_error"
    assert "synthetic-secret-value" not in json.dumps(service.management_store.scan("distillation_jobs"))


def test_result_error_text_is_not_a_debug_log(service):
    service.status, service.error = "error", "request included synthetic-secret-value"
    result = service.jobs.run_sync("alpha", "session")
    assert result["outcomes_error"] == "ExtractionError"


def test_connection_observation_uses_real_outcome(service):
    from engram.management.connections import ConnectionTracker

    service.connections = ConnectionTracker(service)
    service.status = "unavailable"
    service.jobs.run_sync("alpha", "codex:synthetic")
    events = service.management_store.list("alpha", "connection_events")
    assert len(events) == 1
    assert events[0]["status"] == "failed" and events[0]["source"] == "codex"
    service.status = "completed"
    service.jobs.run_sync("alpha", "console:synthetic")
    events = service.management_store.list("alpha", "connection_events")
    assert len(events) == 2
    assert any(event["status"] == "succeeded" and event["counts"]["facts_added"] == 5 for event in events)


def test_restart_marks_unlocked_running_attempt_interrupted_and_can_retry(service):
    job = service.jobs.enqueue("alpha", "session", start=False)
    service.management_store.update("alpha", "distillation_jobs", "queue", lambda state: {
        "jobs": [{**state["jobs"][0], "status": "running", "run_token": "abandoned", "started_at": "2026-01-01"}]
    })
    restarted = SyntheticService(service.data_dir)
    assert restarted.jobs.recover()["interrupted"] == 1
    record = restarted.jobs.get("alpha", job["id"])
    assert record["status"] == "failed" and record["reason"] == "interrupted"
    assert record["result"] is None
    restarted.jobs.retry("alpha", job["id"])
    try:
        assert wait_status(restarted.jobs, "alpha", job["id"], "succeeded")["attempt"] == 2
    finally:
        restarted.jobs.close()


def test_restart_resumes_persisted_pending_work(service):
    job = service.jobs.enqueue("alpha", "session", start=False)
    restarted = SyntheticService(service.data_dir)
    restarted.jobs.start()
    try:
        wait_status(restarted.jobs, "alpha", job["id"], "succeeded")
        assert len(restarted.calls) == 1
    finally:
        restarted.jobs.close()


def test_sync_client_joins_async_job_without_deadlocking_or_duplicate_execution(service, monkeypatch):
    service.release.clear()
    job = service.jobs.enqueue("alpha", "session")
    assert service.entered.wait(2)
    joined = threading.Event()
    enqueue = service.jobs.enqueue

    def enqueue_and_signal(*args, **kwargs):
        result = enqueue(*args, **kwargs)
        joined.set()
        return result

    monkeypatch.setattr(service.jobs, "enqueue", enqueue_and_signal)
    with ThreadPoolExecutor(max_workers=1) as pool:
        sync = pool.submit(service.jobs.run_sync, "alpha", "session")
        try:
            # Keep the async attempt active until the synchronous caller has joined its queue entry.
            assert joined.wait(2), "synchronous caller did not finish enqueueing"
        finally:
            service.release.set()
        result = sync.result(timeout=3)
    assert result["job_id"] == job["id"] and result["outcomes_status"] == "completed"
    assert len(service.calls) == 1


def test_scrub_fences_claimed_job_waiting_for_memory_write_lock(service):
    arrived, proceed = threading.Event(), threading.Event()

    def before_lock():
        arrived.set()
        assert proceed.wait(5)

    service.before_lock = before_lock
    job = service.jobs.enqueue("alpha", "forgotten-session")
    assert arrived.wait(2)
    with service.write_lock("alpha"):
        assert service.jobs.scrub("alpha", ["forgotten-session"]) == 1
    proceed.set()
    service.jobs.close()
    assert service.calls == [] and service.writes == []
    assert service.jobs.list("alpha")["page"]["total"] == 0
    with pytest.raises(KeyError):
        service.jobs.get("alpha", job["id"])
    assert service.management_store.scan("distillation_jobs") == []


def test_scrub_after_execution_does_not_resurrect_receipt(service):
    job = service.jobs.enqueue("alpha", "session", start=False)
    service.management_store.update("alpha", "distillation_jobs", "queue", lambda state: {
        "jobs": [{**state["jobs"][0], "status": "running", "run_token": "late"}]
    })
    with service.write_lock("alpha"):
        service.jobs.scrub("alpha")
    assert service.jobs._finish("alpha", job["id"], "late", "succeeded", "completed", {}) == 0
    assert service.jobs.list("alpha")["page"]["total"] == 0


def test_selective_scrub_and_cancel_preserve_other_tenants_and_sessions(service):
    one = service.jobs.enqueue("alpha", "one", start=False)
    two = service.jobs.enqueue("alpha", "two", start=False)
    other = service.jobs.enqueue("beta", "one", start=False)
    with service.write_lock("alpha"):
        assert service.jobs.cancel("alpha", ["one"]) == 1
        assert service.jobs.get("alpha", one["id"])["reason"] == "cancelled"
        assert service.jobs.scrub("alpha", []) == 0
        assert service.jobs.scrub("alpha", ["one"]) == 1
    assert service.jobs.get("alpha", two["id"])["status"] == "pending"
    assert service.jobs.get("beta", other["id"])["status"] == "pending"


def test_stale_attempt_cannot_overwrite_retried_job(service):
    service.status = "error"
    result = service.jobs.run_sync("alpha", "session")
    service.jobs.retry("alpha", result["job_id"], start=False)
    assert service.jobs._finish("alpha", result["job_id"], "old-token", "succeeded", "completed", {}) == 0
    assert service.jobs.get("alpha", result["job_id"])["status"] == "pending"


def test_http_surface_validation_and_namespace_isolation(service, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from engram.server.job_routes import create_router

    app = fastapi.FastAPI()

    def auth(authorization: str = fastapi.Header(default="")):
        if authorization not in {"Bearer alpha", "Bearer beta"}:
            raise fastapi.HTTPException(status_code=401)
        return authorization.split()[1]

    app.include_router(create_router(lambda: service, auth))
    monkeypatch.setattr(service.jobs, "start", lambda: None)
    with TestClient(app) as client:
        alpha, beta = {"Authorization": "Bearer alpha"}, {"Authorization": "Bearer beta"}
        assert client.get("/v1/jobs").status_code == 401
        assert client.post("/v1/jobs", headers=alpha, json={"session_id": "  "}).status_code == 422
        assert client.get("/v1/jobs?status=unknown", headers=alpha).status_code == 422
        assert client.get("/v1/jobs?limit=101", headers=alpha).status_code == 422
        assert client.get("/v1/jobs?offset=-1", headers=alpha).status_code == 422
        response = client.post("/v1/jobs", headers=alpha, json={"session_id": "session"})
        assert response.status_code == 202
        job = response.json()
        repeat = client.post("/v1/jobs", headers=alpha, json={"session_id": "session"}).json()
        assert repeat["id"] == job["id"] and repeat["deduplicated"]
        assert client.get(f"/v1/jobs/{job['id']}", headers=beta).status_code == 404
        assert client.post(f"/v1/jobs/{job['id']}/retry", headers=beta).status_code == 404
        assert client.get("/v1/jobs", headers=beta).json()["page"]["total"] == 0
        service.status = "unavailable"
        service.jobs._execute("alpha", job["id"])
        failed = client.get("/v1/jobs?status=failed", headers=alpha).json()
        assert failed["page"]["total"] == 1
        assert failed["jobs"][0]["reason"] == "model_unavailable"
        assert client.post(f"/v1/jobs/{job['id']}/retry", headers=alpha).status_code == 202


def test_close_does_not_mark_inflight_work_completed(service):
    service.release.clear()
    job = service.jobs.enqueue("alpha", "session")
    assert service.entered.wait(2)
    service.jobs.close(timeout=0)
    assert service.jobs.get("alpha", job["id"])["status"] == "running"
    with pytest.raises(JobConflict):
        service.jobs.enqueue("alpha", "other")
    service.release.set()
    wait_status(service.jobs, "alpha", job["id"], "succeeded")


def test_cancelled_guard_raises_before_writing(service):
    with pytest.raises(JobCancelled):
        service._close_session_impl("alpha", "session", _job_guard=lambda: service.jobs._guard("alpha", "missing", "token"))
    assert not service.writes
