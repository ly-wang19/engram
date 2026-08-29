"""Pinning the extractor is what makes a read-path A/B measure the code instead of the model.

Motivating incident (2026-08-29): a same-item A/B on LOCOMO reported single-hop losing 5 questions on a
render path that was byte-identical between the two runs. It had not regressed -- of 83 single-hop
items, zero produced the same context token count across runs, because the LLM extractor distilled a
different fact set from the same sessions each time. Every delta in that experiment was inside the
extractor's own variance."""
from __future__ import annotations

import tempfile

from engram.llm.cache import CachedLLM


class FlakyLLM:
    """Stands in for a real extractor: same prompt, different reply every call."""

    model = "flaky-1"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        return f"reply-{self.calls}"


def test_same_prompt_returns_the_same_reply():
    with tempfile.TemporaryDirectory() as d:
        inner = FlakyLLM()
        llm = CachedLLM(inner, d)
        first = llm.complete("extract facts from: Alice moved to Paris")
        again = llm.complete("extract facts from: Alice moved to Paris")
        assert first == again, "a pinned extractor must not vary between calls"
        assert inner.calls == 1, "the second call must be served from cache"
        assert llm.stats() == {"hits": 1, "misses": 1, "hit_rate": 0.5}


def test_cache_survives_a_new_process_object():
    """Runs are separate processes; the pin is worthless if it does not outlive one."""
    with tempfile.TemporaryDirectory() as d:
        first = CachedLLM(FlakyLLM(), d).complete("p")
        second_inner = FlakyLLM()
        second = CachedLLM(second_inner, d).complete("p")
        assert first == second
        assert second_inner.calls == 0, "the second run must not call the model at all"


def test_different_prompts_and_kwargs_do_not_collide():
    with tempfile.TemporaryDirectory() as d:
        llm = CachedLLM(FlakyLLM(), d)
        assert llm.complete("a") != llm.complete("b")
        assert llm.complete("a", system="s1") != llm.complete("a", system="s2")


def test_different_models_do_not_share_entries():
    """Serving doubao's facts for a gpt-5.6 run would corrupt the very comparison this protects."""
    with tempfile.TemporaryDirectory() as d:
        shared = FlakyLLM()  # one model object, so replies differ per call and a hit is visible
        a = CachedLLM(shared, d, model_tag="model-a").complete("p")
        b = CachedLLM(shared, d, model_tag="model-b").complete("p")
        assert shared.calls == 2, "a different model tag must miss the cache"
        assert a != b
        # and each tag replays its own entry
        assert CachedLLM(shared, d, model_tag="model-a").complete("p") == a
        assert shared.calls == 2


def test_empty_replies_are_not_cached():
    """An empty body is the transient-failure shape the retry loop handles; pinning it would make one
    flaky call permanent."""

    class SometimesEmpty:
        model = "e"

        def __init__(self):
            self.calls = 0

        def complete(self, prompt, **kwargs):
            self.calls += 1
            return "" if self.calls == 1 else "real answer"

    with tempfile.TemporaryDirectory() as d:
        inner = SometimesEmpty()
        llm = CachedLLM(inner, d)
        assert llm.complete("p") == ""
        assert llm.complete("p") == "real answer"  # retried, not replayed from cache


def test_unknown_attributes_fall_through_to_the_real_model():
    with tempfile.TemporaryDirectory() as d:
        assert CachedLLM(FlakyLLM(), d).model == "flaky-1"
