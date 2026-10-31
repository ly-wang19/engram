"""Review queues: complete pagination, durable handling, source revisions, tenant boundaries."""
from __future__ import annotations

import pytest

from engram.management.review import ReviewError, ReviewQueue
from engram.service import MemoryService
from engram.types import Episode, Fact


@pytest.fixture
def service(tmp_path, monkeypatch):
    from engram.llm import providers
    monkeypatch.setattr(providers, "load_dotenv", lambda: None)
    for key in ("ENGRAM_LLM", "ENGRAM_ANSWERER", "ENGRAM_VISION_LLM", "ENGRAM_ASYNC_CONSOLIDATION"):
        monkeypatch.delenv(key, raising=False)
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    yield svc
    svc.close()


def fact(svc, *, user="owner", kind="open_question", value="Synthetic question?", cold=False, **kwargs):
    mem = svc.get(user)
    item = Fact(user_id=user, subject=kwargs.pop("subject", "session-a"), predicate=kind,
                object=value, valid_at=kwargs.pop("valid_at", 100), **kwargs)
    (mem.cold_store if cold else mem.fact_store).upsert(item.id, [0.0], item)
    return item


def resolve(queue, question, answer):
    row = next(r for r in queue.questions("owner")["items"] if r["id"] == question.id)
    return queue.update_question("owner", question.id, status="resolved", resolution_id=answer.id,
                                 expected_version=row["version"], note="Synthetic rationale", include_sensitive=True)


def test_question_resolve_restart_and_reopen(service):
    q = fact(service)
    a = fact(service, kind="decision", value="Synthetic decision")
    queue = ReviewQueue(service)
    row = resolve(queue, q, a)
    assert row["review_status"] == "resolved" and row["resolution"]["id"] == a.id
    assert queue.questions("owner")["page"]["total"] == 0
    service._save("owner", service.get("owner"))
    fresh = MemoryService(data_dir=service.data_dir, embedder_name="hashing", llm_name="")
    try:
        queue = ReviewQueue(fresh)
        row = queue.questions("owner", status="resolved", include_sensitive=True)["items"][0]
        assert row["history"][0]["note"] == "Synthetic rationale"
        opened = queue.update_question("owner", q.id, status="open", expected_version=row["version"], include_sensitive=True)
        assert opened["resolution_id"] is None and len(opened["history"]) == 2
    finally:
        fresh.close()


def test_question_server_filter_before_page_and_cold_storage(service):
    for i in range(125):
        fact(service, kind="decision", value=f"new decision {i}", valid_at=1000+i)
    questions = [fact(service, value=f"Question-{i}", cold=i == 2, valid_at=100+i) for i in range(3)]
    queue = ReviewQueue(service)
    assert queue.outcomes("owner", kind="open_question", limit=2)["page"]["total"] == 3
    assert queue.outcomes("owner", kind="open_question", limit=2, offset=2)["items"][0]["id"] == questions[0].id
    assert queue.questions("owner", q="question-2", limit=1)["items"][0]["id"] == questions[2].id
    assert queue.questions("other")["page"]["total"] == 0


def test_question_rejects_foreign_non_outcome_and_question_resolution(service):
    q = fact(service)
    candidates = [fact(service, user="other", kind="decision"), fact(service, kind="likes"), fact(service)]
    queue = ReviewQueue(service)
    row = next(r for r in queue.questions("owner")["items"] if r["id"] == q.id)
    for candidate in candidates:
        with pytest.raises(ReviewError) as exc:
            queue.update_question("owner", q.id, status="resolved", resolution_id=candidate.id, expected_version=row["version"])
        assert exc.value.status_code == 422
    with pytest.raises(ReviewError) as exc:
        queue.update_question("other", q.id, status="open", expected_version=row["version"])
    assert exc.value.status_code == 404


def test_question_stale_action_and_source_change_reopen(service):
    q = fact(service)
    a = fact(service, kind="finding")
    queue = ReviewQueue(service)
    old = resolve(queue, q, a)
    q.object = "Changed question"
    with pytest.raises(ReviewError) as exc:
        queue.update_question("owner", q.id, status="open", expected_version=old["version"])
    assert exc.value.status_code == 409
    assert queue.questions("owner")["items"][0]["review_status"] == "open"
    assert queue.questions("owner", include_sensitive=True)["items"][0]["history"][-1]["reason"] == "source_changed"


