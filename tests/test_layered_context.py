"""Splitting the read context into a cacheable half and a per-query half.

The whole feature rests on one property: the stable half must be byte-identical across a user's turns.
If it drifts with the question, prompt-caching misses every turn and the split costs more than the flat
context it replaced. `test_stable_block_is_identical_across_queries` is that property, and
`test_no_evidence_is_lost_by_splitting` is the guard that the split does not quietly drop retrieval.
"""
from __future__ import annotations

from engram.memory import Memory
from engram.retrieve.layered import RECALL_GUIDE, layered_context, memory_map
from engram.util import DAY, now

QUERIES = [
    "where does alice work",
    "what does alice drink",
    "when did alice go to kyoto",
    "who is alice's manager",
]


def _memory() -> Memory:
    mem = Memory()
    base = now() - 30 * DAY
    for i, text in enumerate(
        [
            "Alice works at Acme Corp as a staff engineer.",
            "Alice prefers oat milk in her coffee.",
            "Alice travelled to Kyoto in April for a conference.",
            "Alice's manager is Bob.",
            "Alice is learning to play the cello.",
        ]
    ):
        mem.add(text, user_id="u1", session_id=f"s{i}", event_time=base + i * DAY)
    mem.consolidate()
    mem.summarize_episodes(list(mem.episodes_doc.values()))
    return mem


def test_stable_block_is_identical_across_queries():
    """The property the feature exists for: a differing question must not change the cached half."""
    mem = _memory()
    blocks = {layered_context(mem, q, "u1").stable for q in QUERIES}
    assert len(blocks) == 1, "the stable half must not vary with the query, or caching never hits"


def test_dynamic_block_does_vary_with_the_query():
    """And the other half must actually be doing per-query work."""
    mem = _memory()
    blocks = {layered_context(mem, q, "u1").dynamic for q in QUERIES}
    assert len(blocks) > 1


def test_no_evidence_is_lost_by_splitting():
    """Splitting must not drop retrieval: everything the flat path shows must still be present."""
    mem = _memory()
    query = "where does alice work"
    flat = mem.lean_context(query, user_id="u1")
    layered = layered_context(mem, query, "u1")

    for line in (ln.strip() for ln in flat.splitlines()):
        if line.startswith("- ") and len(line) > 8:
            assert line in layered.text, f"evidence dropped by the split: {line!r}"


def test_profile_is_not_duplicated_across_the_halves():
    """The profile lives in the cached half; repeating it in the dynamic half would spend exactly the
    tokens this split saves."""
    mem = _memory()
    layered = layered_context(mem, "where does alice work", "u1")
    assert "USER PROFILE" in layered.stable
    assert "USER PROFILE" not in layered.dynamic


def test_guide_is_in_the_cached_half_and_optional():
    mem = _memory()
    assert RECALL_GUIDE in layered_context(mem, "q", "u1").stable
    assert RECALL_GUIDE not in layered_context(mem, "q", "u1", guide=False).stable


def test_as_messages_places_each_half_in_its_own_turn():
    mem = _memory()
    layered = layered_context(mem, "where does alice work", "u1")
    messages = layered.as_messages("where does alice work", system="You are a helpful assistant.")

    assert [m["role"] for m in messages] == ["system", "user"]
    assert "You are a helpful assistant." in messages[0]["content"]
    assert layered.stable in messages[0]["content"]
    assert layered.dynamic in messages[1]["content"]
    assert "where does alice work" in messages[1]["content"]


# --- memory map ---


def test_memory_map_is_recency_ordered_and_query_independent():
    """Ranking by relevance would reorder the map every turn and defeat the caching."""
    mem = _memory()
    rendered = memory_map(mem, "u1")
    dates = [line.split()[1] for line in rendered.splitlines()[1:]]
    assert dates == sorted(dates, reverse=True)


def test_memory_map_is_tenant_scoped():
    mem = _memory()
    mem.add("Bob works at Globex.", user_id="u2", session_id="other")
    assert "Globex" not in memory_map(mem, "u1")


def test_memory_map_respects_as_of():
    """An as-of read must not reveal sessions from after the time being asked about."""
    mem = _memory()
    cutoff = now() - 29 * DAY
    rendered = memory_map(mem, "u1", as_of=cutoff)
    assert rendered.count("\n- ") + (1 if "\n- " not in rendered and "- " in rendered else 0) <= 2
    assert "cello" not in rendered


def test_memory_map_is_empty_without_episodes():
    assert memory_map(Memory(), "nobody") == ""


