"""Hierarchical abstraction (CLAUDE.md §3): L2 session summaries + L3 user persona.

These are the *scaling primitives*. Instead of dragging every raw session into the read context (which
doesn't scale past a model's window — CLAUDE.md Bet A/E), consolidation distills each session into a
compact L2 summary and the whole user into an L3 persona, computed ONCE off the critical path and stored.
The read path then retrieves a small slice of summaries + the persona, so a lean context can cover many
sessions cheaply.

Everything degrades gracefully offline: with no LLM, the session summary is a trimmed excerpt and the
persona is the deterministic single-valued fact profile — so `Memory()` still works with zero setup.
"""
from __future__ import annotations

from typing import Optional

from ..llm import LLM
from ..types import Episode, Fact

_SESSION_SUMMARY_SYSTEM = (
    "You compress one dated conversation into a dense, factual digest for a memory index that a later "
    "question must be answerable FROM THE DIGEST ALONE. Preserve EVERY specific value VERBATIM — never "
    "generalize or round: exact numbers, quantities, prices, durations, dates/times, proper nouns (people, "
    "places, brands, products, titles), and counts. List each distinct item the user mentions (so a later "
    "'how many' can be counted from the digest). Capture preferences, decisions, events and plans. "
    "Omit only filler and chit-chat. Be terse but COMPLETE on specifics — losing a number or name is the "
    "one failure that matters. No preamble."
)

# Structured 4-layer persona, adopted from Tencent Agent-Memory's L3 "deep scan" (Anchors → Interest
# graph → Interaction protocol → Cognitive core). A structured profile grounds preference/recommendation
# answers far better than a flat fact dump — it's where Hunyuan/Tencent banked their big preference gains.
_PERSONA_SYSTEM = (
    "You maintain a concise, STRUCTURED user profile for a long-term memory system. From the user's known "
    "facts, write these compact labeled sections (omit a section if nothing supports it):\n"
    "  IDENTITY & ANCHORS: name, role/work, location, key relationships, notable possessions/tools.\n"
    "  INTERESTS & PREFERENCES: hobbies, likes/dislikes, habits, favorite brands/genres/styles — note "
    "active vs past where the dates show it.\n"
    "  CONSTRAINTS & GOALS: needs, limits, allergies, current plans, what they're working toward.\n"
    "  STYLE: how they prefer to be helped / standing instructions, if stated.\n"
    "Specifics only (names, brands, places, numbers); only what the facts support — do not invent. Tight."
)


class SessionSummarizer:
    """Episode (raw session) -> compact L2 summary. LLM-backed when available; excerpt fallback offline."""

    def __init__(self, llm: Optional[LLM] = None, max_excerpt: int = 400) -> None:
        self.llm = llm
        self.max_excerpt = max_excerpt
        # The active summary system prompt. Defaults to the built-in; a Memory may swap in a per-user
        # override from its policy (the editable "提示词" in the console).
        self.system = _SESSION_SUMMARY_SYSTEM

    def summarize(self, episode: Episode) -> str:
        if self.llm is None:
            # offline fallback: a trimmed excerpt still gives lexical/embedding signal for retrieval.
            text = " ".join(episode.content.split())
            return text[: self.max_excerpt]
        date = episode.metadata.get("date", "")
        prompt = f"Date: {date}\nConversation:\n{episode.content}\n\nDigest:"
        try:
            out = self.llm.complete(prompt, system=self.system)
            return out.strip() or episode.content[: self.max_excerpt]
        except Exception:  # noqa: BLE001 -- never let summarization break consolidation
            return episode.content[: self.max_excerpt]


class ProfileBuilder:
    """L3, deterministic floor: the user's current single-valued facts as an O(1) profile dict. Always
    available (no LLM), and the offline fallback for the narrative persona."""

    _KEYS = {"works_at", "lives_in", "name", "born_in", "married_to", "occupation", "job_title", "age"}

    def build(self, subject: str, live_facts: list[Fact]) -> dict[str, str]:
        # #7 profile authority: when two live facts share a slot, the authoritative (source="user") one wins,
        # and the most-recent wins within a source tier — so a manually-pinned precise value is never
        # shadowed by a vague auto-extracted one (or by iteration order).
        best: dict[str, Fact] = {}
        for f in live_facts:
            if f.subject.lower() != subject.lower() or not (
                    f.predicate in self._KEYS or f.predicate.startswith("favorite_")):
                continue
            cur = best.get(f.predicate)
            if cur is None or (f.source == "user", f.valid_at) > (cur.source == "user", cur.valid_at):
                best[f.predicate] = f
        return {p: f.object for p, f in best.items()}

    def narrative(self, subject: str, live_facts: list[Fact], llm: Optional[LLM] = None,
                  system: Optional[str] = None) -> str:
        """L3 persona as prose. LLM-synthesized from the live facts when available; otherwise a readable
        rendering of the deterministic profile dict + the salient facts. `system` overrides the persona
        prompt (the editable per-user policy)."""
        user_facts = [f for f in live_facts if f.subject.lower() == subject.lower()]
        if not user_facts:
            user_facts = live_facts
        # #7: authoritative (pinned) facts lead, then by salience — so they survive the [:12]/[:120] truncation.
        ordered = sorted(user_facts, key=lambda x: (x.source != "user", -x.salience))
        if llm is None:
            prof = self.build(subject, live_facts)
            lines = [f"{k.replace('_', ' ')}: {v}" for k, v in prof.items()]
            for f in ordered[:12]:  # add a few top facts for color
                lines.append(f.text)
            return "\n".join(dict.fromkeys(lines))  # dedupe, preserve order
        facts_block = "\n".join(f"- {f.text}" for f in ordered[:120])
        try:
            return self.llm_complete(llm, facts_block, system)
        except Exception:  # noqa: BLE001
            return "\n".join(f.text for f in user_facts[:20])

    @staticmethod
    def llm_complete(llm: LLM, facts_block: str, system: Optional[str] = None) -> str:
        out = llm.complete(f"User facts:\n{facts_block}\n\nUSER PROFILE:", system=system or _PERSONA_SYSTEM)
        return out.strip()


# Public aliases so the Memory policy layer can expose the built-in prompts as editable defaults.
SESSION_SUMMARY_SYSTEM = _SESSION_SUMMARY_SYSTEM
PERSONA_SYSTEM = _PERSONA_SYSTEM
