"""A lexical/slot index over the fact store, and a VectorStore decorator that keeps it current.

Why this exists (CLAUDE.md Bet E — linear scaling). The read path scored *every* live fact on every
query: cosine over all embeddings, and — more expensively — re-tokenising and re-stemming every fact's
text to compute BM25 from scratch. Measured with `eval/scaling.py`, per-query cost tracked store size
exactly (constant ms per 1k facts), so a 100x larger store cost 81x more per query.

The fix is to score a *bounded candidate set* instead of the whole store. Two invariants make that a
speed optimisation rather than a silent ranking change:

  1. **Corpus statistics stay global.** BM25's IDF and average document length describe the collection,
     not the candidate subset. The index keeps them, so a candidate scores exactly what it would have
     scored in a full scan (see `lexical.bm25_scores(corpus=...)`).
  2. **Slots stay whole.** `_current_slot_heads` suppresses superseded facts by comparing facts that
     share a (user, subject, predicate) slot. If a candidate's slot-mates were missing, a stale fact
     could survive that a full scan would have filtered. The index can return a slot's full membership.

The decorator keeps every existing `.upsert()/.delete()` call site untouched — the index is maintained
where writes already land, not by threading a new dependency through eight callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..util import indexed_text, stems
from .base import Predicate, VectorStore

__all__ = ["FactIndex", "IndexedVectorStore"]


@dataclass(frozen=True)
class _UserCorpus:
    """Satisfies retrieve.lexical.CorpusStats for one tenant's slice of the index."""

    n_docs: int
    avgdl: float
    df: dict[str, int] = field(default_factory=dict)


def _fact_key(fact: Any) -> Optional[tuple[str, str, str]]:
    """A fact's conflict slot, or None for payloads that are not facts (episodes, summaries)."""
    slot = getattr(fact, "slot", None)
    return slot if isinstance(slot, tuple) else None


