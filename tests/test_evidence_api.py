"""Evidence bindings and privacy boundaries, with synthetic data and a recording offline answerer."""
import json

import pytest

from engram.management.evidence import EvidenceAnswers, EvidenceError
from engram.service import MemoryService
from engram.types import Episode, Fact


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from engram.llm import providers
    monkeypatch.setattr(providers, "load_dotenv", lambda: None)
    for key in ("ENGRAM_ANSWERER", "ENGRAM_VISION_LLM", "ENGRAM_ASYNC_CONSOLIDATION"):
        monkeypatch.delenv(key, raising=False)
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    yield service
    service.close()


def seed(svc, user="owner", sensitive=False, stamp=100):
    mem = svc.get(user)
    ep = Episode(content="Synthetic traveler likes the blue observatory.", user_id=user,
                 session_id="sample-session", event_time=stamp, ingested_at=stamp,
                 metadata={"source": "records"})
    ep.embedding = mem.embedder.embed(ep.content)
    mem.episodes_doc.put(ep.id, ep)
    mem.episodes_vec.upsert(ep.id, ep.embedding, ep)
    f = Fact(subject="traveler", predicate="likes", object="blue observatory", user_id=user,
             provenance=[ep.id], sensitive=sensitive, valid_at=stamp, created_at=stamp)
    f.embedding = mem.embedder.embed(f.text)
    mem.fact_store.upsert(f.id, f.embedding, f)
    return mem, ep, f


class Recorder:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def complete(self, prompt, system):
        self.calls.append((prompt, system))
        if self.result is not None:
            return self.result
        return json.dumps({"claims": [{"text": "The traveler likes the blue observatory.",
                                       "evidence_ids": ["E1"]}], "abstained": False})


@pytest.fixture
def client(svc):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    from engram.server.evidence_routes import create_router
    app = FastAPI()
    def auth(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "unauthorized")
        return authorization[7:]
    app.include_router(create_router(lambda: svc, auth))
    with TestClient(app) as client:
        yield client


def test_citations_are_exactly_the_ids_supplied_to_the_one_answer_call(svc, monkeypatch):
    mem, ep, f = seed(svc)
    answerer = svc.answerer = Recorder()
    # Calling recall a second time to decorate an unrelated answer must never be necessary.
    monkeypatch.setattr(svc, "recall", lambda *a, **k: pytest.fail("second context retrieval"))
    data = EvidenceAnswers(svc).ask("owner", "What does traveler like?", redact_sensitive=False)
    assert data["answer_status"] == "completed" and len(answerer.calls) == 1
    sent = json.loads(answerer.calls[0][0].split("EVIDENCE:\n", 1)[1].split("\n\nQUESTION:", 1)[0])
    assert {x["source_id"] for x in sent["evidence"]} == {ep.id, f.id}
    assert [x["id"] for x in data["citations"]] == ["E1"]
    assert data["citations"][0]["text"] == sent["evidence"][0]["text"]
    assert len(data["context_evidence"]) == 2  # unused candidates are not called citations
    assert data["answer"].endswith("[E1]")


@pytest.mark.parametrize("raw", [
    '{"claims":[{"text":"unsupported","evidence_ids":["E999"]}],"abstained":false}',
    '{"claims":[{"text":"unsupported","evidence_ids":[]}],"abstained":false}',
    '{"claims":[],"abstained":false}', 'not JSON',
    '{"claims":[{"text":"unsupported","evidence_ids":[["E1"]]}],"abstained":false}',
])
def test_bad_citations_never_produce_an_answer(svc, raw):
    seed(svc)
    svc.answerer = Recorder(raw)
    data = EvidenceAnswers(svc).ask("owner", "traveler likes")
    assert data["answer_status"] == "invalid_citations"
    assert data["answer"] == "" and not data["citations"]


def test_default_redaction_excludes_raw_and_sensitive_facts_before_model(svc):
    seed(svc, sensitive=True)
    svc.answerer = Recorder()
    result = EvidenceAnswers(svc).ask("owner", "traveler likes")
    assert result["answer_status"] == "no_evidence" and not svc.answerer.calls
    assert "observatory" not in json.dumps(result)
    visible = EvidenceAnswers(svc).ask("owner", "traveler likes", redact_sensitive=False)
    assert visible["answer_status"] == "completed"
    with pytest.raises(EvidenceError) as exc:
        EvidenceAnswers(svc).source("owner", visible["citations"][0]["source_token"])
    assert exc.value.status == 403


