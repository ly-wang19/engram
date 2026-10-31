"""Console browse contracts, using synthetic, offline memory namespaces."""
from __future__ import annotations

import pytest

from engram.service import MemoryService
from engram.types import Episode, Fact


@pytest.fixture
def service(tmp_path, monkeypatch):
    from engram.llm import providers

    monkeypatch.setattr(providers, "load_dotenv", lambda: None)
    for name in ("ENGRAM_LLM", "ENGRAM_ANSWERER", "ENGRAM_VISION_LLM",
                 "ENGRAM_ASYNC_CONSOLIDATION", "ENGRAM_API_KEYS", "ENGRAM_SPACE_KEYS"):
        monkeypatch.delenv(name, raising=False)
    return MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")


def episode(service, sid, stamp, *, user="owner", consolidated=True, content="Synthetic source"):
    mem = service.get(user)
    ep = Episode(content=content, user_id=mem.resolver.resolve(user), session_id=sid,
                 event_time=stamp, consolidated=consolidated)
    mem.episodes_doc.put(ep.id, ep)
    return ep


def fact(service, sources=(), *, user="owner", cold=False, **kwargs):
    mem = service.get(user)
    f = Fact(subject="sample", predicate="likes", object="synthetic value",
             user_id=mem.resolver.resolve(user), provenance=[e.id for e in sources], **kwargs)
    (mem.cold_store if cold else mem.fact_store).upsert(f.id, [0.0], f)
    return f


def test_sessions_filter_sort_summary_and_stable_page(service):
    a = episode(service, "project:a", 100, consolidated=False)
    a2 = episode(service, "project:a", 101)
    b = episode(service, "project:b", 200)
    episode(service, "unrelated:c", 300)
    fact(service, [a, a2])  # Two citations from one session still contribute only one fact.
    fact(service, [b])
    fact(service, [b], sensitive=True)
    service.get("owner").remember_working("Temporary synthetic state", user_id="owner",
                                          session_id="project:a")

    data = service.sessions("owner", q="PROJECT", status="with_facts", sort="most_facts", limit=1)
    assert data["summary"] == {"total": 2, "pending": 1, "with_facts": 2, "working": 1}
    assert data["sessions"][0]["id"] == "project:b"
    assert data["sessions"][0]["facts_added"] == 2
    assert data["page"]["total"] == 2 and data["next_offset"] == 1
    more = service.sessions("owner", q="project", status="with_facts", sort="most_facts", limit=1, offset=1)
    assert more["sessions"][0]["facts_added"] == 1
    assert more["next_offset"] is None
    assert service.sessions("owner", status="pending")["sessions"][0]["id"] == "project:a"
    assert service.sessions("owner", status="working")["page"]["total"] == 1
    assert service.sessions("owner", sort="oldest")["sessions"][0]["id"] == "project:a"
    assert service.sessions("owner")["sessions"][0]["id"] == "unrelated:c"
    assert service.sessions("other")["summary"]["total"] == 0


def test_sessions_ties_have_deterministic_id_order(service):
    for sid in ("c", "a", "b"):
        episode(service, sid, 100)
    assert [s["id"] for s in service.sessions("owner", limit=2)["sessions"]] == ["a", "b"]
    assert service.sessions("owner", offset=2, limit=2)["sessions"][0]["id"] == "c"


def test_memories_session_search_and_episode_paging(service):
    episodes = [episode(service, "selected", 100+i, content=f"searchable transcript {i}")
                for i in range(205)]
    other = episode(service, "other", 1000, content="searchable other transcript")
    ours = fact(service, [episodes[0]], valid_at=100)
    fact(service, [other], valid_at=200)
    result = service.memories("owner", session_id="selected", q="searchable", include_sensitive=True,
                              episodes_limit=5, episodes_offset=200, sort="oldest")
    assert result["episodes_page"]["total"] == 205
    assert [e["content"] for e in result["episodes"]] == [f"searchable transcript {i}" for i in range(200, 205)]
    assert result["episodes_page"]["has_more"] is False
    selected = service.memories("owner", session_id="selected", include_sensitive=True)
    assert [f["id"] for f in selected["facts"]] == [ours.id]
    assert selected["counts"]["episodes"] == 206  # Global counters stay unfiltered.
    assert service.memories("owner", session_id="absent", include_sensitive=True)["episodes"] == []
    assert service.memories("owner", session_id="selected")["episodes"] == []


def test_memories_facets_are_scoped_before_facet_filter_and_page(service):
    ep = episode(service, "selected", 100)
    other = episode(service, "other", 200)
    a = fact(service, [ep], category="Work", source="user", valid_at=100, salience=1)
    b = fact(service, [ep], category="Interests", source="extracted", valid_at=200, salience=5)
    fact(service, [ep], category="Private", sensitive=True, valid_at=300)
    fact(service, [other], category="Elsewhere", valid_at=400)
    data = service.memories("owner", session_id="selected", category="Work", source="user", facts_limit=1)
    assert [f["id"] for f in data["facts"]] == [a.id]
    assert data["fact_facets"] == {"categories": ["Interests", "Work"], "sources": ["extracted", "user"]}
    assert data["facts_page"]["total"] == 1
    assert service.memories("owner", session_id="selected", sort="salience")["facts"][0]["id"] == b.id
    assert service.memories("owner", session_id="selected", sort="oldest")["facts"][0]["id"] == a.id
    assert service.memories("owner", session_id="selected")["facts"][0]["id"] == b.id
    assert service.memories("owner", q="no-matching-phrase")["fact_facets"] == {"categories": [], "sources": []}


