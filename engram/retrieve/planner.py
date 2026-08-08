"""Multi-hop query planner (CLAUDE.md Bet B) -- the field's soft spot, our target.

Decomposes a relational question ("Where does Wei's colleague work?") into an ordered predicate chain
[colleague, works_at], anchors it on an entity (Wei), and walks the bi-temporal graph hop by hop. Only
fires for genuine multi-hop questions (>=2 predicates + a known anchor); single-hop falls through to the
hybrid retriever. Offline it is keyword-driven; an LLM planner slots in behind the same `.plan()` API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import re

from ..config import Config
from ..store import GraphStore, VectorStore
from ..types import Fact, is_visible
from .lexical import stem, stems

# query keyword -> graph predicate
_PRED_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("colleague", "coworker", "co-worker"), "colleague"),
    (("sister",), "sister"),
    (("brother",), "brother"),
    (("mother", "mom"), "mother"),
    (("father", "dad"), "father"),
    (("parent",), "parent"),
    (("child",), "child"),
    (("spouse", "wife", "husband", "partner"), "spouse"),
    (("project", "projects", "initiative", "initiatives", "works_on", "worked_on"), "works_on"),
    (("work", "works", "working", "employer", "company", "job", "employed"), "works_at"),
    (("profession", "occupation", "role", "title"), "occupation"),
    (("live", "lives", "living", "city", "reside", "resides"), "lives_in"),
    (("based", "located", "location", "headquarters", "headquarter", "hq"), "based_in"),
]
_RELATION_PREDS = {"colleague", "sister", "brother", "mother", "father", "parent", "child", "spouse", "works_on"}
_ANSWER_ATTR_PREDS = {"works_at", "occupation", "lives_in", "based_in"}
_LOCATION_ATTR_PREDS = {"based_in", "located_in", "headquartered_in"}
_PROJECT_REL_PREDS = {"works_on", "project", "worked_on", "built", "building"}


@dataclass
class PlanResult:
    answer: str
    facts: list[Fact] = field(default_factory=list)
    chain: list[str] = field(default_factory=list)


class MultiHopPlanner:
    def __init__(
        self,
        graph: GraphStore,
        fact_store: VectorStore,
        config: Config,
        extra_fact_stores: Optional[list[VectorStore]] = None,
    ) -> None:
        self.graph = graph
        self.fact_store = fact_store
        self.fact_stores = [fact_store] + list(extra_fact_stores or [])
        self.config = config

    def _fact(self, fact_id: str) -> Optional[Fact]:
        for store in self.fact_stores:
            fact = store.get(fact_id)
            if fact is not None:
                return fact
        return None

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
        if not self.config.planner_project_chains:
            ordered = [p for p in ordered if p not in _PROJECT_REL_PREDS]
        if not self.config.planner_location_chains:
            ordered = [p for p in ordered if p not in _LOCATION_ATTR_PREDS]
        rels = [p for p in ordered if p in _RELATION_PREDS]
        attrs = [p for p in ordered if p in _ANSWER_ATTR_PREDS]
        if rels and attrs:
            # Possessive relation questions ("user's sister's profession") mention the desired attribute
            # before the relation in English, but the graph walk must start from the user -> relation.
            ordered = rels + [p for p in attrs if p not in rels]
        return ordered

    def _anchor_entity(self, query: str, user_id: str):
        if re.search(r"\b(my|user's|the user's|their|his|her)\b", query.lower()):
            return self.graph.get_entity(user_id, user_id) or self.graph.get_entity(user_id, "user")
        q = set(stems(query))
        for ent in self.graph.entities.values():
            if ent.user_id != user_id:
                continue
            name_toks = stems(ent.name)
            if name_toks and all(t in q for t in name_toks):
                return ent
        return None

    def _location_constraint(self, query: str) -> str:
        q = query.lower()
        m = re.search(r"\b(?:moved|relocated|lives?|living)\s+(?:to|in)\s+([a-z][a-z' -]{1,40})", q)
        if not m:
            return ""
        return re.split(r"\b(?:for|with|after|before|who|that|and|or)\b|[?.!,;:]", m.group(1), 1)[0].strip()

    def _location_fact(
        self,
        entity_id: str,
        target: str,
        as_of: Optional[float],
        known_at: Optional[float] = None,
    ) -> Optional[Fact]:
        if not target:
            return None
        target_terms = set(stems(target))
        if not target_terms:
            return None
        for rel in self.graph.neighbors(entity_id, as_of, "out", known_at):
            if rel.predicate != "lives_in":
                continue
            obj = self.graph.entities.get(rel.object_id)
            name = obj.name if obj else ""
            if target_terms <= set(stems(name)):
                fact = self._fact(rel.fact_id)
                if fact is not None and is_visible(fact, as_of=as_of, known_at=known_at):
                    return fact
        return None

    def plan(
        self,
        query: str,
        user_id: str,
        as_of: Optional[float] = None,
        known_at: Optional[float] = None,
    ) -> Optional[PlanResult]:
        preds = self._ordered_predicates(query)
        if len(preds) < 2:
            return None  # not multi-hop; let hybrid handle it
        anchor = self._anchor_entity(query, user_id)
        if anchor is None:
            return None

        frontier_paths: dict[str, list[Fact]] = {anchor.id: []}
        location = self._location_constraint(query)
        for pred in preds:
            if pred in _ANSWER_ATTR_PREDS and location and len(preds) > 1:
                constrained: dict[str, list[Fact]] = {}
                for eid, facts in frontier_paths.items():
                    loc_fact = self._location_fact(eid, location, as_of, known_at)
                    if loc_fact is not None:
                        constrained[eid] = facts + [loc_fact]
                if not constrained:
                    return None
                frontier_paths = constrained

            next_paths: dict[str, list[Fact]] = {}
            for eid, facts in frontier_paths.items():
                for rel in self.graph.neighbors(eid, as_of, "out", known_at):
                    rel_pred = rel.predicate
                    if (
                        rel_pred == pred
                        or (pred == "works_at" and rel_pred.startswith("work"))
                        or (pred == "works_on" and rel_pred in _PROJECT_REL_PREDS)
                        or (pred == "based_in" and rel_pred in _LOCATION_ATTR_PREDS)
                        or (pred == "spouse" and rel_pred in {"wife", "husband", "partner"})
                    ):
                        fact = self._fact(rel.fact_id)
                        if fact is None or not is_visible(
                            fact,
                            as_of=as_of,
                            known_at=known_at,
                        ):
                            continue
                        next_paths[rel.object_id] = facts + ([fact] if fact is not None else [])
            if not next_paths:
                return None  # chain broke -> no confident multi-hop answer
            frontier_paths = next_paths

        answer_names = [self.graph.entities[eid].name for eid in frontier_paths if eid in self.graph.entities]
        if not answer_names:
            return None
        answer_id = next(eid for eid in frontier_paths if eid in self.graph.entities)
        return PlanResult(answer=self.graph.entities[answer_id].name, facts=frontier_paths[answer_id], chain=preds)
