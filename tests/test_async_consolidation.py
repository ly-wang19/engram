"""Architecture: async System-2 consolidation (CLAUDE.md Bet F), opt-in via
ENGRAM_ASYNC_CONSOLIDATION=1. The default synchronous path is unchanged (proven here too).
Offline (hashing embedder, rule extractor, no LLM)."""
from __future__ import annotations

import shutil
import tempfile

from engram.service import MemoryService

_SENT = "My name is Wei and I live in Shenzhen."  # the rule extractor pulls >=1 fact from this


def test_consolidation_is_synchronous_by_default(monkeypatch):
    monkeypatch.delenv("ENGRAM_ASYNC_CONSOLIDATION", raising=False)
    d = tempfile.mkdtemp(prefix="engram_sync_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        assert svc._worker is None  # no background thread
        r = svc.remember("u", _SENT)
        assert "queued" not in r and r.get("scope") == "long"  # consolidated inline
        assert r.get("extracted", 0) >= 1  # fact available immediately (remember-then-queryable)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_async_remember_returns_fast_then_flush_consolidates(monkeypatch):
    monkeypatch.setenv("ENGRAM_ASYNC_CONSOLIDATION", "1")
    d = tempfile.mkdtemp(prefix="engram_async_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        assert svc._worker is not None  # background worker started

        r = svc.remember("u", _SENT)
        assert r.get("queued") is True  # System-1 returned WITHOUT inline consolidation
        # the episode is already durable (System-1 saved it) before consolidation runs
        assert svc.memories("u")["counts"]["episodes"] >= 1

        svc.flush()  # wait for System-2 to drain
        # after flush, consolidation has run: the rule extractor pulled at least one fact
        assert svc.memories("u")["counts"]["facts_live"] >= 1

        svc.close()
        assert svc._worker is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_async_coalesces_multiple_writes_then_flush(monkeypatch):
    monkeypatch.setenv("ENGRAM_ASYNC_CONSOLIDATION", "1")
    d = tempfile.mkdtemp(prefix="engram_async2_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        svc.remember("u", "My name is Wei.")
        svc.remember("u", "I live in Shenzhen.")
        svc.remember("u", "I work at Moonshot AI.")
        svc.flush()  # all queued passes complete (coalesced); no write is lost
        assert svc.memories("u")["counts"]["episodes"] == 3
        assert svc.memories("u")["counts"]["facts_live"] >= 1
        svc.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
