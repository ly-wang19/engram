"""Multi-hop query planner (CLAUDE.md Bet B) -- the field's soft spot, our target.

Decomposes a relational question ("Where does Wei's colleague work?") into an ordered predicate chain
[colleague, works_at], anchors it on an entity (Wei), and walks the bi-temporal graph hop by hop. Only
fires for genuine multi-hop questions (>=2 predicates + a known anchor); single-hop falls through to the
hybrid retriever. Offline it is keyword-driven; an LLM planner slots in behind the same `.plan()` API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..config import Config
from ..store import GraphStore, VectorStore
from ..types import Fact
from .lexical import stem, stems

# query keyword -> graph predicate
_PRED_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("colleague", "coworker", "co-worker"), "colleague"),
    (("work", "works", "working", "employer", "company", "job", "employed"), "works_at"),
    (("live", "lives", "living", "city", "reside", "resides"), "lives_in"),
]


@dataclass
class PlanResult:
    answer: str
    facts: list[Fact] = field(default_factory=list)
    chain: list[str] = field(default_factory=list)


class MultiHopPlanner:
    def __init__(self, graph: GraphStore, fact_store: VectorStore, config: Config) -> None:
        self.graph = graph
        self.fact_store = fact_store
        self.config = config

    def _ordered_predicates(self, query: str) -> list[str]:
        toks = stems(query)
        tokset = set(toks)
        hits: list[tuple[int, str]] = []
        for keywords, pred in _PRED_KEYWORDS:
            positions = [toks.index(stem(kw)) for kw in keywords if stem(kw) in tokset]
            if positions:
                hits.append((min(positions), pred))
        hits.sort()
        # dedupe preserving order
        seen: set[str] = set()
        ordered: list[str] = []
        for _, pred in hits:
            if pred not in seen:
                ordered.append(pred)
                seen.add(pred)
        return ordered

    def _anchor_entity(self, query: str, user_id: str):
        q = set(stems(query))
        for ent in self.graph.entities.values():
            if ent.user_id != user_id:
                continue
            name_toks = stems(ent.name)
            if name_toks and all(t in q for t in name_toks):
                return ent
        return None

    def plan(self, query: str, user_id: str, as_of: Optional[float] = None) -> Optional[PlanResult]:
        preds = self._ordered_predicates(query)
        if len(preds) < 2:
            return None  # not multi-hop; let hybrid handle it
        anchor = self._anchor_entity(query, user_id)
        if anchor is None:
            return None

        frontier = [anchor.id]
        path_facts: list[Fact] = []
        for pred in preds:
            nxt: list[str] = []
            for eid in frontier:
                for rel in self.graph.neighbors(eid, as_of, "out"):
                    if rel.predicate == pred or (pred == "works_at" and rel.predicate.startswith("work")):
                        nxt.append(rel.object_id)
                        fact = self.fact_store.get(rel.fact_id)
                        if fact is not None:
                            path_facts.append(fact)
            if not nxt:
                return None  # chain broke -> no confident multi-hop answer
            frontier = nxt

        answer_names = [self.graph.entities[eid].name for eid in frontier if eid in self.graph.entities]
        if not answer_names:
            return None
        return PlanResult(answer=answer_names[0], facts=path_facts, chain=preds)
