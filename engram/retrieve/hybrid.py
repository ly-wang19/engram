"""Hybrid retrieval (CLAUDE.md §3.3): fuse dense-semantic + BM25-lexical + graph-proximity + recency +
salience over the live fact set, combined with weighted Reciprocal Rank Fusion."""
from __future__ import annotations

from typing import Optional

from ..config import Config
from ..embed import Embedder
from ..store import GraphStore, VectorStore
from ..types import Fact
from ..util import cosine, fmt_date, now, recency, tokenize
from .fusion import order_by_score, weighted_rrf
from .lexical import bm25_scores, stem, stems

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")


def date_terms(epoch: float) -> str:
    """Render a fact's date as searchable tokens (year, numeric month, month name) so a query that names
    a time ('May 2023', 'in 2024') matches the right-dated facts via BM25 — dates otherwise live only in
    valid_at and are invisible to retrieval. This is query-time temporal matching done as a lexical signal
    (MemoryScope time_ratio in spirit), with no score multiplier that could override relevance."""
    try:
        d = fmt_date(epoch)  # YYYY-MM-DD
        y, m, _ = d.split("-")
        return f"{d} {y} {m} {_MONTHS[int(m) - 1]}"
    except Exception:  # noqa: BLE001
        return ""

# Predicates that mark a durable identity or preference fact (vs. an incidental event mention). Used for
# type-weighted fusion — these get a retrieval boost (CLAUDE.md §3.3; MemoryScope/OMEGA convergent finding).
_PREFERENCE_PREDS = frozenset({
    "likes", "dislikes", "prefers", "avoids", "loves", "hates", "enjoys", "wants",
    "allergic_to", "interested_in", "favorite", "prefers_to",
})
_IDENTITY_PREDS = frozenset({
    "name", "works_at", "lives_in", "born_in", "married_to", "occupation", "age",
    "studied_at", "owns", "has", "speaks",
})


def fact_type_weight(fact: Fact, config: Config) -> float:
    """Retrieval multiplier by fact type: preference > identity > incidental. A durable 'who they are /
    what they like' fact should outrank a one-off mention when both match a query."""
    p = fact.predicate.lower()
    if p in _PREFERENCE_PREDS or p.startswith("favorite"):
        return config.w_type_preference
    if p in _IDENTITY_PREDS:
        return config.w_type_identity
    return 1.0


class HybridRetriever:
    def __init__(self, fact_store: VectorStore, graph: GraphStore, embedder: Embedder, config: Config) -> None:
        self.fact_store = fact_store
        self.graph = graph
        self.embedder = embedder
        self.config = config
        # Type weighting needs a real semantic signal; the offline HashingEmbedder's cosines are noise, so
        # gate it off there (same pattern as the conflict resolver) — keeps the zero-dep demo deterministic.
        from ..embed import HashingEmbedder
        self._semantic = not isinstance(embedder, HashingEmbedder)

    def query_entity_ids(self, query: str, user_id: str) -> set[str]:
        """Entity nodes whose full name appears in the query (the query's anchor entities)."""
        q = set(stems(query)) | set(tokenize(query))
        ids: set[str] = set()
        for ent in self.graph.entities.values():
            if ent.user_id != user_id:
                continue
            name_toks = [stem(t) for t in tokenize(ent.name)]
            if name_toks and all(t in q for t in name_toks):
                ids.add(ent.id)
        return ids

    def _graph_scores(
        self, query: str, user_id: str, live: list[Fact], as_of: Optional[float]
    ) -> tuple[dict[str, float], set[str]]:
        qids = self.query_entity_ids(query, user_id)
        one_hop: set[str] = set()
        for eid in qids:
            for rel in self.graph.neighbors(eid, as_of, "out"):
                one_hop.add(rel.object_id)
            for rel in self.graph.neighbors(eid, as_of, "in"):
                one_hop.add(rel.subject_id)
        scores: dict[str, float] = {}
        for f in live:
            subj = self.graph.get_entity(f.user_id, f.subject)
            obj = self.graph.get_entity(f.user_id, f.object)
            sid = subj.id if subj else None
            oid = obj.id if obj else None
            if sid in qids or oid in qids:
                scores[f.id] = 1.0
            elif sid in one_hop or oid in one_hop:
                scores[f.id] = 0.5
            else:
                scores[f.id] = 0.0
        return scores, qids

    def retrieve(
        self, query: str, user_id: str, as_of: Optional[float] = None, top_k: Optional[int] = None
    ) -> tuple[list[tuple[Fact, float]], dict]:
        top_k = top_k or self.config.top_k
        live = [f for f in self.fact_store.values() if f.user_id == user_id and f.is_live(as_of)]
        if not live:
            return [], {"sem": {}, "lex": {}, "qids": set()}

        qvec = self.embedder.embed(query)
        sem = {f.id: cosine(qvec, f.embedding or []) for f in live}
        # Type-weighted retrieval: scale the SEMANTIC score by fact type. Because an off-topic fact has
        # sem≈0, the multiplier only reorders among genuinely-relevant candidates (a preference fact beats
        # an equally-relevant incidental one) and can never lift an irrelevant fact. Gated to real
        # embeddings — the hashing fallback's cosines are noise and a multiplier there would misrank.
        if self._semantic:
            for f in live:
                tw = fact_type_weight(f, self.config)
                if tw != 1.0:
                    sem[f.id] *= tw
        # include each fact's date as searchable tokens so time-named queries ('May 2023') match by date
        lex = bm25_scores(query, [(f.id, f"{f.text} {date_terms(f.valid_at)}") for f in live])
        gph, qids = self._graph_scores(query, user_id, live, as_of)
        t = now() if as_of is None else as_of
        rec = {f.id: recency(max(0.0, t - f.valid_at), self.config.recency_tau_days) for f in live}
        sal = {f.id: f.salience for f in live}

        rankings = {
            "sem": order_by_score(sem),
            "lex": order_by_score(lex),
            "graph": order_by_score(gph),
            "rec": order_by_score(rec),
            "sal": order_by_score(sal),
        }
        weights = {
            "sem": self.config.w_sem,
            "lex": self.config.w_lex,
            "graph": self.config.w_graph,
            "rec": self.config.w_rec,
            "sal": self.config.w_sal,
        }
        fused = weighted_rrf(rankings, weights, self.config.rrf_k)
        ranked = sorted(live, key=lambda f: fused.get(f.id, 0.0), reverse=True)[:top_k]
        diag = {"sem": sem, "lex": lex, "fused": fused, "qids": qids}
        return [(f, fused.get(f.id, 0.0)) for f in ranked], diag
