"""Two named speakers under one user_id must stay two subjects.

LOCOMO hands the harness a two-person conversation as one user_id with the speaker's NAME as the
role. The extractor used to register every declared name as an alias of the user_id and fold every
matching subject onto one canonical name, so the second speaker's facts vanished under the first's.
"""
from __future__ import annotations

from engram.consolidate.llm_extractor import LLMExtractor
from engram.types import Episode


class ScriptedLLM:
    """Returns a JSON array per prompt, keyed by which speaker's text the prompt contains."""

    def __init__(self, table: dict[str, str]):
        self.table = table

    def complete(self, prompt: str, system: str = "") -> str:
        for needle, reply in self.table.items():
            if needle in prompt:
                return reply
        return "[]"


def _ep(speaker, content, uid="conv-1", t=1.0):
    return Episode(content=content, speaker=speaker, user_id=uid, session_id="s1", event_time=t)


def test_named_speakers_keep_their_own_facts():
    llm = ScriptedLLM({
        "call me Mel": '[{"subject":"I","predicate":"nickname","object":"Mel"},'
                       '{"subject":"I","predicate":"adopted","object":"a dog"}]',
        "I run a pottery studio": '[{"subject":"I","predicate":"runs","object":"a pottery studio"}]',
        "Mel is visiting": '[{"subject":"Mel","predicate":"is_visiting","object":"Caroline"}]',
    })
    ex = LLMExtractor(llm)
    facts = []
    for ep in (_ep("Melanie", "Hi! You can call me Mel. I adopted a dog."),
               _ep("Caroline", "I run a pottery studio."),
               _ep("Caroline", "Mel is visiting next week.")):
        facts += ex.facts_from(ep, ex.raw_items(ep))

    by_subject = {f.object: f.subject for f in facts}
    assert by_subject["a dog"] == "Melanie"              # Melanie's "I" is Melanie
    assert by_subject["a pottery studio"] == "Caroline"  # Caroline's "I" is Caroline
    assert by_subject["Caroline"] == "Melanie"           # "Mel" resolves to the speaker who declared it
    # and nothing was registered at the user_id level: the second speaker never became a user alias
    assert "melanie" not in ex.aliases.get("conv-1", set())
    assert "mel" not in ex.aliases.get("conv-1", set())
    assert ex.self_of("conv-1") == "conv-1"


def test_chat_roles_keep_the_single_user_behaviour():
    """LongMemEval / ordinary chat: speaker is 'user' or 'assistant', so the old path is untouched —
    a declared name becomes the user's canonical subject and 'I' normalizes to it."""
    llm = ScriptedLLM({
        "My name is Evan": '[{"subject":"I","predicate":"name","object":"Evan"},'
                           '{"subject":"I","predicate":"likes","object":"oolong tea"}]',
    })
    ex = LLMExtractor(llm)
    ep = _ep("user", "My name is Evan and I like oolong tea.", uid="u1")
    facts = ex.facts_from(ep, ex.raw_items(ep))
    assert [f.subject for f in facts] == ["Evan"]
    assert ex.self_of("u1") == "Evan" and "evan" in ex.aliases["u1"]