@pytest.mark.parametrize("change", ["edit", "delete", "expire"])
def test_resolution_change_reopens_question(service, change):
    q, a = fact(service), fact(service, kind="lesson")
    queue = ReviewQueue(service)
    resolve(queue, q, a)
    if change == "edit":
        a.text = "Corrected conclusion"
    elif change == "delete":
        service.get("owner").fact_store.delete(a.id)
    else:
        a.invalid_at = 200
    row = queue.questions("owner", include_sensitive=True)["items"][0]
    assert row["review_status"] == "open" and row["resolution_id"] is None
    assert row["history"][-1]["reason"] == "resolution_changed"


def test_question_source_episode_change_reopens(service):
    mem = service.get("owner")
    ep = Episode(content="Synthetic source", user_id="owner")
    mem.episodes_doc.put(ep.id, ep)
    q, a = fact(service, provenance=[ep.id]), fact(service, kind="decision")
    queue = ReviewQueue(service)
    resolve(queue, q, a)
    ep.content = "Corrected source"
    assert queue.questions("owner")["items"][0]["source_changed"]


def test_question_sensitive_boundary_including_resolution_history(service):
    q = fact(service)
    secret = fact(service, kind="decision", value="Synthetic private conclusion", sensitive=True)
    queue = ReviewQueue(service)
    row = queue.questions("owner")["items"][0]
    with pytest.raises(ReviewError):
        queue.update_question("owner", q.id, status="resolved", resolution_id=secret.id, expected_version=row["version"])
    resolve(queue, q, secret)
    visible = queue.questions("owner", status="resolved")["items"][0]
    assert visible["resolution_hidden"] and visible["resolution"] is None
    assert visible["history"][0]["note"] == "" and visible["note"] == ""
    assert "resolution_id" not in visible["history"][0]
    q.sensitive = True
    assert queue.questions("owner", status="all")["items"] == []


def test_health_complete_paging_and_filters(service):
    for i in range(137):
        fact(service, kind="likes", value=f"synthetic_machine_token_{i}", subject=f"subject-{i}")
    fact(service, kind="likes", value="likes")
    queue = ReviewQueue(service)
    first = queue.health("owner", kind="machine_token", limit=30)
    assert first["page"]["total"] == 137 and first["page"]["next_offset"] == 30
    ids = []
    for offset in range(0, 137, 30):
        ids += [r["id"] for r in queue.health("owner", kind="machine_token", limit=30, offset=offset)["items"]]
    assert len(ids) == len(set(ids)) == 137
    assert queue.health("owner", kind="empty_value")["page"]["total"] == 1
    assert queue.health("other")["page"]["total"] == 0


def test_health_ignore_persist_restart_reopen_and_stale_write(service):
    f = fact(service, kind="likes", value="synthetic_machine_token")
    queue = ReviewQueue(service)
    row = queue.health("owner")["items"][0]
    result = queue.update_health("owner", row["id"], status="ignored", expected_version=row["version"], note="False positive", include_sensitive=True)
    assert result["history"][0]["note"] == "False positive"
    assert queue.health("owner")["items"] == []
    service._save("owner", service.get("owner"))
    fresh = MemoryService(data_dir=service.data_dir, embedder_name="hashing", llm_name="")
    try:
        assert ReviewQueue(fresh).health("owner", status="ignored")["items"][0]["id"] == row["id"]
    finally:
        fresh.close()
    f.object = "new_synthetic_machine_token"
    changed = queue.health("owner", include_sensitive=True)["items"][0]
    assert changed["source_changed"] and changed["status"] == "open"
    assert changed["history"][-1]["reason"] == "source_changed"
    with pytest.raises(ReviewError) as exc:
        queue.update_health("owner", row["id"], status="resolved", expected_version=result["version"])
    assert exc.value.status_code == 409
    result = queue.update_health("owner", row["id"], status="resolved", expected_version=changed["version"])
    queue.update_health("owner", row["id"], status="open", expected_version=result["version"])
    assert queue.health("owner")["items"][0]["status"] == "open"


