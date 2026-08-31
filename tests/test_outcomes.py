"""Session outcomes — the memory unit that matches how the owner actually works.

The per-episode extractor looks for biographical triples, which is right for LongMemEval and wrong for a
technical working session: on a real 115-turn transcript it produced
`user requires_no_commit_push_publish = true` (a one-off task constraint promoted to a durable
attribute), and 3353 turns across ten sessions yielded five facts. Asked for decisions and lessons
instead, the same model returned material equivalent to what the owner writes into markdown by hand."""
from __future__ import annotations

from engram.consolidate.outcomes import OUTCOME_PREDICATES, _windowed, extract_outcomes
from engram.types import Episode


class StubLLM:
    """Records the prompt so the tests can assert how the transcript is presented."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompt = ""
        self.system = ""

    def complete(self, prompt: str, system: str = "", **kwargs) -> str:
        self.prompt, self.system = prompt, system
        return self.reply


def _episodes(n: int = 3) -> list[Episode]:
    return [Episode(content=f"turn {i} with enough words to survive filtering", user_id="u",
                    session_id="s1", speaker="user" if i % 2 else "assistant",
                    event_time=1_700_000_000.0 + i)
            for i in range(n)]


def test_outcomes_become_ordinary_facts_with_kind_predicates():
    """No new type and no new store: outcomes ride the Fact table so they inherit bi-temporal validity,
    supersession and provenance for free."""
    llm = StubLLM('[{"kind":"lesson","statement":"Running --pair overwrites the single global config",'
                  '"why":"took four agents offline"}]')
    facts = extract_outcomes(llm, _episodes(), "u", session_id="s1")
    assert len(facts) == 1
    f = facts[0]
    assert f.predicate == "lesson" and f.predicate in OUTCOME_PREDICATES
    assert f.object.startswith("Running --pair")
    assert "took four agents offline" in f.text  # the reasoning is embedded, so cause is searchable
    assert f.display == f.object
    # attributed to the whole session: a conclusion is supported by the arc, not by one turn
    assert len(f.provenance) == 3
    assert f.valid_at == _episodes()[-1].event_time


def test_unknown_kinds_and_degenerate_statements_are_rejected():
    llm = StubLLM('['
                  '{"kind":"gossip","statement":"something long enough to pass length checks"},'
                  '{"kind":"lesson","statement":"too short"},'
                  '{"kind":"finding","statement":"' + "x" * 500 + '"},'
                  '{"kind":"finding","statement":"A real finding with adequate length here"}]')
    facts = extract_outcomes(llm, _episodes(), "u")
    assert [f.object for f in facts] == ["A real finding with adequate length here"]


def test_duplicate_statements_collapse():
    llm = StubLLM('[{"kind":"decision","statement":"We chose rsync over docker compose"},'
                  '{"kind":"decision","statement":"We chose rsync over docker compose"}]')
    assert len(extract_outcomes(llm, _episodes(), "u")) == 1


def test_transcript_is_fenced_as_data_not_conversation():
    """A body starting with "[assistant] ..." was read as a conversation to continue: on a real
    1164-turn session the model wrote the next turn instead of extracting, and yielded nothing."""
    llm = StubLLM("[]")
    extract_outcomes(llm, _episodes(), "u")
    assert "<session>" in llm.prompt and "</session>" in llm.prompt
    assert not llm.prompt.lstrip().startswith("[")


def test_long_sessions_keep_both_ends():
    """Plain truncation showed the model the first 7% of a 335k-char session — the setup, before
    anything was decided. Conclusions live at the end."""
    turns = [f"turn {i} " + "x" * 200 for i in range(300)]
    out = _windowed(turns, 4000)
    assert len(out) <= 4100
    assert "turn 0 " in out and "turn 299 " in out and "省略" in out


def test_a_session_with_nothing_durable_yields_nothing():
    assert extract_outcomes(StubLLM("[]"), _episodes(), "u") == []


def test_model_outage_never_loses_the_session():
    class Broken:
        def complete(self, *a, **k):
            raise RuntimeError("provider down")

    assert extract_outcomes(Broken(), _episodes(), "u") == []


def test_truncated_model_output_salvages_complete_objects():
    """A reply cut off by max_tokens used to discard every extracted item — which is exactly what long
    sessions produce, since the model fills its budget."""
    from engram.consolidate.llm_extractor import parse_json_facts

    truncated = ('[{"kind":"finding","statement":"first complete one","why":"a"},'
                 '{"kind":"lesson","statement":"second complete one","why":"b"},'
                 '{"kind":"decis')
    got = parse_json_facts(truncated)
    assert [g["statement"] for g in got] == ["first complete one", "second complete one"]
