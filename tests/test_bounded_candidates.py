"""Bounded candidate retrieval (CLAUDE.md Bet E).

The load-bearing test is `test_bounded_matches_full_scan_when_pool_covers_store`: with a pool large
enough to hold every fact, bounded retrieval must return exactly what the full scan returns. That is
what makes the feature a speed optimisation rather than a silent ranking change — and it is the guard
that would catch a future edit to one path but not the other.
"""
from __future__ import annotations

from engram.config import Config
from engram.embed.hashing import HashingEmbedder
from engram.retrieve.hybrid import HybridRetriever
from engram.store.indexed import FactIndex, IndexedVectorStore
from engram.store.memory_store import InMemoryGraphStore, InMemoryVectorStore
from engram.types import Fact
from engram.util import now

FACTS = [
    ("alice", "works_at", "acme corp", "alice works at acme corp"),
    ("alice", "lives_in", "berlin", "alice lives in berlin"),
    ("alice", "prefers", "oat milk", "alice prefers oat milk in coffee"),
    ("bob", "works_at", "globex", "bob works at globex"),
    ("bob", "visited", "kyoto", "bob visited kyoto in spring"),
    ("carol", "studied", "linear algebra", "carol studied linear algebra at university"),
    ("carol", "owns", "a road bike", "carol owns a road bike"),
    ("dave", "avoids", "crowded cafes", "dave avoids crowded cafes"),
]


def _facts(embedder: HashingEmbedder, user_id: str = "u1") -> list[Fact]:
    t = now()
    return [
        Fact(
            user_id=user_id,
            subject=subj,
            predicate=pred,
            object=obj,
            text=text,
            valid_at=t - i * 86400.0,
            embedding=embedder.embed(text),
        )
        for i, (subj, pred, obj, text) in enumerate(FACTS)
    ]


def _stores():
    """One set of facts loaded into both an undecorated and a decorated store.

    Building two independent fact sets would compare different data — different ids, and a different
    `now()` so different dates, recency and lexical date terms.
    """
    embedder = HashingEmbedder()
    facts = _facts(embedder)
    plain = InMemoryVectorStore()
    boxed = IndexedVectorStore(InMemoryVectorStore())
    for f in facts:
        plain.upsert(f.id, f.embedding or [], f)
        boxed.upsert(f.id, f.embedding or [], f)
    return plain, boxed, embedder


def _retrieve(store, embedder, query, *, bounded, pool=400, user_id="u1"):
    config = Config(bounded_candidates=bounded, candidate_pool=pool)
    retriever = HybridRetriever(store, InMemoryGraphStore(), embedder, config)
    ranked, _diag = retriever.retrieve(query, user_id, top_k=5)
    return [(f.id, round(score, 9)) for f, score in ranked]


QUERIES = [
    "where does alice work",
    "what does alice prefer",
    "who visited kyoto",
    "what did carol study",
    "bike",
]


def test_bounded_matches_full_scan_when_pool_covers_store():
    """The safety property: a pool wider than the store must not change a single result."""
    plain, boxed, embedder = _stores()
    for query in QUERIES:
        full = _retrieve(plain, embedder, query, bounded=False)
        bounded = _retrieve(boxed, embedder, query, bounded=True, pool=400)
        assert bounded == full, f"bounded retrieval diverged from full scan on {query!r}"


def test_bounded_is_a_noop_without_an_index():
    """Turning the flag on against an undecorated store must fall back, not crash or silently empty."""
    plain, _boxed, embedder = _stores()
    for query in QUERIES:
        assert _retrieve(plain, embedder, query, bounded=True) == _retrieve(
            plain, embedder, query, bounded=False
        )


def test_bounded_still_returns_results_with_a_tiny_pool():
    """A pool smaller than the store is allowed to rank differently, but must stay useful."""
    _plain, boxed, embedder = _stores()
    ranked = _retrieve(boxed, embedder, "where does alice work", bounded=True, pool=2)
    assert ranked, "a small pool must still retrieve something"


