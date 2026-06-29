"""Hybrid retrieval (CLAUDE.md §3.3): fuse dense-semantic + BM25-lexical + graph-proximity + recency +
salience over the live fact set, combined with weighted Reciprocal Rank Fusion."""
from __future__ import annotations

from typing import Optional

from ..config import Config
from ..consolidate.conflict import is_single_valued
from ..embed import Embedder
from ..store import GraphStore, VectorStore
from ..types import Fact
from ..util import cosine, fmt_date, now, recency, tokenize
from .fusion import order_by_score, weighted_rrf
from .lexical import bm25_scores, stem, stems

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_GRAPH_HOP_DECAY = 0.65


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
_GRAPH_PREDICATE_ALIASES: dict[str, tuple[str, ...]] = {
    "works_at": ("work", "works", "working", "employer", "employed", "employment", "company", "job"),
    "occupation": ("profession", "role", "title", "job", "career"),
    "lives_in": ("live", "lives", "living", "reside", "resides", "city", "home", "location", "where"),
    "based_in": ("based", "base", "located", "location", "headquarter", "headquarters", "hq", "city", "where"),
    "located_in": ("located", "location", "based", "headquarter", "headquarters", "hq", "city", "where"),
    "colleague": ("colleague", "coworker", "co-worker", "workmate"),
    "sister": ("sister", "sibling"),
    "brother": ("brother", "sibling"),
    "spouse": ("spouse", "wife", "husband", "partner"),
    "wife": ("spouse", "wife", "partner"),
    "husband": ("spouse", "husband", "partner"),
    "manager": ("manager", "boss", "lead"),
    "likes": ("like", "likes", "favorite", "interest"),
    "favorite": ("favorite", "favourite", "likes", "prefers"),
}
_GRAPH_QUERY_CUE_WORDS = frozenset(
    word
    for words in _GRAPH_PREDICATE_ALIASES.values()
    for alias in words
    for word in stems(alias)
) | frozenset({"where", "who", "whose", "which"})


def order_positive(scores: dict[str, float]) -> list[str]:
    """Rank only real evidence hits.

    RRF gives every ranked item a positive contribution. For evidence signals (semantic, lexical, graph),
    a zero score means "this signal found no evidence", not "last place evidence". Filtering zeros keeps
    priors like salience/recency from borrowing fake support through arbitrary zero-score ordering.
    """
    return [doc_id for doc_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True) if score > 0.0]


def fact_type_weight(fact: Fact, config: Config) -> float:
    """Retrieval multiplier by fact type: preference > identity > incidental. A durable 'who they are /
    what they like' fact should outrank a one-off mention when both match a query."""
    p = fact.predicate.lower()
    if p in _PREFERENCE_PREDS or p.startswith("favorite"):
        return config.w_type_preference
    if p in _IDENTITY_PREDS:
        return config.w_type_identity
    return 1.0


def _graph_predicate_terms(predicate: str) -> set[str]:
    pred = predicate.lower()
    terms = set(stems(pred.replace("_", " ")))
    for alias in _GRAPH_PREDICATE_ALIASES.get(pred, ()):
        terms.update(stems(alias))
    return terms


def graph_relation_relevance(query: str, fact: Fact) -> float:
    """How well this fact's relation matches the query's relation intent.

    Graph proximity finds *nearby* facts; this score makes it query-conditioned, so a company/location
    question prefers works_at/based_in edges over same-node distractors such as likes/owns. If the query
    contains no relation cue ("tell me about Wei"), callers should skip relation weighting entirely.
    """
    q_terms = set(stems(query)) | {stem(t) for t in tokenize(query)}
    pred_terms = _graph_predicate_terms(fact.predicate)
    if q_terms & pred_terms:
        return 1.0
    text_terms = set(stems(f"{fact.subject} {fact.object} {fact.text}"))
    if q_terms & text_terms:
        return 0.75
    return 0.0


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
        if not self.config.graph_proximity:
            return {f.id: 0.0 for f in live}, qids
        live_fact_ids = {f.id for f in live}
        node_scores: dict[str, float] = {eid: 1.0 for eid in qids}
        frontier = set(qids)

        for depth in range(1, max(0, self.config.max_hops) + 1):
            hop_score = _GRAPH_HOP_DECAY ** depth
            next_frontier: set[str] = set()
            for eid in frontier:
                for direction in ("out", "in"):
                    for rel in self.graph.neighbors(eid, as_of, direction):
                        if rel.fact_id not in live_fact_ids:
                            continue
                        neighbor_id = rel.object_id if direction == "out" else rel.subject_id
                        if node_scores.get(neighbor_id, 0.0) >= hop_score:
                            continue
                        node_scores[neighbor_id] = hop_score
                        next_frontier.add(neighbor_id)
            if not next_frontier:
                break
            frontier = next_frontier

        scores: dict[str, float] = {}
        q_terms = set(stems(query)) | {stem(t) for t in tokenize(query)}
        use_relation_weight = self.config.graph_relation_awareness and bool(q_terms & _GRAPH_QUERY_CUE_WORDS)
        for f in live:
            subj = self.graph.get_entity(f.user_id, f.subject)
            obj = self.graph.get_entity(f.user_id, f.object)
            sid = subj.id if subj else None
            oid = obj.id if obj else None
            score = max(node_scores.get(sid, 0.0), node_scores.get(oid, 0.0))
            if use_relation_weight and score > 0.0:
                score *= 0.55 + 0.45 * graph_relation_relevance(query, f)
            scores[f.id] = score
        return scores, qids

    def _current_slot_heads(self, facts: list[Fact]) -> list[Fact]:
        """For single-valued (current-state) slots, retrieve only the slot head.

        Conflict resolution should normally invalidate stale slot values before retrieval. This is a
        defensive read-path guard for duplicate live payloads from partial backends or manual imports.
        The cardinality test is `is_single_valued` (the same predicate classifier conflict resolution
        uses): any non-accumulating predicate — works_at, lives_in, studies, salary, attends_yoga — has
        one current value per slot, so two live facts in the same slot must not compete in fusion.
        Accumulating predicates (likes, owns, visited, ...) are multi-valued and exempt.
        """
        heads: dict[tuple[str, str, str], Fact] = {}
        for fact in facts:
            if not is_single_valued(fact.predicate):
                continue
            cur = heads.get(fact.slot)
            # Manual facts are authoritative; otherwise the latest valid/current observation is the head.
            if cur is None or (
                fact.source == "user",
                fact.valid_at,
                fact.created_at,
                fact.id,
            ) > (
                cur.source == "user",
                cur.valid_at,
                cur.created_at,
                cur.id,
            ):
                heads[fact.slot] = fact
        return [
            fact
            for fact in facts
            if not is_single_valued(fact.predicate) or heads.get(fact.slot) is fact
        ]

    def retrieve(
        self, query: str, user_id: str, as_of: Optional[float] = None, top_k: Optional[int] = None
    ) -> tuple[list[tuple[Fact, float]], dict]:
        top_k = top_k or self.config.top_k
        live = [f for f in self.fact_store.values() if f.user_id == user_id and f.is_live(as_of)]
        live = self._current_slot_heads(live)
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
            "sem": order_positive(sem),
            "lex": order_positive(lex),
            "graph": order_positive(gph),
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
