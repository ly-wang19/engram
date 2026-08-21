"""Re-embedding migration (the escape hatch the store's embedding-space guards point to). The guards
themselves (DimensionMismatchError/EmbedderMismatchError on open) are covered in test_persist_safety;
here we prove the MIGRATION: open allow_mismatch -> reembed() -> every vector is in the new space,
retrieval works, and the saved manifest makes the next open clean. All offline."""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from engram.embed import HashingEmbedder
from engram.memory import Memory
from engram.service import MemoryService
from engram.store import DimensionMismatchError

_SENT = "My name is Wei and I live in Shenzhen."


def _store(path: str, dim: int = 64) -> None:
    m = Memory.open(path, embedder=HashingEmbedder(dim))
    m.add(_SENT, user_id="u", consolidate=True)
    m.summarize_episodes(list(m.episodes_doc.values()))  # populate the summary index too
    m.save()


def test_reembed_migrates_every_vector_then_reopens_clean():
    d = tempfile.mkdtemp(prefix="engram_reembed_")
    try:
        p = os.path.join(d, "u")
        _store(p, dim=64)
        with pytest.raises(DimensionMismatchError):
            Memory.open(p, embedder=HashingEmbedder(128))  # guard still refuses a plain mismatched open

        m = Memory.open(p, allow_mismatch=True, embedder=HashingEmbedder(128))
        counts = m.reembed()
        assert counts["facts"] >= 1 and counts["episodes"] >= 1 and counts["summaries"] >= 1
        # every index now holds 128-dim vectors and retrieval still works
        assert all(len(f.embedding) == 128 for f in m.fact_store.values())
        assert all(len(ep.embedding) == 128 for ep in m.episodes_vec.values())
        assert all(len(ep.summary_embedding) == 128 for ep in m.summary_vec.values())
        assert m.search("where do I live", user_id="u").facts
        m.save()  # manifest now records the new embedder identity/dim

        m2 = Memory.open(p, embedder=HashingEmbedder(128))  # clean open, no allow_mismatch needed
        assert m2.search("where do I live", user_id="u").facts
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_service_refuses_then_migrates_with_flag(monkeypatch):
    d = tempfile.mkdtemp(prefix="engram_reembed_svc_")
    try:
        # a namespace stored under dim-64 hashing; the service builds the config-default dim
        svc0 = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        default_dim = svc0.embedder.dim
        assert default_dim != 64
        store_dir = svc0._path("u")
        _store(store_dir, dim=64)

        monkeypatch.delenv("ENGRAM_REEMBED_ON_MISMATCH", raising=False)
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        with pytest.raises(DimensionMismatchError):
            svc.memories("u")  # refuses: no silent corruption

        monkeypatch.setenv("ENGRAM_REEMBED_ON_MISMATCH", "1")
        svc2 = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        dump = svc2.memories("u")  # auto-migrates on first touch
        assert dump["counts"]["facts_live"] >= 1
        mem = svc2.get("u")
        assert all(len(f.embedding) == default_dim for f in mem.fact_store.values())

        # migration persisted: a THIRD service (flag off) opens cleanly
        monkeypatch.delenv("ENGRAM_REEMBED_ON_MISMATCH", raising=False)
        svc3 = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        assert svc3.memories("u")["counts"]["facts_live"] >= 1
    finally:
        shutil.rmtree(d, ignore_errors=True)
