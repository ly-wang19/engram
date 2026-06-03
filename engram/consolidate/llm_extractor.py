"""LLM-backed fact extractor. Same `.extract(episode) -> list[Fact]` API as the offline RuleExtractor,
so the entire pipeline (graph build, conflict resolution, retrieval) is unchanged -- only the extraction
quality goes up. Used automatically when a Memory is given an `llm`."""
from __future__ import annotations

import json
import re

from ..llm import LLM
from ..types import Episode, Fact

EXTRACT_SYSTEM = (
    "You are a precise information-extraction engine for a long-term memory system. "
    "From a multi-turn conversation, extract the atomic, durable facts it states about the user and the "
    "people/things they mention (identities, attributes, preferences, relationships, possessions, "
    "goals/plans, and events with their times). Output ONLY a JSON array of objects, each with keys "
    "\"subject\", \"predicate\", \"object\". Use short snake_case predicates (e.g. works_at, lives_in, "
    "favorite_color, owns, married_to, born_in, visited). Capture PREFERENCES explicitly and completely "
    "with predicates like likes, dislikes, prefers, avoids, allergic_to, favorite_<thing> "
    "(e.g. likes/'spicy food', dislikes/'crowds', prefers/'window seat', allergic_to/'peanuts'). "
    "For each preference or dislike stated, output a SEPARATE fact. "
    "Resolve first-person ('I','my','me') to the user's name when it is known in the conversation, "
    "otherwise to \"user\". Capture a stated name as "
    "{\"subject\":\"user\",\"predicate\":\"name\",\"object\":\"<Name>\"}. Do NOT infer or invent facts "
    "that are not stated. If there are no durable facts, output []."
)

EXTRACT_TEMPLATE = "Conversation:\n{content}\n\nJSON facts:"

_NAME_PREDICATES = {"name", "name_is", "is_named", "called"}


def _norm_predicate(p: str) -> str:
    p = p.strip().lower().replace("-", " ").replace("/", " ")
    p = re.sub(r"\s+", "_", p).strip("_")
    return p


def parse_json_facts(raw: str) -> list[dict]:
    """Tolerant JSON-array parsing: handle code fences and surrounding prose."""
    if not raw:
        return []
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    candidate = text[start : end + 1] if (start != -1 and end > start) else text
    for attempt in (candidate, text):
        try:
            data = json.loads(attempt)
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except json.JSONDecodeError:
            continue
    return []


class LLMExtractor:
    def __init__(self, llm: LLM, system: str = EXTRACT_SYSTEM, template: str = EXTRACT_TEMPLATE) -> None:
        self.llm = llm
        self.system = system
        self.template = template
        self.self_name: dict[str, str] = {}

    def self_of(self, user_id: str) -> str:
        return self.self_name.get(user_id, user_id)

    def extract(self, ep: Episode) -> list[Fact]:
        raw = self.llm.complete(self.template.format(speaker=ep.speaker, content=ep.content), system=self.system)
        facts: list[Fact] = []
        for item in parse_json_facts(raw):
            subj = str(item.get("subject", "")).strip()
            pred = _norm_predicate(str(item.get("predicate", "")))
            obj = str(item.get("object", "")).strip()
            if not subj or not pred or not obj:
                continue
            if pred in _NAME_PREDICATES:
                # register identity; rewrite the placeholder subject so later facts attribute correctly
                self.self_name[ep.user_id] = obj
                continue
            if subj.lower() in {"user", "i", "me", "myself"}:
                subj = self.self_of(ep.user_id) if self.self_of(ep.user_id) != ep.user_id else subj
            facts.append(
                Fact(
                    subject=subj,
                    predicate=pred,
                    object=obj,
                    user_id=ep.user_id,
                    valid_at=ep.event_time,
                    created_at=ep.ingested_at,
                    provenance=[ep.id],
                )
            )
        return facts