class FactIndex:
    """Inverted index + corpus statistics + slot membership for the facts in one store.

    Deliberately pure-stdlib: the charter's zero-setup invariant means the default path cannot depend on
    tantivy/bm25s. Those remain valid drop-in replacements behind the same three lookups this exposes
    (`lexical_candidates`, `slot_members`, `user_members`)."""

    def __init__(self) -> None:
        self.postings: dict[str, set[str]] = {}
        self.doc_len: dict[str, int] = {}
        self._doc_terms: dict[str, set[str]] = {}
        self._total_len: int = 0
        self._by_user: dict[str, set[str]] = {}
        self._by_slot: dict[tuple[str, str, str], set[str]] = {}
        # key -> (user_id, slot): lets remove() unhook a fact in O(1) instead of sweeping every bucket.
        self._doc_meta: dict[str, tuple[Optional[str], Optional[tuple[str, str, str]]]] = {}
        # key -> payload. Candidate selection produces ids; resolving them through the backend's get()
        # would be O(store) per id on LanceDB (it materialises the table and scans for the key), turning
        # a bounded pool back into a quadratic read. These are references to objects the store already
        # holds, so the cost is one dict entry per fact, not a second copy of the data.
        self.payloads: dict[str, Any] = {}
        # key -> insertion rank. Candidate ids arrive as an unordered set, but the scorers downstream are
        # rank-based (RRF), so ties are broken by list position: an unordered candidate list would make
        # the same query return different orderings run to run, and would diverge from the full scan even
        # when the pool covers the whole store. Ranking by insertion order reproduces the store's own
        # iteration order in O(k log k) over the candidates instead of O(store).
        self._seq: dict[str, int] = {}
        self._next_seq: int = 0

    # --- corpus statistics (the CorpusStats protocol in retrieve.lexical) ---

    @property
    def n_docs(self) -> int:
        return len(self.doc_len)

    @property
    def avgdl(self) -> float:
        return max(1.0, self._total_len / self.n_docs) if self.n_docs else 1.0

    @property
    def df(self) -> dict[str, int]:
        # Document frequency is exactly the posting-list length; materialising a parallel counter would
        # be one more thing to keep in sync for no gain at these sizes.
        return {term: len(ids) for term, ids in self.postings.items()}

    # --- maintenance ---

    def add(self, key: str, fact: Any) -> None:
        """Index (or re-index) one fact. Re-upserting the same id is an update, not a duplicate."""
        text = getattr(fact, "text", None)
        if not isinstance(text, str):
            return  # not a fact payload; nothing lexical to index
        # An update keeps its original position, matching dict semantics in the backing stores.
        seq = self._seq.get(key)
        self.remove(key)
        if seq is None:
            seq = self._next_seq
            self._next_seq += 1
        self._seq[key] = seq

        terms = stems(indexed_text(text, getattr(fact, "valid_at", 0.0) or 0.0))
        unique = set(terms)
        for term in unique:
            self.postings.setdefault(term, set()).add(key)
        self._doc_terms[key] = unique
        self.doc_len[key] = len(terms)
        self._total_len += len(terms)

        user = getattr(fact, "user_id", None)
        user = user if isinstance(user, str) else None
        if user is not None:
            self._by_user.setdefault(user, set()).add(key)
        slot = _fact_key(fact)
        if slot is not None:
            self._by_slot.setdefault(slot, set()).add(key)
        self._doc_meta[key] = (user, slot)
        self.payloads[key] = fact

    def remove(self, key: str) -> None:
        if key not in self.doc_len:
            return
        for term in self._doc_terms.pop(key, ()):  # only this doc's terms, not the whole vocabulary
            ids = self.postings.get(term)
            if ids is not None:
                ids.discard(key)
                if not ids:
                    del self.postings[term]
        self._total_len -= self.doc_len.pop(key, 0)
        self.payloads.pop(key, None)
        self._seq.pop(key, None)
        user, slot = self._doc_meta.pop(key, (None, None))
        if user is not None and (ids := self._by_user.get(user)) is not None:
            ids.discard(key)
            if not ids:
                del self._by_user[user]
        if slot is not None and (ids := self._by_slot.get(slot)) is not None:
            ids.discard(key)
            if not ids:
                del self._by_slot[slot]

    def clear(self) -> None:
        self.__init__()  # noqa: PLC2801 — re-initialising is the whole operation

    # --- lookups ---

    def user_members(self, user_id: str) -> set[str]:
        return set(self._by_user.get(user_id, ()))

    def corpus_for(self, user_id: str, terms: Iterable[str]) -> "_UserCorpus":
        """BM25 statistics for one user's facts, restricted to `terms` (all a scorer ever looks up).

        Scoping matters twice over. Per *user*, because the full-scan path scores one tenant's facts and
        an IDF polluted by other tenants would both leak signal and misrank. Per *term*, because building
        the whole vocabulary's document frequencies per query would be its own linear scan.

        One deliberate difference from the full scan: document frequency here counts a user's indexed
        facts, not only those live at `as_of`. When nothing has been invalidated the two are identical;
        once facts have been superseded, IDF is computed over the slightly larger historical corpus. That
        is the more stable statistic (a term's rarity should not jitter as facts age out) and it never
        reorders a single query's results, since every candidate is scored against the same corpus."""
        members = self._by_user.get(user_id) or set()
        n = len(members)
        total = sum(self.doc_len.get(key, 0) for key in members) if n else 0
        df = {}
        for term in set(terms):
            ids = self.postings.get(term)
            if ids:
                df[term] = len(ids & members)
        return _UserCorpus(n_docs=n, avgdl=max(1.0, total / n) if n else 1.0, df=df)

    def resolve(self, keys: Iterable[str]) -> list[Any]:
        """Candidate ids -> payloads in store order, skipping anything the index no longer holds.

        Store order (not set order) is what makes bounded retrieval reproducible and what lets it match
        the full scan exactly — see `_seq`."""
        found = [(self._seq.get(key, 0), key) for key in keys if key in self.payloads]
        found.sort()
        return [self.payloads[key] for _, key in found]

    def slot_members(self, slots: Iterable[tuple[str, str, str]]) -> set[str]:
        out: set[str] = set()
        for slot in slots:
            out |= self._by_slot.get(slot, set())
        return out

    def lexical_candidates(self, query: str, limit: int, user_id: Optional[str] = None) -> set[str]:
        """Facts sharing at least one query term, ranked by summed inverse document frequency.

        This is the recall half of the hybrid thesis (CLAUDE.md M1): a fact can be lexically strong and
        semantically weak — an exact name, a number, a date — and a vector-only candidate pool would drop
        it. Ranking by rarity rather than raw overlap keeps a single distinctive term (a proper noun)
        ahead of several common ones."""
        if limit <= 0:
            return set()
        allowed = self._by_user.get(user_id) if user_id is not None else None
        n = max(1, self.n_docs)
        score: dict[str, float] = {}
        for term in set(stems(query)):
            ids = self.postings.get(term)
            if not ids:
                continue
            # Rarer term -> larger weight. len(ids) is the term's document frequency.
            weight = n / len(ids)
            for key in ids:
                if allowed is not None and key not in allowed:
                    continue
                score[key] = score.get(key, 0.0) + weight
        if len(score) <= limit:
            return set(score)
        ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
        return {key for key, _ in ranked[:limit]}


class IndexedVectorStore(VectorStore):
    """Wraps any VectorStore and maintains a `FactIndex` alongside it.

    A decorator rather than a subclass so it composes with whichever backend is configured (in-memory,
    LanceDB, a future pgvector), and so persistence keeps working: `store.persist` replays facts through
    `.upsert()`, which rebuilds the index for free on load."""

    def __init__(self, inner: VectorStore) -> None:
        self.inner = inner
        self.index = FactIndex()
        for payload in inner.values():  # adopt a pre-populated store
            key = getattr(payload, "id", None)
            if isinstance(key, str):
                self.index.add(key, payload)

    def upsert(self, key: str, vector: list[float], payload: Any) -> None:
        self.inner.upsert(key, vector, payload)
        self.index.add(key, payload)

    def search(
        self,
        vector: list[float],
        top_k: int,
        where: Optional[Predicate] = None,
        *,
        user_id: Optional[str] = None,
    ) -> list[tuple[float, Any]]:
        return self.inner.search(vector, top_k, where, user_id=user_id)

    def get(self, key: str) -> Any | None:
        return self.inner.get(key)

    def delete(self, key: str) -> None:
        self.inner.delete(key)
        self.index.remove(key)

    def values(self) -> list[Any]:
        return self.inner.values()

    # Some backends expose extra surface (LanceDB's table handle, pickling hooks). Forward what we don't
    # wrap so decorating a store never removes capability. Guarded against being consulted before
    # __init__ has bound `inner` (unpickling calls __getattr__ before __dict__ is restored).
    def __getattr__(self, name: str) -> Any:
        inner = self.__dict__.get("inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)
