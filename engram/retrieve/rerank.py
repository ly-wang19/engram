"""Cross-encoder reranker (CLAUDE.md L1 strong retrieval). A bi-encoder (BGE) gives a cheap candidate
pool; a cross-encoder rescores (query, passage) jointly for far better precision on which sessions/chunks
actually answer the question. This is the highest-leverage retrieval upgrade for LongMemEval _S.

A cross-encoder reads a bounded window — 512 tokens for the BGE rerankers. Hand it a whole LongMemEval
session (~2000 tokens) and it does not fail; it silently scores the first quarter and discards the rest,
so a session whose answer sits in its second half ranks as if it were irrelevant. `rerank_long` scores at
a granularity the model can actually read and keeps each document's best segment, which is what makes
reranking safe to apply to raw sessions rather than only to short facts.
"""
from __future__ import annotations

import re
from typing import Any, Optional

__all__ = ["CrossEncoderReranker", "segment_text", "rerank_long"]

# Paragraph first, then sentence: splitting mid-sentence would hand the model a fragment whose meaning
# depends on text it cannot see, which is the same failure as truncation, just smaller.
_PARA_RE = re.compile(r"\n\s*\n")
_SENT_RE = re.compile(r"(?<=[.!?。！？])\s+")


def segment_text(text: str, max_words: int = 300) -> list[str]:
    """Split `text` into segments of at most `max_words` words, preferring natural boundaries.

    Word count is a proxy for the model's token budget: ~300 words is comfortably inside a 512-token
    window once the query and special tokens are added. Text already short enough comes back as a single
    segment, so short candidates behave exactly as they did before.
    """
    if not text or not text.strip():
        return []
    words = text.split()
    if len(words) <= max_words:
        return [text.strip()]

    segments: list[str] = []
    for block in _split_units(text):
        block_words = block.split()
        if not block_words:
            continue
        if len(block_words) <= max_words:
            _append_or_merge(segments, block, max_words)
            continue
        # A single unit longer than the budget (an unpunctuated wall of text): cut on word count. Better
        # a hard cut than handing the model something it will truncate invisibly.
        for start in range(0, len(block_words), max_words):
            segments.append(" ".join(block_words[start:start + max_words]))
    return segments or [" ".join(words[:max_words])]


def _split_units(text: str) -> list[str]:
    units: list[str] = []
    for para in _PARA_RE.split(text):
        para = para.strip()
        if not para:
            continue
        units.extend(s for s in (part.strip() for part in _SENT_RE.split(para)) if s)
    return units


def _append_or_merge(segments: list[str], unit: str, max_words: int) -> None:
    """Pack consecutive units together while they fit, so segments are as informative as the budget
    allows instead of one-sentence slivers."""
    if segments and len(segments[-1].split()) + len(unit.split()) <= max_words:
        segments[-1] = f"{segments[-1]} {unit}"
    else:
        segments.append(unit)


def rerank_long(
    reranker: Any,
    query: str,
    candidates: list[tuple[Any, str]],
    top_k: int,
    max_words: int = 300,
) -> list[tuple[Any, float]]:
    """Rerank documents that may exceed the model's window, by scoring their segments.

    A document scores as its BEST segment, not its average: a long session earns its place because one
    passage answers the question, and averaging would dilute exactly the signal being looked for.

    Takes any object with a `.rerank(query, [(id, text)], top_k)` method rather than a concrete type, so
    the segmentation is testable without loading a cross-encoder — the zero-setup invariant means the
    default test path cannot import sentence-transformers.
    """
    if not candidates:
        return []

    pieces: list[tuple[str, str]] = []
    owner: dict[str, Any] = {}
    order: dict[Any, int] = {}
    for position, (cid, text) in enumerate(candidates):
        order.setdefault(cid, position)
        for seg_index, segment in enumerate(segment_text(text, max_words)):
            key = f"{position}:{seg_index}"
            owner[key] = cid
            pieces.append((key, segment))
    if not pieces:
        return []

    # Score every segment: the reranker truncates its own return value to top_k, and a document's best
    # segment can sit anywhere in that list.
    scored = reranker.rerank(query, pieces, len(pieces))

    best: dict[Any, float] = {}
    for key, score in scored:
        cid = owner.get(key)
        if cid is None:
            continue
        if cid not in best or score > best[cid]:
            best[cid] = score
    # Ties fall back to the incoming order, so the caller's upstream ranking still decides.
    ranked = sorted(best.items(), key=lambda item: (-item[1], order.get(item[0], 0)))
    return ranked[:top_k]


class CrossEncoderReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-base", device: Optional[str] = None,
                 max_length: int = 512) -> None:
        from sentence_transformers import CrossEncoder  # heavy, lazy

        self.model = CrossEncoder(model_name, max_length=max_length, device=device)

    def rerank(self, query: str, candidates: list[tuple[str, str]], top_k: int) -> list[tuple[str, float]]:
        """candidates: [(id, text), ...] -> [(id, score), ...] best-first, truncated to top_k."""
        if not candidates:
            return []
        scores = self.model.predict([(query, text) for _, text in candidates], show_progress_bar=False)
        order = sorted(range(len(candidates)), key=lambda i: float(scores[i]), reverse=True)
        return [(candidates[i][0], float(scores[i])) for i in order[:top_k]]
