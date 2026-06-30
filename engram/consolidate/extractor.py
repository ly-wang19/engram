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
_NON_OCCUPATIONS = {
    "awesome", "cool", "exciting", "fine", "fun", "good", "great", "nice", "noted", "ok", "okay",
    "wonderful",
}
_NON_NAMES = {
    "a", "an", "the", "not", "working", "going", "moving", "relocating", "into", "fond", "fan",
    "vegetarian", "particularly", "especially", "interested", "looking", "planning", "thinking",
    "trying", "getting", "glad", "happy", "sure", "always", "also", "still", "really",
}

_CLAUSE_SPLIT = re.compile(r"\n+|[.!?。！？]+(?:\s+|$)|,|;| and | but |—|--|\bthen\b", re.I)
_FAMILY = "sister|brother|mother|father|parent|child|spouse|wife|husband|partner"
_I_AM = r"i\s*(?:am|'m|m)?"


def _clean_obj(s: str) -> str:
    s = s.strip().strip(".!?;:, ").strip()
    toks = s.split()
    while toks and toks[-1].lower() in _FILLER:
        toks.pop()
    if toks and toks[0].lower() == "the":
        toks = toks[1:]
    return " ".join(toks)


def _clean_role(s: str) -> str:
    """Keep the role/profession head, not the workplace or explanatory tail."""
    return _clean_obj(re.split(r"\b(?:at|for|with|in)\b", s, 1, flags=re.I)[0])


def _clean_location(s: str) -> str:
    """Keep the destination head from movement clauses."""
    return _clean_obj(re.split(
        r"\b(?:next|this|last)\s+(?:week|month|year)|\b(?:for|with|after|before)\b",
        s,
        1,
        flags=re.I,
    )[0])


def _clean_procedure_steps(s: str) -> str:
    return " ".join(_clean_obj(s).split())


def _clean_preference_obj(s: str) -> str:
    text = _clean_obj(s)
    text = re.split(r"\b(?:over|rather than|instead of|because|when|while)\b", text, 1, flags=re.I)[0]
    text = re.sub(r"^(?:to|that)\s+", "", text.strip(), flags=re.I)
    return _clean_obj(text)


