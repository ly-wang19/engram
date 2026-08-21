"""Opt-in ANN candidate generation (CLAUDE.md Bet E, scale read path). The guarantee tested here: it is
OFF by default, and when a small store's ANN pool covers all facts the candidate set equals the full scan
— so turning it on changes nothing for small users (the behavior-preserving property we can verify offline;
ranking quality at scale is eval-gated). Uses the in-memory store, so no LanceDB needed.

Text is kept to a form the offline rule extractor actually parses ("X and Y" single sentence), so a fact
genuinely lands and the equivalence check is non-vacuous."""
from __future__ import annotations

from engram.config import Config
from engram.embed import HashingEmbedder
from engram.memory import Memory

_TEXT = "My name is Wei and I live in Shenzhen."  # rule extractor -> (Wei, lives_in, Shenzhen)


def _mem(ann: bool) -> Memory:
    m = Memory(config=Config(ann_candidates=ann), embedder=HashingEmbedder(64))
    m.add(_TEXT, user_id="u", consolidate=True)
    return m


def _spo(ranked):
    return [(f.subject, f.predicate, f.object) for f, _ in ranked]


def test_ann_candidates_off_by_default():
    assert Config().ann_candidates is False


def test_ann_path_retrieves_the_fact():
    ranked, _ = _mem(ann=True).retriever.retrieve("where do I live", "u")
    assert ranked, "the ANN candidate path should return candidates"
    assert any("shenzhen" in ((f.object or "") + " " + f.text).lower() for f, _ in ranked)


def test_ann_equals_full_scan_on_small_store():
    # few facts (< ann_pool) -> the ANN pool covers them all -> identical candidate set -> identical ranking
    full, ann = _mem(ann=False), _mem(ann=True)
    for query in ("where do I live", "Wei", "Shenzhen"):
        assert _spo(full.retriever.retrieve(query, "u")[0]) == _spo(ann.retriever.retrieve(query, "u")[0]), \
            f"opt-in changed ranking for {query!r} on a small store"
    # non-vacuous: the fact really is retrieved (so the equalities above aren't just []==[])
    assert _spo(ann.retriever.retrieve("where do I live", "u")[0])
