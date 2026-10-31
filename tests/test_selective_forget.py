"""Bounded erasure, race refusal and restart survival, all against temporary synthetic data."""
import json

import pytest

from engram.management.evidence import EvidenceAnswers, EvidenceError
from engram.management.forgetting import ForgetError, SelectiveForgetting
from engram.service import MemoryService
from engram.types import Conflict, Episode, Fact


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from engram.llm import providers
    monkeypatch.setattr(providers, "load_dotenv", lambda: None)
    for key in ("ENGRAM_ANSWERER", "ENGRAM_VISION_LLM", "ENGRAM_ASYNC_CONSOLIDATION"):
        monkeypatch.delenv(key, raising=False)
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    yield service
    service.close()


def episode(mem, session, stamp, content, source="records"):
    ep = Episode(content=content, user_id="owner", session_id=session, event_time=stamp,
                 ingested_at=stamp, metadata={"source": source})
    ep.embedding = mem.embedder.embed(content)
    mem.episodes_doc.put(ep.id, ep)
    mem.episodes_vec.upsert(ep.id, ep.embedding, ep)
    return ep


def fact(mem, text, sources, *, cold=False, source="extracted"):
    f = Fact(subject="traveler", predicate="likes", object=text, user_id="owner",
             provenance=sources, valid_at=100, created_at=100, source=source)
    f.embedding = mem.embedder.embed(f.text)
    (mem.cold_store if cold else mem.fact_store).upsert(f.id, f.embedding, f)
    mem.engine.graph_builder.add_fact(f)
    return f


def fixture_memory(svc):
    mem = svc.get("owner")
    doomed = episode(mem, "erase", 100, "Synthetic violet observatory secret")
    keep = episode(mem, "keep", 200, "Independent green garden", "document")
    hot = fact(mem, "violet observatory", [doomed.id])
    cold = fact(mem, "violet observation protocol", [doomed.id], cold=True)
    shared = fact(mem, "shared violet value", [doomed.id, keep.id])
    transitive = fact(mem, "derived violet value", [shared.id])
    retained = fact(mem, "green garden", [keep.id], source="user")
    mem.summarize_episodes([doomed, keep])
    # reflect() can copy erased values into an otherwise retained episode's summary.
    keep.summary += " [updated: violet observatory secret]"
    mem._persona_cache["owner"] = "violet observatory secret"
    mem._identity["owner"] = "violet observatory secret"
    mem._aliases["owner"] = {"violet observatory secret"}
    mem.working_set = [hot, retained]
    mem.remember_working("violet observatory secret", user_id="owner", session_id="erase")
    mem.remember_working("independent working", user_id="owner", session_id="keep")
    conflict = Conflict(older=hot.id, newer=retained.id, user_id="owner", text_older=hot.text)
    mem.conflicts[conflict.id] = conflict
    svc.management_store.put("owner", "review_health", "finding", {"id": "finding", "note": "violet secret"})
    svc._save("owner", mem)
    return mem, doomed, keep, hot, cold, shared, transitive, retained


def confirm(domain, p):
    return domain.confirm("owner", p["token"], p["scope"], p["version"], confirm=True)


def test_cascades_all_tiers_and_persists_without_erasing_other_tenant(svc):
    mem, ep, keep, hot, cold, shared, transitive, retained = fixture_memory(svc)
    svc.add_fact("other", "another", "likes", "violet observatory")
    domain = SelectiveForgetting(svc)
    preview = domain.preview("owner", {"session_ids": ["erase"]}, include_sensitive=True)
    assert preview["counts"]["facts"] == 4
    assert preview["counts"]["facts_cold"] == 1 and preview["counts"]["shared_source_facts"] == 1
    assert preview["counts"]["summaries"] == 2
    assert preview["episodes"]["items"][0]["text"] == ep.content
    assert "violet" not in json.dumps(svc.management_store.list("owner", "forget_previews"))
    result = confirm(domain, preview)
    assert result["status"] == "completed"
    assert mem.episodes_doc.get(ep.id) is None and mem.episodes_vec.get(ep.id) is None
    assert {f.id for f in mem._all_facts()} == {retained.id}
    assert mem.summary_vec.values() == [] and mem.episodes_doc.get(keep.id).summary == ""
    assert not mem._persona_cache and not mem._identity and not mem._aliases and not mem.working_set
    assert {w.session_id for w in mem.working_mem.values()} == {"keep"}
    assert mem.conflicts == {} and all(r.fact_id == retained.id for r in mem.graph.relations())
    assert not svc.management_store.list("owner", "review_health")
    reopened = MemoryService(data_dir=svc.data_dir, embedder_name="hashing", llm_name="")
    try:
        export = json.dumps(reopened.export("owner", include_sensitive=True))
        assert "violet" not in export and "green garden" in export
        assert "violet" in json.dumps(reopened.export("other", include_sensitive=True))
        assert "violet" not in reopened.recall("owner", "violet observatory", redact_sensitive=False)["context"]
    finally:
        reopened.close()