def test_slot_completion_keeps_superseded_facts_from_surviving():
    """A single-valued slot's head must be pulled in even when only a stale slot-mate matched.

    Without slot completion `_current_slot_heads` would see one lonely stale fact, treat it as its own
    head, and let a superseded value through — the read path's non-destructive-invalidation guarantee.
    """
    embedder = HashingEmbedder()
    store = IndexedVectorStore(InMemoryVectorStore())
    t = now()
    old = Fact(
        user_id="u1", subject="alice", predicate="works_at", object="acme corp",
        text="alice works at acme corp", valid_at=t - 400 * 86400.0,
        embedding=embedder.embed("alice works at acme corp"),
    )
    new = Fact(
        user_id="u1", subject="alice", predicate="works_at", object="initech",
        text="alice works at initech", valid_at=t,
        embedding=embedder.embed("alice works at initech"),
    )
    for f in (old, new):
        store.upsert(f.id, f.embedding or [], f)

    config = Config(bounded_candidates=True, candidate_pool=400)
    retriever = HybridRetriever(store, InMemoryGraphStore(), embedder, config)
    # Query only the OLD value's distinctive term, so the stale fact is what the lexical channel finds.
    candidates = retriever._bounded_candidates("acme", "u1", None, embedder.embed("acme"))
    ids = {f.id for f in candidates}
    assert old.id in ids
    assert new.id in ids, "slot head must be pulled in alongside a matched slot-mate"

    ranked, _ = retriever.retrieve("acme", "u1", top_k=5)
    assert old.id not in {f.id for f, _ in ranked}, "superseded slot value must not be retrieved"


def test_index_tracks_updates_and_deletes():
    index = FactIndex()
    embedder = HashingEmbedder()
    f = Fact(
        user_id="u1", subject="alice", predicate="works_at", object="acme",
        text="alice works at acme", valid_at=now(), embedding=embedder.embed("x"),
    )
    index.add(f.id, f)
    assert index.n_docs == 1
    assert index.lexical_candidates("acme", 10, user_id="u1") == {f.id}

    # Re-adding the same id is an update: the old terms must not linger as a phantom document.
    f.text = "alice works at initech"
    index.add(f.id, f)
    assert index.n_docs == 1, "re-upsert must update, not duplicate"
    assert index.lexical_candidates("acme", 10, user_id="u1") == set()
    assert index.lexical_candidates("initech", 10, user_id="u1") == {f.id}

    index.remove(f.id)
    assert index.n_docs == 0
    assert index.lexical_candidates("initech", 10, user_id="u1") == set()
    assert index.postings == {}, "posting lists must not retain empty entries"
    assert index.payloads == {}


def test_index_scopes_candidates_and_corpus_by_user():
    """Tenants must not see each other's facts, nor pollute each other's IDF."""
    index = FactIndex()
    embedder = HashingEmbedder()
    ids = {}
    for user in ("u1", "u2"):
        f = Fact(
            user_id=user, subject="alice", predicate="works_at", object="acme",
            text="alice works at acme", valid_at=now(), embedding=embedder.embed("x"),
        )
        index.add(f.id, f)
        ids[user] = f.id

    assert index.lexical_candidates("acme", 10, user_id="u1") == {ids["u1"]}
    assert index.lexical_candidates("acme", 10, user_id="u2") == {ids["u2"]}

    corpus = index.corpus_for("u1", ["acme"])
    assert corpus.n_docs == 1, "corpus size must count only this tenant's facts"
    assert corpus.df["acme"] == 1, "document frequency must not count the other tenant's copy"


def test_decorator_preserves_store_behaviour():
    """The decorator must be transparent: same reads, same values, delete still deletes."""
    inner = InMemoryVectorStore()
    boxed = IndexedVectorStore(inner)
    embedder = HashingEmbedder()
    f = Fact(
        user_id="u1", subject="alice", predicate="works_at", object="acme",
        text="alice works at acme", valid_at=now(), embedding=embedder.embed("x"),
    )
    boxed.upsert(f.id, f.embedding or [], f)
    assert boxed.get(f.id) is f
    assert boxed.values() == inner.values()
    assert boxed.search(f.embedding or [], 5)[0][1] is f

    boxed.delete(f.id)
    assert boxed.get(f.id) is None
    assert boxed.index.n_docs == 0


def test_decorator_adopts_a_prepopulated_store():
    """Wrapping a store that already holds facts must index them, not start blind."""
    inner = InMemoryVectorStore()
    embedder = HashingEmbedder()
    f = Fact(
        user_id="u1", subject="alice", predicate="works_at", object="acme",
        text="alice works at acme", valid_at=now(), embedding=embedder.embed("x"),
    )
    inner.upsert(f.id, f.embedding or [], f)
    boxed = IndexedVectorStore(inner)
    assert boxed.index.lexical_candidates("acme", 10, user_id="u1") == {f.id}
