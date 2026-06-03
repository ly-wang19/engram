"""Offline, deterministic (subject, predicate, object) extractor.

This is the zero-dependency FALLBACK. It handles the common first-/third-person patterns the demo and
tests use; it is intentionally NOT general. Production swaps in an LLM extractor behind the same
`.extract(episode) -> list[Fact]` API (CLAUDE.md §3, §8). Keeping the API identical is what lets the
offline demo and a GPT/Kimi/DeepSeek-backed run share the entire rest of the pipeline.
"""
from __future__ import annotations

import re

from ..types import Episode, Fact

# trailing words we strip off a captured object ("Moonshot AI too" -> "Moonshot AI")
_FILLER = {"too", "now", "also", "currently", "anymore", "though", "well", "as",
           "right", "these", "days", "nowadays", "actually"}
_PRONOUNS = {"i", "my", "me", "myself", "we", "actually", "then", "and", "but", "so", "also", "now"}

_CLAUSE_SPLIT = re.compile(r",|;| and | but |—|--|\bthen\b", re.I)


def _clean_obj(s: str) -> str:
    s = s.strip().strip(".!?;:, ").strip()
    toks = s.split()
    while toks and toks[-1].lower() in _FILLER:
        toks.pop()
    if toks and toks[0].lower() == "the":
        toks = toks[1:]
    return " ".join(toks)


def _slug(s: str) -> str:
    return re.sub(r"\s+", "_", s.strip().lower())


class RuleExtractor:
    def __init__(self) -> None:
        # learned per-user self name, so "I work at X" can be attributed to "Wei", not a pronoun.
        self.self_name: dict[str, str] = {}

    def extract(self, ep: Episode) -> list[Fact]:
        facts: list[Fact] = []
        for raw in _CLAUSE_SPLIT.split(ep.content):
            clause = raw.strip()
            if clause:
                facts.extend(self._clause(clause, ep))
        return facts

    def self_of(self, user_id: str) -> str:
        return self.self_name.get(user_id, user_id)

    def _first_person(self, clause: str) -> bool:
        return re.search(r"\b(i|i'm|im|i've|my|me|myself)\b", clause, re.I) is not None

    def _subject(self, clause: str, ep: Episode) -> str:
        if self._first_person(clause):
            return self.self_of(ep.user_id)
        m = re.match(r"([A-Z][\w]*)", clause.strip())
        if m and m.group(1).lower() not in _PRONOUNS:
            return m.group(1)
        return self.self_of(ep.user_id)

    def _mk(self, subj: str, pred: str, obj: str, ep: Episode) -> Fact:
        return Fact(
            subject=subj,
            predicate=pred,
            object=obj,
            user_id=ep.user_id,
            valid_at=ep.event_time,
            created_at=ep.ingested_at,
            provenance=[ep.id],
        )

    def _clause(self, clause: str, ep: Episode) -> list[Fact]:
        out: list[Fact] = []

        # 1. name -> just register identity, emit no SPO fact
        m = (
            re.search(r"\bmy name is (\w[\w'-]*)", clause, re.I)
            or re.search(r"\bi am (\w[\w'-]*)\b", clause, re.I)
            or re.search(r"\bi'?m (\w[\w'-]*)\b", clause, re.I)
        )
        if m and m.group(1).lower() not in {"a", "an", "the", "not", "working", "going"}:
            self.self_name[ep.user_id] = m.group(1)
            return out

        # 2. "my colleague Lin works at Moonshot AI" -> colleague edge + Lin's employer
        m = re.search(r"\bmy (?:colleague|coworker|co-worker|friend) (\w+) works? (?:at|for) (.+)", clause, re.I)
        if m:
            out.append(self._mk(self.self_of(ep.user_id), "colleague", m.group(1), ep))
            out.append(self._mk(m.group(1), "works_at", _clean_obj(m.group(2)), ep))
            return out

        # 3. plain colleague mention
        m = re.search(r"\bmy (?:colleague|coworker|co-worker|friend) (?:is )?(\w+)", clause, re.I)
        if m:
            out.append(self._mk(self.self_of(ep.user_id), "colleague", m.group(1), ep))
            return out

        # 4. "my favorite X is Y" (favou?rite matches both "favorite" and "favourite")
        m = re.search(r"\bfavou?rite (.+?) (?:is|are) (.+)", clause, re.I)
        if m:
            out.append(self._mk(self._subject(clause, ep), "favorite_" + _slug(m.group(1)), _clean_obj(m.group(2)), ep))
            return out

        # 5. "<subj> works at Y"
        m = re.search(r"\bworks? (?:at|for) (.+)", clause, re.I)
        if m:
            out.append(self._mk(self._subject(clause, ep), "works_at", _clean_obj(m.group(1)), ep))
            return out

        # 6. "<subj> lives in Y"
        m = re.search(r"\blives? in (.+)", clause, re.I)
        if m:
            out.append(self._mk(self._subject(clause, ep), "lives_in", _clean_obj(m.group(1)), ep))
            return out

        return out