def _procedure_subject(action: str) -> str:
    """Pick a stable slot subject from a how-to action: 'rotate the PAT' -> 'PAT'."""
    text = _clean_obj(action)
    text = re.sub(
        r"^(?:how\s+to|to)\s+",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"^(?:rotate|reset|update|deploy|install|renew|refresh|regenerate|open|create|start|stop|"
        r"restart|configure|setup|set\s+up|run|use|change|replace)\s+",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^(?:the|a|an)\s+", "", text, flags=re.I).strip()
    return text or "procedure"


_PROCEDURE_ACTION = (
    r"rotate|reset|update|deploy|install|renew|refresh|regenerate|create|start|stop|restart|"
    r"configure|setup|set\s+up|change|replace|revoke|enable|disable"
)


def _slug(s: str) -> str:
    return re.sub(r"\s+", "_", s.strip().lower())


class RuleExtractor:
    def __init__(self, explicit_preference_extraction: bool = True) -> None:
        # learned per-user self name, so "I work at X" can be attributed to "Wei", not a pronoun.
        self.self_name: dict[str, str] = {}
        self.explicit_preference_extraction = explicit_preference_extraction

    def extract(self, ep: Episode) -> list[Fact]:
        facts: list[Fact] = self._episode_procedures(ep)
        previous_preference = False
        for raw in _CLAUSE_SPLIT.split(ep.content):
            clause = raw.strip()
            if clause:
                if (
                    self.explicit_preference_extraction
                    and previous_preference
                    and clause[:1].islower()
                    and re.match(r"(?:like|love|enjoy|prefer|dislike|hate|avoid)s?\s+.+", clause, re.I)
                ):
                    clause = f"I {clause}"
                facts.extend(self._clause(clause, ep))
                previous_preference = self._is_first_person_preference_clause(clause)
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

    def _episode_procedures(self, ep: Episode) -> list[Fact]:
        text = " ".join(ep.content.split())
        out: list[Fact] = []

        # "PAT runbook source: rotate the PAT by opening settings, regenerating token, then updating CI".
        # The anchor is deliberate: imported conversations contain "assistant:"/"user:" role labels, so a
        # loose colon rule would promote ordinary advice into durable procedures.
        m = re.search(
            r"^\s*(?P<subject>[A-Za-z0-9][A-Za-z0-9 _./-]{0,48}?)\s+"
            r"(?:runbook|procedure|workflow|checklist)(?:\s+source)?\s*:\s*(?P<steps>.+)",
            text,
            re.I,
        )
        if m:
            subject = re.sub(r"^(?:the|a|an)\s+", "", _clean_obj(m.group("subject")), flags=re.I)
            steps = _clean_procedure_steps(m.group("steps"))
            if subject and steps:
                out.append(self._mk(subject, "procedure", steps, ep))
                return out

        # "To rotate the PAT: open settings, regenerate token, then update CI"
        m = re.search(
            rf"^\s*(?:to|how to)\s+(?P<action>(?:{_PROCEDURE_ACTION})\b[^:]{{0,90}})\s*:\s*(?P<steps>.+)",
            text,
            re.I,
        )
        if m:
            subject = _procedure_subject(m.group("action"))
            steps = _clean_procedure_steps(m.group("steps"))
            if subject and steps:
                out.append(self._mk(subject, "procedure", steps, ep))
        return out

    def _clause(self, clause: str, ep: Episode) -> list[Fact]:
        out: list[Fact] = []

        # 0. Agent/project memory conventions used by Codex / Claude Code adapters.
        # These turn deliberate memory notes into structured facts even in zero-setup offline mode.
        m = re.match(r"\s*(?:project|repo|repository)\s+(rule|preference|decision|note)\s*:\s*(.+)", clause, re.I)
        if m:
            out.append(self._mk("project", m.group(1).lower(), _clean_obj(m.group(2)), ep))
            return out

        m = re.match(r"\s*(codex|claude code|cursor|agent)\s+should\s+(.+)", clause, re.I)
        if m:
            out.append(self._mk(m.group(1), "agent_instruction", _clean_obj("should " + m.group(2)), ep))
            return out

        # 1. Movement + purpose. This must run before name detection, otherwise "I'm moving..." looks like
        # the user's name is "moving" to the tiny offline extractor.
        m = re.search(r"\b(?:i'?m|i am|we'?re|we are)?\s*(?:moving|relocating) to (.+)", clause, re.I)
        if m:
            dest = _clean_location(m.group(1))
            if dest:
                out.append(self._mk(self._subject(clause, ep), "lives_in", dest, ep))
            job = re.search(r"\bfor (?:a |an |the )?job at (.+)", clause, re.I)
            if job:
                out.append(self._mk(self._subject(clause, ep), "works_at", _clean_obj(job.group(1)), ep))
            return out

        # 2. Dietary restrictions and allergies.
        m = re.search(r"\ballergic to (.+)", clause, re.I)
        if m:
            out.append(self._mk(self._subject(clause, ep), "allergic_to", _clean_obj(m.group(1)), ep))
            return out
        if re.search(r"\b(?:i'?m|i am|we'?re|we are)?\s*vegetarian\b", clause, re.I):
            out.append(self._mk(self._subject(clause, ep), "diet", "vegetarian", ep))
            return out

        # 3. name -> just register identity, emit no SPO fact
        m = re.search(r"\bmy name is (\w[\w'-]*)", clause, re.I)
        if m and m.group(1).lower() not in _NON_NAMES:
            self.self_name[ep.user_id] = m.group(1)
            return out
        m = re.search(r"\bi am (\w[\w'-]*)\b", clause, re.I) or re.search(r"\bi'?m (\w[\w'-]*)\b", clause, re.I)
        if (
            m
            and m.group(1).lower() not in _NON_NAMES
            and m.group(1)[:1].isupper()
            and len(clause.split()) <= 3
        ):
            self.self_name[ep.user_id] = m.group(1)
            return out

        # 4. "my colleague Lin works at Moonshot AI" -> colleague edge + Lin's employer
        m = re.search(r"\bmy (?:colleague|coworker|co-worker|friend) (\w+) works? (?:at|for) (.+)", clause, re.I)
        if m:
            out.append(self._mk(self.self_of(ep.user_id), "colleague", m.group(1), ep))
            out.append(self._mk(m.group(1), "works_at", _clean_obj(m.group(2)), ep))
            return out

        # 5. plain colleague mention
        m = re.search(r"\bmy (?:colleague|coworker|co-worker|friend) (?:is )?(\w+)", clause, re.I)
        if m:
            out.append(self._mk(self.self_of(ep.user_id), "colleague", m.group(1), ep))
            return out

        # 6. "my sister Maya is a pediatrician" -> family edge + Maya's occupation
        m = re.search(rf"\bmy ({_FAMILY}) (\w+) is (?:an? |the )?(.+)", clause, re.I)
        if m:
            rel, name, role = m.group(1).lower(), m.group(2), _clean_role(m.group(3))
            out.append(self._mk(self.self_of(ep.user_id), rel, name, ep))
            if role:
                out.append(self._mk(name, "occupation", role, ep))
            return out

        # 7. plain family mention
        m = re.search(rf"\bmy ({_FAMILY}) (?:is )?(\w+)", clause, re.I)
        if m:
            out.append(self._mk(self.self_of(ep.user_id), m.group(1).lower(), m.group(2), ep))
            return out

        # 8. "Maya moved to Seattle" / "Maya relocated to Seattle" -> current location
        m = re.search(r"\b(?:moved|relocated) to (.+)", clause, re.I)
        if m:
            out.append(self._mk(self._subject(clause, ep), "lives_in", _clean_location(m.group(1)), ep))
            return out

        # 9. "my favorite X is Y" (favou?rite matches both "favorite" and "favourite")
        m = re.search(r"\bfavou?rite (.+?) (?:is|are) (.+)", clause, re.I)
        if m:
            out.append(self._mk(self._subject(clause, ep), "favorite_" + _slug(m.group(1)), _clean_obj(m.group(2)), ep))
            return out

        # 10. "I am a fan of jazz" / "I'm into synthwave" -> preference, not occupation.
        m = (
            re.search(rf"\b{_I_AM}\s+(?:an? )?fan of (.+)", clause, re.I)
            or re.search(rf"\b{_I_AM}\s+(?:really )?into (.+)", clause, re.I)
            or re.search(rf"\b{_I_AM}\s+fond of (.+)", clause, re.I)
        )
        if m:
            out.append(self._mk(self._subject(clause, ep), "likes", _clean_obj(m.group(1)), ep))
            return out

        # 11. Explicit preference verbs. This fills the zero-dep gap for common profile statements
        # ("I prefer aisle seats", "I avoid red-eyes") without invoking an LLM on the write path.
        if self.explicit_preference_extraction:
            pref = self._explicit_preference(clause, ep)
            if pref is not None:
                out.append(pref)
                return out

        # 12. "Maya is a pediatrician" / "I work as a designer" -> occupation
        m = (
            re.search(r"\bworks? as (?:an? |the )?(.+)", clause, re.I)
            or re.search(r"\bi am (?:an? |the )(.+)", clause, re.I)
            or re.search(r"\bi'?m (?:an? |the )(.+)", clause, re.I)
            or re.search(r"\bis (?:an? |the )?(.+)", clause, re.I)
        )
        if m:
            role = _clean_role(m.group(1))
            if (
                role
                and role.lower() not in {"my", "your", "his", "her", "their", *_NON_OCCUPATIONS}
                and "fan of" not in role.lower()
            ):
                out.append(self._mk(self._subject(clause, ep), "occupation", role, ep))
                return out

        # 13. "<subj> works at Y"
        m = re.search(r"\bworks? (?:at|for) (.+)", clause, re.I)
        if m:
            out.append(self._mk(self._subject(clause, ep), "works_at", _clean_obj(m.group(1)), ep))
            return out

        # 14. "<subj> lives in Y"
        m = re.search(r"\blives? in (.+)", clause, re.I)
        if m:
            out.append(self._mk(self._subject(clause, ep), "lives_in", _clean_obj(m.group(1)), ep))
            return out

        return out

    def _explicit_preference(self, clause: str, ep: Episode) -> Fact | None:
        # Negated positive verbs are negative preference facts.
        m = re.search(
            r"\b(?:i|we)\s+(?:do\s+not|don't|dont)\s+(?:like|love|enjoy|prefer)\s+(.+)",
            clause,
            re.I,
        ) or re.search(rf"\b{_I_AM}\s+not\s+into\s+(.+)", clause, re.I)
        if m:
            obj = _clean_preference_obj(m.group(1))
            return self._mk(ep.user_id, "dislikes", obj, ep) if obj else None

        m = re.search(
            r"\b(?:i|we)\s+(?:try\s+to\s+|really\s+|strongly\s+)?"
            r"(like|love|enjoy|prefer|dislike|hate|avoid)s?\s+(.+)",
            clause,
            re.I,
        )
        if not m:
            return None

        verb = m.group(1).lower()
        obj = _clean_preference_obj(m.group(2))
        if not obj:
            return None
        pred = {
            "like": "likes",
            "love": "loves",
            "enjoy": "enjoys",
            "prefer": "prefers",
            "dislike": "dislikes",
            "hate": "hates",
            "avoid": "avoids",
        }[verb]
        return self._mk(ep.user_id, pred, obj, ep)

    def _is_first_person_preference_clause(self, clause: str) -> bool:
        if re.match(r"\s*(?:assistant|system)\s*:", clause, re.I):
            return False
        return bool(
            re.search(
                r"\b(?:i|we)\s+(?:do\s+not|don't|dont)\s+(?:like|love|enjoy|prefer)\s+",
                clause,
                re.I,
            )
            or re.search(
                r"\b(?:i|we)\s+(?:try\s+to\s+|really\s+|strongly\s+)?"
                r"(?:like|love|enjoy|prefer|dislike|hate|avoid)s?\s+",
                clause,
                re.I,
            )
            or re.search(rf"\b{_I_AM}\s+not\s+into\s+", clause, re.I)
        )