@pytest.mark.parametrize("scope", [{}, {"session_ids": []}, {"source": ""}, {"time_from": 10},
                                    {"time_to": 20}, {"time_from": 20, "time_to": 10},
                                    {"time_from": 0, "time_to": float("inf")},
                                    {"session_ids": "erase"}, {"unknown": "erase"}])
def test_rejects_empty_or_unbounded_scopes(svc, scope):
    with pytest.raises(ForgetError):
        SelectiveForgetting(svc).preview("owner", scope)


def test_intersection_exact_source_and_time_half_open(svc):
    mem = svc.get("owner")
    a = episode(mem, "session", 100, "delete", "records")
    b = episode(mem, "session", 200, "keep", "records")
    c = episode(mem, "session", 110, "keep", "document")
    scope = {"session_ids": ["session"], "source": "records", "time_from": 100, "time_to": 200}
    p = SelectiveForgetting(svc).preview("owner", scope)
    assert p["counts"]["episodes"] == 1 and p["episodes"]["items"][0]["id"] == a.id
    assert p["episodes"]["items"][0]["text"] == ""
    confirm(SelectiveForgetting(svc), p)
    assert {e.id for e in mem.episodes_doc.values()} == {b.id, c.id}


def test_confirmation_bound_to_namespace_scope_version_and_single_use(svc):
    fixture_memory(svc)
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"session_ids": ["erase"]})
    for user, scope, version, explicit in [
        ("other", p["scope"], p["version"], True),
        ("owner", {"session_ids": ["keep"]}, p["version"], True),
        ("owner", p["scope"], "0" * 64, True),
        ("owner", p["scope"], p["version"], False),
    ]:
        with pytest.raises(ForgetError):
            d.confirm(user, p["token"], scope, version, confirm=explicit)
    confirm(d, p)
    with pytest.raises(ForgetError) as exc:
        confirm(d, p)
    assert exc.value.status == 409


def test_new_data_or_same_id_content_change_requires_new_preview(svc):
    mem, ep, *_ = fixture_memory(svc)
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"session_ids": ["erase"]})
    ep.content += " extra approved? no"
    with pytest.raises(ForgetError) as exc:
        confirm(d, p)
    assert exc.value.status == 409
    p = d.preview("owner", {"session_ids": ["erase"]})
    episode(mem, "erase", 110, "new content")
    with pytest.raises(ForgetError):
        confirm(d, p)
    assert len(mem.episodes_doc.values()) == 3


def test_expired_token_and_preview_pagination(svc, monkeypatch):
    mem = svc.get("owner")
    for i in range(25):
        episode(mem, "erase", i, f"Synthetic record {i}")
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"session_ids": ["erase"]})
    assert p["episodes"]["total"] == 25 and p["episodes"]["next_offset"] == 20
    page = d.preview_page("owner", p["token"], offset=20)
    assert len(page["items"]) == 5 and page["next_offset"] is None
    import engram.management.forgetting as module
    monkeypatch.setattr(module.time, "time", lambda: p["expires_at"] + 1)
    with pytest.raises(ForgetError):
        confirm(d, p)


def test_pending_intent_recovery_retries_exact_ids_and_sanitizes_failure(svc, monkeypatch):
    _, doomed, *_ = fixture_memory(svc)
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"session_ids": ["erase"]})
    save = svc._save
    def fail(*args):
        raise OSError("SECRET failure")
    monkeypatch.setattr(svc, "_save", fail)
    with pytest.raises(ForgetError) as exc:
        confirm(d, p)
    assert exc.value.status == 503 and "SECRET" not in str(exc.value)
    assert svc.management_store.list("owner", "forget_intents")[0]["status"] == "pending"
    monkeypatch.setattr(svc, "_save", save)
    with svc.write_lock("owner"):
        mem = svc.get("owner")
        d.recover_locked("owner", mem)
    assert svc.management_store.list("owner", "forget_intents")[0]["status"] == "completed"
    assert svc.get("owner").episodes_doc.get(doomed.id) is None


def test_old_evidence_refs_invalidated_and_jobs_cancelled_under_lock(svc, monkeypatch):
    fixture_memory(svc)
    refs = EvidenceAnswers(svc).ask("owner", "violet observatory", redact_sensitive=False)["context_evidence"]
    class Jobs:
        def __init__(self): self.cancelled = []
        def scrub(self, user, session_ids): self.cancelled.append((user, session_ids))
        def close(self): pass
    jobs = Jobs()
    monkeypatch.setattr(svc, "jobs", jobs)
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"session_ids": ["erase"]})
    confirm(d, p)
    assert jobs.cancelled == [("owner", ["erase"])]
    for ref in refs:
        with pytest.raises(EvidenceError) as exc:
            EvidenceAnswers(svc).source("owner", ref["source_token"], include_sensitive=True)
        assert exc.value.status == 404


