"""Hybrid retrieval (CLAUDE.md §3.3): fuse dense-semantic + BM25-lexical + graph-proximity + recency +
salience over the live fact set, combined with weighted Reciprocal Rank Fusion."""
from __future__ import annotations

from typing import Optional

from ..config import Config
from ..embed import Embedder
from ..store import GraphStore, VectorStore
from ..types import Fact
from ..util import cosine, now, recency, tokenize
from .fusion import order_by_score, weighted_rrf
from .lexical import bm25_scores, stem, stems


class HybridRetriever:
    def __init__(self, fact_store: VectorStore, graph: GraphStore, embedder: Embedder, config: Config) -> None:
        self.fact_store = fact_store
        self.graph = graph
        self.embedder = embedder
        self.config = config

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
        lex = bm25_scores(query, [(f.id, f.text) for f in live])
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
