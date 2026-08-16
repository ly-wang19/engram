"""Split the read context into a cacheable half and a per-query half.

`Memory.lean_context` returns one flat string, and callers drop it into the user turn. Across a
multi-turn session that re-sends and re-processes the whole thing every turn, including the parts that
did not change — the user's profile, the map of what exists in memory, the instructions on how to use it.

Splitting it lets the unchanging half sit in the system prompt, where provider prompt-caching can reuse
it for the rest of the session, while only this query's evidence varies. The retrieved evidence is
identical either way, so accuracy is unchanged by construction: this is a tokens-and-latency change, two
thirds of the triple the charter insists on reporting together, and it should not be expected to move a
benchmark score.

The property that makes it work is that the stable half is **query-independent**. If it varied with the
question it would invalidate the cache every turn and cost more than it saved, so the map below is ranked
by recency rather than relevance and `test_stable_block_is_identical_across_queries` pins that down.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..util import fmt_date

__all__ = ["LayeredContext", "layered_context", "memory_map", "RECALL_GUIDE"]

# How to use a retrieved slice, including when to refuse. Query-independent, so it belongs in the cached
# half rather than being re-sent every turn. It states the abstention rule the read path already applies.
RECALL_GUIDE = (
    "The memories below are a retrieved slice of this user's history, not the complete record. Answer "
    "only from what is shown plus the current question. If the needed fact is not present, say it is not "
    "in memory rather than guessing — a confident wrong answer is worse than an honest 'I don't have "
    "that.'"
)

# Appended only when a map is actually included. Pointing the model at a section that is not there —
# which is what a redacted context would do — invites it to ask for something it cannot be given.
_MAP_HINT = (
    " MEMORY MAP lists sessions that exist but were not opened; if the answer plausibly lives in one, "
    "you may ask for it by its date."
)


@dataclass(frozen=True)
class LayeredContext:
    """The same evidence as `lean_context`, in two halves.

    `stable` is identical across a user's turns until their memory itself changes; `dynamic` is this
    query's evidence.
    """

    stable: str
    dynamic: str

    @property
    def text(self) -> str:
        """Both halves as one string — what `lean_context` would have returned."""
        return "\n\n".join(part for part in (self.stable, self.dynamic) if part)

    def as_messages(self, query: str, system: str = "") -> list[dict[str, str]]:
        """Chat messages placing the cacheable half in the system turn and the rest in the user turn."""
        system_parts = [part for part in (system, self.stable) if part]
        messages = []
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})
        user_parts = [part for part in (self.dynamic, query) if part]
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})
        return messages


def memory_map(mem: Any, user_id: str, limit: int = 20, as_of: Optional[float] = None) -> str:
    """A query-independent index of what is in memory but was not retrieved.

    Deliberately ranked by recency, not by relevance to the question: relevance ranking would reorder the
    map on every turn and defeat the caching this exists for. Each row carries a date the model can ask
    for, which is what makes the unretrieved remainder reachable instead of merely invisible.
    """
    if limit <= 0:
        return ""
    sessions: dict[str, Any] = {}
    for ep in mem.episodes_doc.values():
        if ep.user_id != user_id:
            continue
        if as_of is not None and ep.event_time > as_of:
            continue
        current = sessions.get(ep.session_id)
        if current is None or ep.event_time > current.event_time:
            sessions[ep.session_id] = ep
    if not sessions:
        return ""

    rows = []
    for ep in sorted(sessions.values(), key=lambda e: e.event_time, reverse=True)[:limit]:
        gist = (ep.summary or ep.content or "").strip().replace("\n", " ")
        if len(gist) > 96:
            gist = gist[:93].rstrip() + "..."
        rows.append(f"- {fmt_date(ep.event_time)} [{ep.session_id}] {gist}")
    return "MEMORY MAP (sessions in memory, most recent first):\n" + "\n".join(rows)


def layered_context(
    mem: Any,
    query: str,
    user_id: str = "default",
    as_of: Optional[float] = None,
    # The map is off by default because it was measured, not assumed: it is content the flat context does
    # not carry, and at typical session lengths it costs more than the caching saves — a net loss until
    # roughly 20 turns (results/layered_context_tokens.md). Turn it on for long sessions, or when the
    # progressive-disclosure capability is worth paying for on its own.
    map_limit: int = 0,
    guide: bool = True,
    **lean_kwargs: Any,
) -> LayeredContext:
    """Assemble the read context in two halves.

    `mem` is duck-typed as `engram.memory.Memory` to avoid a circular import. Extra keyword arguments go
    straight to `lean_context`, so the dynamic half is produced by the same retrieval as the flat path —
    there is no second, drifting implementation of the read path here.
    """
    user = mem.resolver.resolve(user_id)
    redact = bool(lean_kwargs.get("redact_sensitive", False))

    # The persona and the map are free-text layers that can fold in sensitive content, so a redacted
    # context omits both for the same reason lean_context drops the persona.
    persona = map_block = ""
    if not redact:
        persona = mem._persona_at(user, as_of)
        map_block = memory_map(mem, user, limit=map_limit, as_of=as_of)

    stable_parts = []
    if guide:
        stable_parts.append(RECALL_GUIDE + (_MAP_HINT if map_block else ""))
    if persona:
        label = "USER PROFILE" if as_of is None else f"USER PROFILE (as of {fmt_date(as_of)})"
        stable_parts.append(f"{label}:\n{persona}")
    if map_block:
        stable_parts.append(map_block)

    # persona=False: it is already in the stable half, and sending it twice would cost exactly the tokens
    # this split exists to save.
    lean_kwargs["persona"] = False
    dynamic = mem.lean_context(query, user_id=user_id, as_of=as_of, **lean_kwargs)

    return LayeredContext(stable="\n\n".join(stable_parts), dynamic=dynamic)
