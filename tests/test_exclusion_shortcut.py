"""The negation early-out in graph_excluded_entity_ids.

Every retrieval calls query_entity_ids(), which ends by calling graph_excluded_entity_ids(), which used
to scan every entity in the store. Most queries contain no negation at all, so that scan almost always
found nothing. The early-out skips it — but only because of one invariant, which the first test here
pins down: if the anchored matcher can fire on some slice of a text, the cheap cue matcher must fire on
the whole text. If a future edit adds a cue to one regex and not the other, that test fails rather than
silently dropping exclusions.
"""
from __future__ import annotations

from engram.config import Config
from engram.embed.hashing import HashingEmbedder
from engram.retrieve.hybrid import (
    _EXCLUSION_BEFORE_RE,
    _EXCLUSION_CUE_RE,
    HybridRetriever,
)
from engram.store.memory_store import InMemoryGraphStore, InMemoryVectorStore
from engram.types import Entity, Fact
from engram.util import now

# Strings chosen to exercise each cue, the word-gap forms, the Chinese cues, and near-misses.
SAMPLES = [
    "not lisbon",
    "somewhere other than lisbon",
    "anywhere except berlin",
    "excluding berlin",
    "exclude the berlin office",
    "rather than berlin",
    "besides berlin",
    "cities not counting the two big ones berlin",
    "不是上海",
    "不在北京",
    "排除广州",
    "除了深圳",
    "where does alice work",
    "notable places she visited",
    "the exception was minor",
    "",
    "berlin",
    # A non-ASCII entity name carries no word-boundary guard, so the slice preceding it can end
    # mid-word. This is the case that forced the cue test to drop its trailing \b.
    "not上海",
    "except北京",
]


def test_cue_matcher_is_a_necessary_condition_for_the_anchored_matcher():
    """The invariant the early-out depends on. Checked over every prefix, since the real matcher runs
    against arbitrary slices of the query (the text preceding an entity mention)."""
    for text in SAMPLES:
        for end in range(len(text) + 1):
            slice_ = text[:end]
            if _EXCLUSION_BEFORE_RE.search(slice_):
                assert _EXCLUSION_CUE_RE.search(text), (
                    f"anchored matcher fired on {slice_!r} but the cue matcher misses {text!r}; "
                    "the early-out would drop this exclusion"
                )


def _retriever_with_entity(name: str) -> tuple[HybridRetriever, str]:
    graph = InMemoryGraphStore()
    ent = graph.upsert_entity(Entity(user_id="u1", name=name))
    embedder = HashingEmbedder()
    store = InMemoryVectorStore()
    f = Fact(
        user_id="u1", subject="alice", predicate="lives_in", object=name,
        text=f"alice lives in {name}", valid_at=now(), embedding=embedder.embed(name),
    )
    store.upsert(f.id, f.embedding or [], f)
    return HybridRetriever(store, graph, embedder, Config()), ent.id


def test_negated_entity_is_still_excluded():
    """The early-out must not weaken the feature it guards."""
    retriever, ent_id = _retriever_with_entity("lisbon")
    assert ent_id in retriever.graph_excluded_entity_ids("somewhere not lisbon", "u1")


def test_chinese_negation_still_excluded():
    """Chinese cues have no word boundaries; the tokenizer cannot see them, so the cue test must not
    depend on tokenization."""
    retriever, ent_id = _retriever_with_entity("上海")
    assert ent_id in retriever.graph_excluded_entity_ids("不是上海", "u1")


def test_plain_query_excludes_nothing():
    retriever, _ent_id = _retriever_with_entity("lisbon")
    assert retriever.graph_excluded_entity_ids("where does alice live", "u1") == set()


def test_entity_named_after_a_cue_word_is_not_self_excluded():
    """'not' appearing only as part of the entity's own name is not a negation of it."""
    retriever, ent_id = _retriever_with_entity("notion")
    assert ent_id not in retriever.graph_excluded_entity_ids("does alice use notion", "u1")
