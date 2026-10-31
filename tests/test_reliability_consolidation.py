"""Extraction failures must stay retryable, unlike a valid empty extraction."""
from __future__ import annotations

import pytest

from engram import Memory
from engram.types import Fact


class ExtractionUnavailable(RuntimeError):
    pass


class SinglePhaseExtractor:
    def __init__(self, failing_content="second"):
        self.failing_content = failing_content

    def extract(self, ep):
        if ep.content == self.failing_content:
            raise ExtractionUnavailable("synthetic extraction failure")
        return [Fact(subject="synthetic", predicate=ep.content, object="retained",
                     user_id=ep.user_id, provenance=[ep.id], valid_at=ep.event_time)]


class TwoPhaseExtractor(SinglePhaseExtractor):
    def __init__(self, stage, failing_content="second"):
        super().__init__(failing_content)
        self.stage = stage

    def raw_items(self, ep):
        if self.stage == "raw" and ep.content == self.failing_content:
            raise ExtractionUnavailable("synthetic provider failure")
        return [{}]

    def facts_from(self, ep, items):
        if self.stage == "convert" and ep.content == self.failing_content:
            raise ExtractionUnavailable("synthetic conversion failure")
        return SinglePhaseExtractor(None).extract(ep)

    def extract(self, ep):
        return self.facts_from(ep, self.raw_items(ep))


@pytest.mark.parametrize("stage", ["single_phase", "raw", "convert"])
@pytest.mark.parametrize("batch_size", [1, 2])
def test_failed_extraction_preserves_pending_batch_and_retries_after_reopen(tmp_path, stage, batch_size):
    mem = Memory()
    if batch_size == 2:
        mem.add("first", session_id="synthetic-session", event_time=1)
    mem.add("second", session_id="synthetic-session", event_time=2)
    mem.save(str(tmp_path))
    mem.engine.extractor = (SinglePhaseExtractor() if stage == "single_phase"
                            else TwoPhaseExtractor(stage))

    with pytest.raises(ExtractionUnavailable):
        mem.consolidate()
    assert len(mem.ingestor.pending()) == batch_size
    assert not mem.fact_store.values()

    # Saving a failed pass must not make the missing facts permanently disappear from the queue.
    mem.save(str(tmp_path))
    recovered = Memory.open(str(tmp_path))
    assert len(recovered.ingestor.pending()) == batch_size
    recovered.engine.extractor = SinglePhaseExtractor(None)
    assert recovered.consolidate()["facts_added"] == batch_size
    assert not recovered.ingestor.pending()
    assert recovered.consolidate()["facts_added"] == 0
    assert len(recovered.fact_store.values()) == batch_size
    assert {p for f in recovered.fact_store.values() for p in f.provenance} == {
        ep.id for ep in recovered.episodes_doc.values()
    }


@pytest.mark.parametrize("batch_size", [1, 2])
def test_successful_empty_extraction_is_completed(batch_size):
    class EmptyExtractor:
        def extract(self, ep):
            return []

    mem = Memory()
    for i in range(batch_size):
        mem.add(f"Synthetic greeting {i}")
    mem.engine.extractor = EmptyExtractor()
    assert mem.consolidate()["facts_added"] == 0
    assert not mem.ingestor.pending()


def test_close_job_cannot_succeed_when_batch_extraction_failed(tmp_path, monkeypatch):
    from engram.llm import providers
    from engram.service import MemoryService
    from engram.consolidate import outcomes

    monkeypatch.setattr(providers, "load_dotenv", lambda: None)
    for name in ("ENGRAM_LLM", "ENGRAM_ANSWERER", "ENGRAM_VISION_LLM"):
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("ENGRAM_SESSION_OUTCOMES", raising=False)
    monkeypatch.delenv("ENGRAM_ASYNC_CONSOLIDATION", raising=False)
    monkeypatch.setattr(outcomes, "extract_outcomes", lambda *a, **kw: [])

    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    try:
        mem = svc.get("synthetic-user")
        for content in ("first", "second"):
            mem.add(content, user_id="synthetic-user", session_id="synthetic-session")
        svc._save("synthetic-user", mem)
        mem.engine.extractor = SinglePhaseExtractor()
        svc.llm = object()  # only the explicitly stubbed outcome extractor can see this
        result = svc.close_session("synthetic-user", "synthetic-session", summarize=False)
        assert result["job_status"] == "failed"
        assert result["outcomes_status"] == "error"
        assert len(mem.ingestor.pending()) == 2
    finally:
        svc.close()

    recovered = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    try:
        mem = recovered.get("synthetic-user")
        assert len(mem.ingestor.pending()) == 2
        mem.engine.extractor = SinglePhaseExtractor(None)
        recovered.llm = object()
        result = recovered.close_session("synthetic-user", "synthetic-session", summarize=False)
        assert result["job_status"] == "succeeded"
        assert result["facts_added"] == 2
        assert len(mem.fact_store.values()) == 2
    finally:
        recovered.close()