def test_memory_map_is_off_by_default():
    """Measured as a net token cost below long sessions, so callers opt in (see results/)."""
    mem = _memory()
    assert "MEMORY MAP" not in layered_context(mem, "q", "u1").stable
    assert memory_map(mem, "u1", limit=0) == "", "a zero limit must render nothing, not a bare header"


def test_redacted_context_omits_profile_and_map():
    """A redacted context is structured-facts-only; free-text layers can fold in sensitive content."""
    mem = _memory()
    layered = layered_context(
        mem, "where does alice work", "u1", map_limit=20, redact_sensitive=True
    )
    assert "USER PROFILE" not in layered.stable
    assert "MEMORY MAP (" not in layered.stable
    assert RECALL_GUIDE in layered.stable, "the abstention guide carries no user content"


def test_guide_only_mentions_the_map_when_one_is_present():
    """Pointing the model at a section that is not there invites it to ask for the unavailable."""
    mem = _memory()
    assert "MEMORY MAP" in layered_context(mem, "q", "u1", map_limit=20).stable
    assert "MEMORY MAP" not in layered_context(mem, "q", "u1").stable, "map is off by default"
    assert "MEMORY MAP" not in layered_context(
        mem, "q", "u1", map_limit=20, redact_sensitive=True
    ).stable


def test_memory_method_matches_the_module():
    mem = _memory()
    assert mem.layered_context("where does alice work", "u1").stable == (
        layered_context(mem, "where does alice work", "u1").stable
    )


# --- OpenAI-compatible proxy wiring ---
#
# The proxy already put the whole retrieved slice in the SYSTEM prompt, so the system block changed on
# every turn and no provider prompt cache could ever match a prefix. The split's job on this surface is
# to make that block byte-identical; the tests below pin both halves of that claim.


def _proxy_setup():
    from engram.server import openai_compat as oc

    mem = _memory()

    class _Svc:
        """Only what chat_completion touches."""

        def __init__(self, memory):
            self._mem = memory
            self.llm = self

        def get(self, _user):
            return self._mem

        def recall(self, _user, query, **kwargs):
            return {"context": self._mem.lean_context(query, user_id="u1")}

        def complete(self, prompt, system=None):
            return "ok"

    return oc, _Svc(mem)


def test_proxy_system_block_is_stable_across_turns_when_layered():
    oc, svc = _proxy_setup()
    flat, layered = set(), set()
    for query in QUERIES:
        body = {"model": "engram", "messages": [{"role": "user", "content": query}]}
        ctx = svc.recall("u1", query)["context"]
        flat.add(oc.build_prompt(body["messages"], ctx)[0])
        parts = svc.get("u1").layered_context(query, user_id="u1", guide=False)
        layered.add(oc.build_prompt(body["messages"], parts.dynamic, parts.stable)[0])

    # Not "one block per query": two questions can retrieve the same slice. The property is that the
    # unsplit block varies at all — that alone is enough to miss a prefix cache on those turns.
    assert len(flat) > 1, "precondition: today's system block varies across turns"
    assert len(layered) == 1, "the split must make the system block byte-identical, or caching never hits"


def test_proxy_reports_the_cacheable_prefix_size():
    """A caller cannot reason about caching without knowing how much of the prompt is stable."""
    oc, svc = _proxy_setup()
    body = {"model": "engram", "messages": [{"role": "user", "content": "where does alice work"}]}

    plain = oc.chat_completion(svc, "u1", body)
    assert plain["engram"]["cacheable_tokens_est"] == 0, "nothing is stable without the split"

    split = oc.chat_completion(svc, "u1", body, layered=True)
    assert split["engram"]["cacheable_tokens_est"] > 0


def test_proxy_keeps_the_evidence_when_splitting():
    """Moving evidence to the user turn must not drop it."""
    oc, svc = _proxy_setup()
    body = {"model": "engram", "messages": [{"role": "user", "content": "where does alice work"}]}
    parts = svc.get("u1").layered_context("where does alice work", user_id="u1", guide=False)
    system, prompt = oc.build_prompt(body["messages"], parts.dynamic, parts.stable)

    assert parts.stable in (system or "")
    assert parts.dynamic in prompt
    assert "where does alice work" in prompt


def test_proxy_is_unchanged_when_not_layered():
    """The default path must behave exactly as before."""
    oc, svc = _proxy_setup()
    messages = [{"role": "user", "content": "where does alice work"}]
    ctx = svc.recall("u1", "where does alice work")["context"]
    system, prompt = oc.build_prompt(messages, ctx)
    assert ctx.strip() in (system or ""), "unsplit memory still rides in the system block"
    assert prompt == "where does alice work"