def test_as_of_excludes_future_world_and_transaction_times(svc):
    mem, ep, f = seed(svc, stamp=300)
    svc.answerer = Recorder()
    assert EvidenceAnswers(svc).ask("owner", "traveler likes", as_of=200, redact_sensitive=False)["answer_status"] == "no_evidence"
    ep.event_time, f.valid_at = 100, 100
    assert EvidenceAnswers(svc).ask("owner", "traveler likes", as_of=200, redact_sensitive=False)["answer_status"] == "no_evidence"
    ep.ingested_at, f.created_at = 100, 100
    assert EvidenceAnswers(svc).ask("owner", "traveler likes", as_of=200, redact_sensitive=False)["answer_status"] == "completed"


def test_http_source_detail_correction_and_tenant_boundary(client, svc):
    _, ep, f = seed(svc)
    svc.answerer = Recorder()
    headers = {"Authorization": "Bearer owner"}
    result = client.post("/v1/ask", headers=headers, json={"query": "traveler likes", "redact_sensitive": False}).json()
    token = result["citations"][0]["source_token"]
    path = f"/v1/evidence/{token}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer stranger"}).status_code == 404
    hidden = client.get(path, headers=headers).json()
    assert hidden["raw_redacted"] and hidden["episodes"] == []
    shown = client.get(path + "?include_sensitive=true", headers=headers).json()
    assert shown["episodes"][0]["id"] == ep.id
    assert shown["fact"]["id"] == f.id
    svc.update_fact("owner", f.id, object="green garden")
    assert client.get(path + "?include_sensitive=true", headers=headers).status_code == 409
    assert svc.get("owner").fact_store.get(f.id).source == "user"
    assert client.post("/v1/ask", headers=headers, json={"query": "x", "n_chunks": 0}).status_code == 422


def test_no_model_is_not_a_fake_answer_and_provider_errors_are_sanitized(svc):
    seed(svc)
    assert EvidenceAnswers(svc).ask("owner", "traveler likes")["answer_status"] == "unavailable"
    class Fails:
        def complete(self, *a, **k):
            raise RuntimeError("SECRET-provider-key")
    svc.answerer = Fails()
    data = EvidenceAnswers(svc).ask("owner", "traveler likes")
    assert data["answer_status"] == "error" and "SECRET" not in str(data)


def test_source_changed_while_answering_withholds_entire_snapshot(svc):
    _, _, f = seed(svc)
    class Mutating(Recorder):
        def complete(self, *a, **k):
            svc.update_fact("owner", f.id, object="green garden")
            return super().complete(*a, **k)
    svc.answerer = Mutating()
    data = EvidenceAnswers(svc).ask("owner", "traveler likes")
    assert data["answer_status"] == "stale_evidence"
    assert data["context"] == "" and data["context_evidence"] == []


def test_source_readback_is_as_of_and_rejects_nonfinite(svc):
    mem, ep, f = seed(svc)
    extra = Episode(content="Future source detail", user_id="owner", event_time=300, ingested_at=300)
    mem.episodes_doc.put(extra.id, extra)
    f.provenance.append(extra.id)
    data = EvidenceAnswers(svc).ask("owner", "traveler likes", as_of=200, redact_sensitive=False)
    token = data["context_evidence"][0]["source_token"]
    details = EvidenceAnswers(svc).source("owner", token, include_sensitive=True)
    assert [e["id"] for e in details["episodes"]] == [ep.id]
    with pytest.raises(EvidenceError):
        EvidenceAnswers(svc).ask("owner", "x", as_of=float("nan"))


@pytest.mark.parametrize("failure", ["error", "invalid"])
def test_inflight_erasure_never_leaks_context_even_if_provider_fails(svc, failure):
    from engram.management.forgetting import SelectiveForgetting
    seed(svc)
    class Erasing:
        def complete(self, *a, **k):
            domain = SelectiveForgetting(svc)
            p = domain.preview("owner", {"session_ids": ["sample-session"]})
            domain.confirm("owner", p["token"], p["scope"], p["version"], confirm=True)
            if failure == "error":
                raise RuntimeError("provider failure")
            return "invalid JSON"
    svc.answerer = Erasing()
    data = EvidenceAnswers(svc).ask("owner", "traveler likes", redact_sensitive=False)
    assert data["answer_status"] == "stale_evidence"
    assert "observatory" not in json.dumps(data)