def test_health_missing_source_has_durable_handling_history(service):
    f = fact(service, kind="likes", value="synthetic_machine_token")
    queue = ReviewQueue(service)
    row = queue.health("owner")["items"][0]
    queue.update_health("owner", row["id"], status="ignored", expected_version=row["version"], note="Checked", include_sensitive=True)
    service.get("owner").fact_store.delete(f.id)
    resolved = queue.health("owner", status="resolved", include_sensitive=True)["items"][0]
    assert not resolved["present"] and resolved["history"][-1]["reason"] == "no_longer_flagged"
    assert len(resolved["history"]) == 2 and "text" not in resolved
    assert queue.health("owner")["items"] == []


def test_slot_full_source_version_not_only_first_five_samples(service):
    fs = [fact(service, kind="occupation", value=f"Synthetic job {i}") for i in range(6)]
    queue = ReviewQueue(service)
    row = queue.health("owner", kind="slot_overflow")["items"][0]
    queue.update_health("owner", row["id"], status="ignored", expected_version=row["version"])
    fs[-1].object = "Different sixth job"
    assert queue.health("owner", kind="slot_overflow")["items"][0]["status"] == "open"


def test_health_sensitive_and_cross_tenant_actions(service):
    fact(service, kind="likes", value="synthetic_sensitive_token", sensitive=True)
    queue = ReviewQueue(service)
    assert queue.health("owner")["items"] == []
    row = queue.health("owner", include_sensitive=True)["items"][0]
    for user in ("owner", "other"):
        with pytest.raises(ReviewError) as exc:
            queue.update_health(user, row["id"], status="ignored", expected_version=row["version"])
        assert exc.value.status_code == 404


def test_routes_auth_validation_and_paging(service):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    from engram.server.review_routes import create_router

    def auth(authorization: str = Header("")):
        if authorization not in {"Bearer owner", "Bearer other"}:
            raise HTTPException(401)
        return authorization.split()[1]

    app = FastAPI()
    app.include_router(create_router(lambda: service, auth))
    q, answer = fact(service), fact(service, kind="decision")
    with TestClient(app) as client:
        assert client.get("/v1/review/questions").status_code == 401
        headers = {"Authorization": "Bearer owner"}
        assert client.get("/v1/review/questions?limit=0", headers=headers).status_code == 422
        assert client.get("/v1/review/questions?status=invalid", headers=headers).status_code == 400
        row = client.get("/v1/review/questions", headers=headers).json()["items"][0]
        patch = {"status": "resolved", "resolution_id": answer.id, "expected_version": row["version"]}
        assert client.patch(f"/v1/review/questions/{q.id}", json=patch, headers={"Authorization": "Bearer other"}).status_code == 404
        assert client.patch(f"/v1/review/questions/{q.id}", json=patch, headers=headers).status_code == 200
        assert client.patch(f"/v1/review/questions/{q.id}", json=patch, headers=headers).status_code == 409
        result = client.get("/v1/review/outcomes?kind=decision&limit=1", headers=headers)
        assert result.json()["items"][0]["id"] == answer.id
        f = fact(service, kind="likes", value="synthetic_machine_token")
        row = client.get("/v1/review/health", headers=headers).json()["items"][0]
        assert row["fact_id"] == f.id
        patch = {"status": "ignored", "expected_version": row["version"]}
        assert client.patch(f'/v1/review/health/{row["id"]}', json=patch, headers=headers).status_code == 200


def test_health_returned_source_reopens_previous_resolution(service):
    f = fact(service, kind="likes", value="synthetic_machine_token")
    queue = ReviewQueue(service)
    row = queue.health("owner")["items"][0]
    queue.update_health("owner", row["id"], status="resolved", expected_version=row["version"])
    service.get("owner").fact_store.delete(f.id)
    assert queue.health("owner", status="resolved")["items"][0]["present"] is False
    service.get("owner").fact_store.upsert(f.id, [0.0], f)
    reopened = queue.health("owner")["items"][0]
    assert reopened["status"] == "open" and reopened["source_changed"]
    assert reopened["history"][-1]["reason"] == "source_changed"


def test_recall_metadata_does_not_invalidate_review(service):
    f = fact(service, kind="likes", value="synthetic_machine_token")
    queue = ReviewQueue(service)
    row = queue.health("owner")["items"][0]
    queue.update_health("owner", row["id"], status="ignored", expected_version=row["version"])
    f.access_count += 1
    f.last_access += 100
    f.salience += 0.1
    assert queue.health("owner", status="ignored")["items"][0]["status"] == "ignored"
