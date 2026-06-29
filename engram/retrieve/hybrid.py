"""Hybrid retrieval (CLAUDE.md §3.3): fuse dense-semantic + BM25-lexical + graph-proximity + recency +
salience over the live fact set, combined with weighted Reciprocal Rank Fusion."""
from __future__ import annotations

import re
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
    "works_on": ("project", "work", "works", "working", "built", "building"),
    "project": ("project", "initiative", "work"),
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
_SELF_ANCHOR_RE = re.compile(
    r"\b(i|me|my|mine|myself|we|our|ours|user's|the user's|their|his|her)\b|我|我的|用户|用户的",
    re.IGNORECASE,
)
_ENTITY_ANCHOR_STOP = frozenset({
    "the", "and", "for", "with", "from", "inc", "ltd", "llc", "corp", "co", "company", "project",
    "user", "assistant", "team", "group", "system", "ai",
})
_EXCLUSION_BEFORE_RE = re.compile(
    r"(?:\bnot\b(?:\s+\w+){0,5}|\bexcept\b(?:\s+\w+){0,4}|\bexcluding\b(?:\s+\w+){0,4}|"
    r"\bexclude\b(?:\s+\w+){0,4}|\bother\s+than\b(?:\s+\w+){0,4}|"
    r"\brather\s+than\b(?:\s+\w+){0,4}|\bbesides\b(?:\s+\w+){0,4}|不是|不在|排除|除了)\s*$",
    re.IGNORECASE,
)
_EXCLUSION_VALUE_PREDS = frozenset({
    "based_in", "located_in", "headquartered_in", "lives_in", "works_at", "occupation",
    "likes", "dislikes", "prefers", "favorite", "owns", "has", "diet", "allergic_to",
})


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


def _entity_anchor_terms(name: str, aliases: list[str]) -> set[str]:
    terms: set[str] = set()
    for text in (name, *aliases):
        for tok in tokenize(text):
            term = stem(tok)
            if len(term) >= 3 and not term.isdigit() and term not in _ENTITY_ANCHOR_STOP:
                terms.add(term)
    return terms


def _entity_name_mentions(text: str, name: str) -> list[re.Match]:
    if not name.strip():
        return []
    escaped = re.escape(name.lower())
    if name.isascii():
        pattern = re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])", re.IGNORECASE)
    else:
        pattern = re.compile(escaped, re.IGNORECASE)
    return list(pattern.finditer(text))


