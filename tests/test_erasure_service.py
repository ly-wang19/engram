from __future__ import annotations

from pathlib import Path

from engram.service import MemoryService


def _source_fact(service: MemoryService, user: str, session_id: str, content: str):
    mem = service.get(user)
    episode = mem.add(content, user_id=user, session_id=session_id)
    fact = mem.add_fact("user", "private_note", content, user_id=user)
    fact.provenance = [episode.id]
    mem._upsert_fact(fact)
    service._save(user, mem)
    return episode, fact


def _store_bytes(path: str) -> bytes:
    chunks = []
    for item in sorted(Path(path).iterdir()):
        if item.is_file():
            chunks.append(item.read_bytes())
    return b"\n".join(chunks)


def test_service_fact_delete_returns_verified_receipt_and_purges_disk(tmp_path) -> None:
    secret = "ERASURE-SENTINEL-74d27c-private-address"
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    episode, fact = _source_fact(service, "u1", "private", secret)
    assert secret.encode() in _store_bytes(service._path("u1"))

    preview = service.delete_fact("u1", fact.id)
    assert preview["ok"] is False
    assert preview["confirmation_required"] is True
    assert preview["impact"] == {
        "facts": 1,
        "episodes": 1,
        "working": 0,
        "conflicts": 0,
    }

    result = service.delete_fact("u1", fact.id, confirm=True)

    assert result["ok"] is True
    assert result["erasure"]["verified"] is True
    assert result["erasure"]["storage_verified"] is True
    assert result["erasure"]["canonical_storage_verified"] is True
    assert result["erasure"]["live_index_verified"] is True
    assert result["erasure"]["physical_media_erasure_guaranteed"] is False
    assert result["erasure"]["counts"]["facts"] == 1
    assert result["erasure"]["counts"]["episodes"] == 1
    assert secret.encode() not in _store_bytes(service._path("u1"))

    reloaded = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="").get("u1")
    assert reloaded.fact_store.get(fact.id) is None
    assert reloaded.episodes_doc.get(episode.id) is None


def test_service_session_erasure_requires_confirmation_and_is_scoped(tmp_path) -> None:
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    erased_episode, erased_fact = _source_fact(
        service,
        "u1",
        "erase-me",
        "ERASURE-SESSION-SENTINEL",
    )
    kept_episode, kept_fact = _source_fact(service, "u1", "keep-me", "ordinary note")
    mem = service.get("u1")
    erased_working = mem.remember_working("temporary secret", "u1", "erase-me")
    kept_working = mem.remember_working("temporary note", "u1", "keep-me")
    service._save("u1", mem)

    guard = service.erase_session("u1", "erase-me", confirm=False)
    assert guard["ok"] is False
    assert guard["confirmation_required"] is True

    result = service.erase_session("u1", "erase-me", confirm=True)

    assert result["ok"] is True
    assert result["erasure"]["verified"] is True
    assert result["erasure"]["storage_verified"] is True
    reloaded = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="").get("u1")
    assert reloaded.episodes_doc.get(erased_episode.id) is None
    assert reloaded.fact_store.get(erased_fact.id) is None
    assert erased_working.id not in reloaded.working_mem
    assert reloaded.episodes_doc.get(kept_episode.id) is not None
    assert reloaded.fact_store.get(kept_fact.id) is not None
    assert kept_working.id in reloaded.working_mem