def test_standalone_manual_fact_and_working_only_session(svc):
    mem = svc.get("owner")
    f = fact(mem, "standalone", [], source="user")
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"time_from": 50, "time_to": 150})
    assert p["counts"]["facts"] == 1
    confirm(d, p)
    assert mem.fact_store.get(f.id) is None
    mem.remember_working("synthetic temporary", user_id="owner", session_id="work-only")
    p = d.preview("owner", {"session_ids": ["work-only"]})
    assert p["counts"]["working"] == 1
    confirm(d, p)
    assert not mem.working_mem


def test_http_explicit_confirmation_and_protected_demo(svc):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI, Header
    from fastapi.testclient import TestClient
    from engram.server.forget_routes import create_router
    app = FastAPI()
    def auth(authorization: str = Header(default="")): return authorization.removeprefix("Bearer ")
    app.include_router(create_router(lambda: svc, auth))
    fixture_memory(svc)
    with TestClient(app) as c:
        assert c.post("/v1/forget/preview", headers={"Authorization": "Bearer 1"}, json={"scope": {"session_ids": ["erase"]}}).status_code == 403
        h = {"Authorization": "Bearer owner"}
        p = c.post("/v1/forget/preview", headers=h, json={"scope": {"session_ids": ["erase"]}}).json()
        body = {k: p[k] for k in ("token", "scope", "version")}
        assert c.post("/v1/forget/confirm", headers=h, json=body).status_code == 400
        assert c.post("/v1/forget/confirm", headers=h, json={**body, "confirm": "true"}).status_code == 422
        assert c.post("/v1/forget/confirm", headers=h, json={**body, "confirm": True}).status_code == 200


def test_connection_events_and_source_aggregate_cleanup_keeps_other_sessions(svc):
    fixture_memory(svc)
    svc.connections.record("owner", "records", "stored", "import", session_id="erase")
    keep_event = svc.connections.record("owner", "records", "stored", "import", session_id="keep")
    svc.connections.record("owner", "private-source", "failed", "import", session_id="erase", error="TimeoutError")
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"session_ids": ["erase"]})
    assert p["counts"]["connection_events"] == 2
    confirm(d, p)
    events = svc.management_store.list("owner", "connection_events")
    assert [e["id"] for e in events] == [keep_event["id"]]
    rows = svc.management_store.list("owner", "connections")
    assert len(rows) == 1 and rows[0]["source"] == "records" and rows[0]["attempts"] == 1
    assert svc.management_store.list("owner", "forget_previews") == []


def test_restart_read_barrier_recovers_erasure_before_exposing_memory(svc, monkeypatch):
    fixture_memory(svc)
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"session_ids": ["erase"]})
    monkeypatch.setattr(svc, "_save", lambda *a: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(ForgetError):
        confirm(d, p)
    reopened = MemoryService(data_dir=svc.data_dir, embedder_name="hashing", llm_name="")
    try:
        export = json.dumps(reopened.export("owner", include_sensitive=True))
        assert "violet" not in export and "green garden" in export
        assert reopened.management_store.list("owner", "forget_intents")[0]["status"] == "completed"
    finally:
        reopened.close()


def test_partial_live_snapshot_write_is_repaired_before_open(svc, monkeypatch):
    import engram.store.persist as persist
    fixture_memory(svc)
    d = SelectiveForgetting(svc)
    p = d.preview("owner", {"session_ids": ["erase"]})
    write = persist._write_jsonl
    live_path = svc._path("owner")
    def interrupted(path, name, rows):
        if path == live_path and name == "facts":
            raise OSError("simulated interruption after episodes were replaced")
        return write(path, name, rows)
    monkeypatch.setattr(persist, "_write_jsonl", interrupted)
    with pytest.raises(ForgetError):
        confirm(d, p)
    monkeypatch.setattr(persist, "_write_jsonl", write)
    # A fresh service must repair before Memory.open; no manual recovery call is permitted here.
    reopened = MemoryService(data_dir=svc.data_dir, embedder_name="hashing", llm_name="")
    try:
        export = json.dumps(reopened.export("owner", include_sensitive=True))
        assert "violet" not in export and "green garden" in export
        assert reopened.management_store.list("owner", "forget_intents")[0]["status"] == "completed"
    finally:
        reopened.close()


def test_already_claimed_job_cannot_recreate_erased_data(svc, monkeypatch):
    import threading
    fixture_memory(svc)
    entered, release = threading.Event(), threading.Event()
    original = svc._close_session_impl
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(svc, "_close_session_impl", delayed)
    job = svc.jobs.enqueue("owner", "erase", start=False)
    worker = threading.Thread(target=svc.jobs._execute, args=("owner", job["id"]))
    worker.start()
    try:
        assert entered.wait(3)
        domain = SelectiveForgetting(svc)
        p = domain.preview("owner", {"session_ids": ["erase"]})
        confirm(domain, p)
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert svc.jobs.list("owner")["jobs"] == []
    assert "violet" not in json.dumps(svc.export("owner", include_sensitive=True))
    assert not svc.connections.events("owner")["items"]