def _is_excluded_mention(query_l: str, name: str) -> bool:
    for match in _entity_name_mentions(query_l, name):
        before = query_l[max(0, match.start() - 72):match.start()]
        if _EXCLUSION_BEFORE_RE.search(before):
            return True
    return False


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
        entities = [ent for ent in self.graph.entities.values() if ent.user_id == user_id]
        for ent in entities:
            names = (ent.name, *ent.aliases)
            if any((toks := [stem(t) for t in tokenize(name)]) and all(t in q for t in toks) for name in names):
                ids.add(ent.id)
        if self.config.graph_entity_alias_anchor:
            term_to_ids: dict[str, set[str]] = {}
            for ent in entities:
                for term in _entity_anchor_terms(ent.name, ent.aliases):
                    term_to_ids.setdefault(term, set()).add(ent.id)
            for term in q:
                hits = term_to_ids.get(term)
                if hits is not None and len(hits) == 1:
                    ids.update(hits)
        if self.config.graph_self_anchor and _SELF_ANCHOR_RE.search(query):
            for name in (user_id, "user"):
                ent = self.graph.get_entity(user_id, name)
                if ent is not None:
                    ids.add(ent.id)
        ids -= self.graph_excluded_entity_ids(query, user_id)
        return ids

    def graph_excluded_entity_ids(self, query: str, user_id: str) -> set[str]:
        """Entities explicitly ruled out by the query ("not in Lisbon", "except Zephyr", "不是上海").

        Negative constraints are applied before scoring so excluded values do not get boosted merely
        because the query names them. This keeps graph expansion useful for constrained multi-hop
        questions instead of surfacing the distractor path the user asked us to avoid.
        """
        if not (self.config.graph_proximity and self.config.graph_negative_constraints):
            return set()
        query_l = query.lower()
        direct: set[str] = set()
        entities = [ent for ent in self.graph.entities.values() if ent.user_id == user_id]
        for ent in entities:
            for name in (ent.name, *ent.aliases):
                if _is_excluded_mention(query_l, name):
                    direct.add(ent.id)
                    break
        term_to_ids: dict[str, set[str]] = {}
        for ent in entities:
            for term in _entity_anchor_terms(ent.name, ent.aliases):
                term_to_ids.setdefault(term, set()).add(ent.id)
        for term, hits in term_to_ids.items():
            if len(hits) == 1 and _is_excluded_mention(query_l, term):
                direct.update(hits)
        return direct

    def graph_exclusion_zone(self, query: str, user_id: str, as_of: Optional[float]) -> set[str]:
        """Directly excluded entities plus entities whose attribute value is excluded.

        Example: if the query says "project not in Lisbon", `Lisbon` is directly excluded and a
        `Zephyr --based_in--> Lisbon` edge excludes Zephyr from the candidate path. Relationship edges
        such as `Wei --colleague--> Lin` are not used for expansion, so "except Lin" does not erase Wei.
        """
        direct = self.graph_excluded_entity_ids(query, user_id)
        if not direct:
            return set()
        out = set(direct)
        for rel in self.graph.relations():
            if rel.predicate.lower() not in _EXCLUSION_VALUE_PREDS:
                continue
            live = as_of is None or (rel.valid_at <= as_of and (rel.invalid_at is None or rel.invalid_at > as_of))
            if live and rel.object_id in direct:
                out.add(rel.subject_id)
        return out

    def _fact_touches_entities(self, fact: Fact, entity_ids: set[str]) -> bool:
        if not entity_ids:
            return False
        subj = self.graph.get_entity(fact.user_id, fact.subject)
        obj = self.graph.get_entity(fact.user_id, fact.object)
        return (subj is not None and subj.id in entity_ids) or (obj is not None and obj.id in entity_ids)

    def _graph_scores(
        self, query: str, user_id: str, live: list[Fact], as_of: Optional[float]
    ) -> tuple[dict[str, float], set[str]]:
        qids = self.query_entity_ids(query, user_id)
        if not self.config.graph_proximity:
            return {f.id: 0.0 for f in live}, qids
        excluded = self.graph_exclusion_zone(query, user_id, as_of)
        qids -= excluded
        live_fact_ids = {f.id for f in live}
        node_scores: dict[str, float] = {eid: 1.0 for eid in qids}
        frontier: dict[str, float] = {eid: 1.0 for eid in qids}

        for depth in range(1, max(0, self.config.max_hops) + 1):
            next_frontier: dict[str, float] = {}
            for eid, parent_score in frontier.items():
                for direction in ("out", "in"):
                    for rel in self.graph.neighbors(eid, as_of, direction):
                        if rel.fact_id not in live_fact_ids:
                            continue
                        if rel.subject_id in excluded or rel.object_id in excluded:
                            continue
                        neighbor_id = rel.object_id if direction == "out" else rel.subject_id
                        if neighbor_id in qids:
                            continue
                        contribution = parent_score * _GRAPH_HOP_DECAY
                        if self.config.graph_path_reinforcement:
                            old_score = node_scores.get(neighbor_id, 0.0)
                            node_scores[neighbor_id] = min(1.0, old_score + contribution)
                            next_frontier[neighbor_id] = next_frontier.get(neighbor_id, 0.0) + contribution
                        else:
                            old_score = node_scores.get(neighbor_id, 0.0)
                            if old_score >= contribution:
                                continue
                            node_scores[neighbor_id] = contribution
                            next_frontier[neighbor_id] = max(next_frontier.get(neighbor_id, 0.0), contribution)
            if not next_frontier:
                break
            frontier = {eid: min(score, 1.0) for eid, score in next_frontier.items()}

        scores: dict[str, float] = {}
        q_terms = set(stems(query)) | {stem(t) for t in tokenize(query)}
        use_relation_weight = self.config.graph_relation_awareness and bool(q_terms & _GRAPH_QUERY_CUE_WORDS)
        for f in live:
            subj = self.graph.get_entity(f.user_id, f.subject)
            obj = self.graph.get_entity(f.user_id, f.object)
            sid = subj.id if subj else None
            oid = obj.id if obj else None
            if (sid is not None and sid in excluded) or (oid is not None and oid in excluded):
                scores[f.id] = 0.0
                continue
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
        excluded = self.graph_exclusion_zone(query, user_id, as_of)
        if excluded:
            live = [f for f in live if not self._fact_touches_entities(f, excluded)]
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
