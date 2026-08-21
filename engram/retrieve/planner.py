"""Multi-hop query planner (CLAUDE.md Bet B) -- the field's soft spot, our target.

Decomposes a relational question ("Where does Wei's colleague work?") into an ordered predicate chain
[colleague, works_at], anchors it on an entity (Wei), and walks the bi-temporal graph hop by hop. Only
fires for genuine multi-hop questions (>=2 predicates + a known anchor); single-hop falls through to the
hybrid retriever.

Decomposition has two backends behind one `.plan()` API:
  * with an LLM -> the model picks the anchor + ordered predicate chain, but ONLY from the predicates and
    entities that actually exist in THIS user's stores (so it can't invent edges). This generalizes the
    planner past the hand-listed keyword map below — the win for arbitrary multi-hop phrasings.
  * offline -> the deterministic keyword map (`_PRED_KEYWORDS`) with its relation/location chain
    extensions, so the zero-setup demo/tests plan without a model. This path is unchanged.
Either way the graph walk is identical; a broken chain returns None so search() falls back to hybrid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import re

from ..config import Config
from ..store import GraphStore, VectorStore
from ..types import Fact
from .jsonio import loads_object
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

# The planner LLM is given the user's REAL predicate/entity vocabulary and may only chain those — grounding
# it so it returns walkable edges, not plausible-sounding hallucinations.
_PLAN_SYSTEM = (
    "You decompose a multi-hop question about a user's memory into a graph walk. "
    'Output ONLY JSON: {"anchor": <one entity name from the list, or "">, '
    '"predicates": [<ordered predicate names from the list — the chain of edges to follow from the anchor>]}. '
    "Use ONLY names and predicates from the provided lists; never invent one. If the question needs fewer "
    'than two hops or has no anchor entity in the list, return {"anchor":"","predicates":[]}.'
)
_MAX_VOCAB = 60  # cap the predicate/entity lists fed to the planner LLM (keep the prompt small)


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
        llm=None,
    ) -> None:
        self.graph = graph
        self.fact_store = fact_store
        self.fact_stores = [fact_store] + list(extra_fact_stores or [])
        self.config = config
        self.llm = llm  # when set, the LLM decomposes; otherwise the keyword map does (offline)

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

    def _location_fact(self, entity_id: str, target: str, as_of: Optional[float]) -> Optional[Fact]:
        if not target:
            return None
        target_terms = set(stems(target))
        if not target_terms:
            return None
        for rel in self.graph.neighbors(entity_id, as_of, "out"):
            if rel.predicate != "lives_in":
                continue
            obj = self.graph.entities.get(rel.object_id)
            name = obj.name if obj else ""
            if target_terms <= set(stems(name)):
                fact = self._fact(rel.fact_id)
                if fact is not None and fact.is_live(as_of):
                    return fact
        return None

    @staticmethod
    def _pred_matches(pred: str, rel_pred: str) -> bool:
        """An edge matches a planned predicate on exact name or its family (works_at/works_on/based_in/spouse)."""
        return (
            rel_pred == pred
            or (pred == "works_at" and rel_pred.startswith("work"))
            or (pred == "works_on" and rel_pred in _PROJECT_REL_PREDS)
            or (pred == "based_in" and rel_pred in _LOCATION_ATTR_PREDS)
            or (pred == "spouse" and rel_pred in {"wife", "husband", "partner"})
        )

    def _user_predicates(self, user_id: str) -> list[str]:
        """The distinct predicates that actually occur in this user's live facts — the only edges the LLM
        planner is allowed to chain (grounding it so it can't hallucinate a relation)."""
        preds: list[str] = []
        seen: set[str] = set()
        for store in self.fact_stores:
            for f in store.values():
                if f.user_id != user_id or not f.is_live():
                    continue
                if f.predicate and f.predicate not in seen:
                    seen.add(f.predicate)
                    preds.append(f.predicate)
                if len(preds) >= _MAX_VOCAB:
                    return preds
        return preds

    def _user_entities(self, user_id: str) -> list[str]:
        names: list[str] = []
        for ent in self.graph.entities.values():
            if ent.user_id == user_id and ent.name:
                names.append(ent.name)
            if len(names) >= _MAX_VOCAB:
                break
        return names

    def _llm_plan(self, query: str, user_id: str):
        """Ask the LLM for {anchor, predicates}, constrained to the user's real vocabulary. Returns
        (anchor_entity, predicates): the anchor resolved against the graph; predicates filtered to ones that
        actually exist (invented edges are dropped). Any parse/validation failure returns (None, []) so the
        caller falls back to the keyword decomposer — a model hiccup never breaks retrieval."""
        allowed = self._user_predicates(user_id)
        if len(allowed) < 2:
            return None, []  # not enough relational structure to chain anything
        prompt = f"Question: {query}\nEntities: {self._user_entities(user_id)}\nPredicates: {allowed}\nJSON:"
        try:
            raw = self.llm.complete(prompt, system=_PLAN_SYSTEM)
        except Exception:  # noqa: BLE001 -- never let a model error break the read path
            return None, []
        obj = loads_object(raw)
        if not obj:
            return None, []
        allowed_set = {p.lower() for p in allowed}
        preds = [str(p) for p in (obj.get("predicates") or []) if str(p).lower() in allowed_set]
        anchor_name = str(obj.get("anchor") or "").strip()
        anchor = self.graph.get_entity(user_id, anchor_name) if anchor_name else None
        return anchor, preds

    def _make_plan(self, query: str, user_id: str):
        """LLM decomposition when available and confident, else the deterministic keyword map (with its
        relation/location chain extensions) — the offline behavior is unchanged."""
        if self.llm is not None:
            anchor, preds = self._llm_plan(query, user_id)
            if anchor is not None and len(preds) >= 2:
                return anchor, preds
        return self._anchor_entity(query, user_id), self._ordered_predicates(query)

    def _bridge_paths(
        self, frontier_paths: dict, pred: str, user_id: str, as_of: Optional[float]
    ) -> dict:
        """Fallback hop when no graph EDGE matches `pred`: look the predicate up directly in the fact
        stores keyed on the frontier entities as subjects. Recovers facts that exist but never became a
        clean graph edge (the seam between the planner and plain retrieval). LLM path only — the offline
        keyword walk stays byte-for-byte as before."""
        names = {
            self.graph.entities[eid].name.lower(): eid
            for eid in frontier_paths
            if eid in self.graph.entities
        }
        out: dict[str, list[Fact]] = {}
        for store in self.fact_stores:
            for f in store.values():
                if f.user_id != user_id or not f.is_live(as_of):
                    continue
                eid = names.get(f.subject.lower())
                if eid is None or not self._pred_matches(pred, f.predicate):
                    continue
                obj_ent = self.graph.get_entity(user_id, f.object)
                if obj_ent is not None:
                    out[obj_ent.id] = frontier_paths[eid] + [f]
        return out

    def plan(self, query: str, user_id: str, as_of: Optional[float] = None) -> Optional[PlanResult]:
        anchor, preds = self._make_plan(query, user_id)
        if anchor is None or len(preds) < 2:
            return None  # not a confident multi-hop question; let hybrid handle it

        frontier_paths: dict[str, list[Fact]] = {anchor.id: []}
        location = self._location_constraint(query)
        for pred in preds:
            if pred in _ANSWER_ATTR_PREDS and location and len(preds) > 1:
                constrained: dict[str, list[Fact]] = {}
                for eid, facts in frontier_paths.items():
                    loc_fact = self._location_fact(eid, location, as_of)
                    if loc_fact is not None:
                        constrained[eid] = facts + [loc_fact]
                if not constrained:
                    return None
                frontier_paths = constrained

            next_paths: dict[str, list[Fact]] = {}
            for eid, facts in frontier_paths.items():
                for rel in self.graph.neighbors(eid, as_of, "out"):
                    if self._pred_matches(pred, rel.predicate):
                        fact = self._fact(rel.fact_id)
                        if fact is None or not fact.is_live(as_of):
                            continue
                        next_paths[rel.object_id] = facts + ([fact] if fact is not None else [])
            if not next_paths and self.llm is not None:
                # No graph edge for this hop — try the fact-store bridge before giving up (LLM path only):
                # a fact that exists but never became a clean edge can still complete the chain.
                next_paths = self._bridge_paths(frontier_paths, pred, user_id, as_of)
            if not next_paths:
                return None  # chain broke -> no confident multi-hop answer
            frontier_paths = next_paths

        answer_names = [self.graph.entities[eid].name for eid in frontier_paths if eid in self.graph.entities]
        if not answer_names:
            return None
        answer_id = next(eid for eid in frontier_paths if eid in self.graph.entities)
        return PlanResult(answer=self.graph.entities[answer_id].name, facts=frontier_paths[answer_id], chain=preds)