def test_sources_resolve_old_and_cold_provenance_without_recent_window(service):
    old = episode(service, "older-session", 1, content="Original historical source")
    second = episode(service, "older-session", 2, content="Supporting source")
    f = fact(service, [old, second], cold=True)
    f.provenance += [old.id, "missing-episode"]  # Duplicate references do not inflate source counts.
    for i in range(205):
        episode(service, "recent-session", 100+i)
    result = service.fact_sources("owner", f.id, include_sensitive=True, limit=1)
    assert result["episodes"][0]["id"] == old.id
    assert result["page"]["total"] == 2 and result["page"]["next_offset"] == 1
    assert result["missing_count"] == 1 and result["redacted"] is False
    more = service.fact_sources("owner", f.id, include_sensitive=True, limit=1, offset=1)
    assert more["episodes"][0]["id"] == second.id and more["page"]["next_offset"] is None


@pytest.mark.parametrize("sensitive", [False, True])
def test_sources_require_explicit_raw_text_opt_in(service, sensitive):
    ep = episode(service, "private-session", 100, content="Private original text")
    f = fact(service, [ep], sensitive=sensitive)
    redacted = service.fact_sources("owner", f.id)
    assert redacted["redacted"] is True and redacted["episodes"] == []
    assert "Private original text" not in str(redacted)
    assert service.fact_sources("owner", f.id, include_sensitive=True)["episodes"][0]["content"] == ep.content
    assert service.fact_sources("other", f.id, include_sensitive=True) is None


def test_sources_exclude_foreign_records_even_if_a_store_contains_them(service):
    mem = service.get("owner")
    foreign = Episode(content="Foreign source", user_id="different-owner", session_id="foreign")
    mem.episodes_doc.put(foreign.id, foreign)
    f = fact(service, [foreign])
    assert service.fact_sources("owner", f.id, include_sensitive=True)["episodes"] == []
    assert service.fact_sources("owner", f.id, include_sensitive=True)["missing_count"] == 1
    alien = Fact(subject="x", predicate="likes", object="hidden", user_id="different-owner")
    mem.fact_store.upsert(alien.id, [0.0], alien)
    assert service.fact_sources("owner", alien.id, include_sensitive=True) is None
    assert alien.id not in [f["id"] for f in service.memories("owner", include_sensitive=True)["facts"]]


@pytest.fixture
def client(service, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from engram.server import app as appmod

    monkeypatch.setenv("ENGRAM_OPEN", "1")
    monkeypatch.delenv("ENGRAM_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setattr(appmod, "_svc", service)
    with TestClient(appmod.app) as c:
        yield c


def test_http_sources_auth_boundaries_and_validation(client, service):
    ep = episode(service, "synthetic-session", 100)
    f = fact(service, [ep])
    route = f"/v1/facts/{f.id}/sources"
    h = {"Authorization": "Bearer owner"}
    assert client.get(route).status_code == 401
    assert client.get(route, headers={"Authorization": "Bearer other"}).status_code == 404
    assert client.get(route, headers=h).json()["redacted"] is True
    result = client.get(route, headers=h, params={"include_sensitive": True})
    assert result.status_code == 200 and result.json()["episodes"][0]["id"] == ep.id
    for query in ({"limit": 201}, {"limit": 0}, {"offset": -1}, {"limit": "bad"}):
        assert client.get(route, headers=h, params=query).status_code == 422
    assert client.get("/v1/facts/does-not-exist/sources", headers=h).status_code == 404


def test_http_browse_filter_contracts(client, service):
    ep = episode(service, "target", 100, consolidated=False)
    f = fact(service, [ep], category="Work", source="user")
    h = {"Authorization": "Bearer owner"}
    s = client.get("/v1/sessions", headers=h, params={"status": "pending", "sort": "oldest"}).json()
    assert s["sessions"][0]["id"] == "target" and s["summary"]["pending"] == 1
    response = client.get("/v1/memories", headers=h, params={"session_id": "target", "category": "Work",
                          "source": "user", "sort": "salience", "include_sensitive": True})
    assert response.status_code == 200
    assert response.json()["facts"][0]["id"] == f.id
    assert response.json()["episodes"][0]["id"] == ep.id
    assert client.get("/v1/sessions", headers=h, params={"status": "unknown"}).status_code == 422
    assert client.get("/v1/memories", headers=h, params={"sort": "unknown"}).status_code == 422
    assert client.get("/v1/memories", headers=h, params={"source": "unknown"}).status_code == 422
