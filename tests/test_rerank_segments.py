"""Segment-level reranking of long documents.

A cross-encoder reads ~512 tokens. Given a whole ~2000-token session it does not error -- it scores the
first quarter and silently ignores the rest, so a session whose answer sits late ranks as irrelevant.
`test_answer_late_in_a_long_document_still_ranks_first` reproduces exactly that and would fail against the
old whole-document path.

No cross-encoder is loaded here: the reranker is a stub that scores by query-term overlap, which is enough
to exercise every decision `rerank_long` makes and keeps the suite runnable with no heavy dependency.
"""
from __future__ import annotations

from engram.retrieve.rerank import rerank_long, segment_text


class WindowedReranker:
    """A stand-in cross-encoder with a hard reading window, like the real one.

    `window` words are read and the rest is dropped, reproducing the truncation that makes whole-document
    reranking wrong. Score is query-term overlap over the part it can see.
    """

    def __init__(self, window: int = 400) -> None:
        self.window = window
        self.seen: list[str] = []

    def rerank(self, query, candidates, top_k):
        terms = set(query.lower().split())
        scored = []
        for cid, text in candidates:
            self.seen.append(text)
            visible = " ".join(text.split()[: self.window]).lower()
            scored.append((cid, float(sum(visible.count(t) for t in terms))))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]


def _doc(filler_words: int, needle: str = "", needle_at: str = "end") -> str:
    filler = " ".join(f"w{i}" for i in range(filler_words))
    if not needle:
        return filler
    return f"{needle} {filler}" if needle_at == "start" else f"{filler} {needle}"


# --- segment_text ---


def test_short_text_is_one_segment():
    """Documents already inside the window must be untouched, so short candidates behave as before."""
    assert segment_text("alice works at acme", max_words=300) == ["alice works at acme"]


def test_empty_text_yields_no_segments():
    assert segment_text("", 300) == []
    assert segment_text("   \n  ", 300) == []


def test_long_text_is_split_within_budget():
    segments = segment_text(_doc(1000), max_words=300)
    assert len(segments) > 1
    assert all(len(s.split()) <= 300 for s in segments)


def test_split_preserves_every_word():
    """Segmentation must not drop content -- that would be the truncation bug in another costume."""
    text = _doc(1000, needle="the answer is lisbon")
    assert " ".join(segment_text(text, 300)).split() == text.split()


def test_unpunctuated_wall_of_text_is_still_bounded():
    """A single sentence longer than the budget has no natural boundary; it must still be cut."""
    segments = segment_text(" ".join(f"w{i}" for i in range(900)), max_words=300)
    assert all(len(s.split()) <= 300 for s in segments)


def test_sentences_are_packed_not_slivered():
    """Short sentences should be merged up to the budget, not emitted one per segment."""
    text = " ".join(f"Sentence number {i} here." for i in range(60))
    segments = segment_text(text, max_words=100)
    assert len(segments) < 60
    assert all(len(s.split()) <= 100 for s in segments)


# --- rerank_long ---


def test_answer_late_in_a_long_document_still_ranks_first():
    """The regression this exists to prevent.

    The answer-bearing document hides its match past the model's window; the decoy repeats an unrelated
    filler term early. Scoring whole documents reads only the opening of each, so the decoy wins.
    """
    query = "lisbon"
    answer_doc = _doc(900, needle="lisbon lisbon lisbon", needle_at="end")
    decoy_doc = _doc(900)

    reranker = WindowedReranker(window=400)
    whole = reranker.rerank(query, [("decoy", decoy_doc), ("answer", answer_doc)], 2)
    assert whole[0][0] == "decoy", "precondition: whole-document scoring must miss the late answer"

    segmented = rerank_long(reranker, query, [("decoy", decoy_doc), ("answer", answer_doc)], 2)
    assert segmented[0][0] == "answer"


def test_every_segment_fits_the_reading_window():
    reranker = WindowedReranker(window=400)
    rerank_long(reranker, "lisbon", [("a", _doc(2000))], 1, max_words=300)
    assert reranker.seen, "the reranker should have been called"
    assert all(len(text.split()) <= 300 for text in reranker.seen)


def test_document_scores_as_its_best_segment_not_its_average():
    """One strong passage in a long document must outrank a uniformly mediocre one."""
    query = "lisbon"
    spike = _doc(600, needle="lisbon lisbon lisbon lisbon", needle_at="end")
    diffuse = " ".join(["lisbon"] + [f"w{i}" for i in range(600)])

    ranked = rerank_long(WindowedReranker(2000), query, [("diffuse", diffuse), ("spike", spike)], 2)
    assert ranked[0][0] == "spike"


def test_empty_and_blank_candidates_are_handled():
    assert rerank_long(WindowedReranker(), "q", [], 5) == []
    assert rerank_long(WindowedReranker(), "q", [("a", "   ")], 5) == []


def test_top_k_is_respected_and_ties_keep_incoming_order():
    """With nothing to separate them, the upstream ranking decides -- reranking must not shuffle."""
    docs = [(f"d{i}", "neutral text with no query terms") for i in range(5)]
    ranked = rerank_long(WindowedReranker(), "lisbon", docs, 3)
    assert [cid for cid, _ in ranked] == ["d0", "d1", "d2"]


def test_ids_survive_round_trip():
    """Segment keys are internal; callers must get their own ids back (memory.py passes list indices)."""
    ranked = rerank_long(WindowedReranker(), "lisbon", [(7, _doc(800, needle="lisbon"))], 1)
    assert ranked[0][0] == 7


# --- wiring ---


def test_memory_reranks_sessions_by_segment():
    """The integration point where the defect actually lived.

    Memory.retrieve_episodes passed whole `ep.content` to the reranker, and nothing covered that path.
    A session whose answer sits past the reading window must still be retrieved.
    """
    from engram.memory import Memory

    mem = Memory(reranker=WindowedReranker(window=400))
    answer = _doc(900, needle="the conference was in lisbon", needle_at="end")
    for i in range(4):
        mem.add(_doc(900) if i else answer, user_id="u1", session_id=f"s{i}")

    eps = mem.retrieve_episodes("lisbon", "u1", k=1)
    assert eps, "reranked retrieval must return something"
    assert "lisbon" in eps[0].content, "the session answering the query must survive reranking"
