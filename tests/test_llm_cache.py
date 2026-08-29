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


def test_identity_registration_does_not_depend_on_extraction_order():
    """The last 10% of A/B noise: extraction ran under a thread pool, and registering the user's
    canonical name is order-dependent, so whichever call returned first decided whether later facts read
    'Evan lives in X' or 'user lives in X'. The fact set flipped between identical runs, which flipped
    the persona prompt, which defeated the pinned cache. Extraction is now two-phase: model calls fan
    out, conversion runs in chronological order."""
    from engram import Memory
    from engram.types import Episode

    class StubExtractor:
        """Mimics the LLM extractor's split API; the name is only stated in the LAST episode."""

        def __init__(self):
            self.self_name = {}

        def raw_items(self, ep):
            if "call me" in ep.content:
                return [{"subject": "user", "predicate": "name", "object": "Evan"}]
            return [{"subject": "user", "predicate": "likes", "object": ep.content.split()[-1]}]

        def facts_from(self, ep, items):
            from engram.types import Fact

            out = []
            for it in items:
                if it["predicate"] == "name":
                    self.self_name.setdefault(ep.user_id, it["object"])
                    continue
                subj = self.self_name.get(ep.user_id, it["subject"])
                out.append(Fact(subject=subj, predicate=it["predicate"], object=it["object"],
                                user_id=ep.user_id, valid_at=ep.event_time))
            return out

        def extract(self, ep):
            return self.facts_from(ep, self.raw_items(ep))

    seen = []
    for _ in range(3):
        mem = Memory()
        mem.engine.extractor = StubExtractor()
        for i, text in enumerate(["I like coffee", "I like hiking", "you can call me Evan"]):
            mem.add(text, user_id="u1", event_time=1_700_000_000.0 + i * 86400)
        mem.consolidate()
        seen.append(sorted(f.text for f in mem._all_facts()))
    assert seen[0] == seen[1] == seen[2], f"fact set varied across runs: {seen}"
    # the name arrives last chronologically, so earlier facts keep the pre-name subject -- the point is
    # that this is the SAME every run, not which subject wins.
    assert all("coffee" in " ".join(s) for s in seen)
