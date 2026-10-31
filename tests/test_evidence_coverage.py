import json

import pytest

from engram import Memory
from engram.embed.base import Embedder
from engram.types import Episode
from eval.evidence_coverage import (
    CachedEmbedder, coverage, evaluate_item, independent_rrf, open_run, prepare_candidates,
)


class ConstantEmbedder(Embedder):
    dim = 2

    def embed(self, text):
        return [1.0, 0.0]


def test_coverage_requires_all_gold_sessions_not_only_best_rank():
    assert coverage(["a"], {"a", "b"}) == {
        "any_hit": 1, "all_hit": 0, "fractional": .5, "gold_hits": 1,
    }
    assert coverage(["a", "b"], {"a", "b"})["all_hit"] == 1
    with pytest.raises(ValueError):
        coverage([], set())


def test_positive_only_lexical_has_no_zero_score_or_missing_path_votes():
    mem = Memory(embedder=ConstantEmbedder())
    off = mem.add("ordinary weather", user_id="u", session_id="off", event_time=1)
    hit = mem.add("exactneedle", user_id="u", session_id="hit", event_time=1)
    dense, lexical = prepare_candidates(mem, "exactneedle", "u")
    assert [e.id for e in lexical] == [hit.id]
    assert independent_rrf(dense, lexical, 1)[0][0].id == hit.id
    # A lexical-only hit ties the dense-only rank-one candidate, not a fake second vote.
    chosen, ranked = independent_rrf([off], [hit], 1)
    assert chosen == [off] and ranked == [off, hit]


def test_independent_lexical_can_recover_exact_match_outside_dense_pool():
    mem = Memory(embedder=ConstantEmbedder())
    for i in range(30):
        mem.add("ordinary weather", user_id="u", session_id=f"noise{i}", event_time=1)
    target = mem.add("exactneedle", user_id="u", session_id="gold", event_time=1)
    # Equal vectors put the true exact hit behind the dense pool by stable insertion order.
    assert target.id not in {e.id for e in mem.retrieve_episodes("exactneedle", "u", 2)}
    dense, lexical = prepare_candidates(mem, "exactneedle", "u")
    assert target.id in {e.id for e in independent_rrf(dense, lexical, 2)[0]}


def test_candidate_user_and_as_of_filters_match_baseline():
    mem = Memory(embedder=ConstantEmbedder())
    mem.add("exactneedle", user_id="other", session_id="other", event_time=1)
    mem.add("exactneedle", user_id="u", session_id="future", event_time=20)
    allowed = mem.add("exactneedle", user_id="u", session_id="allowed", event_time=5)
    dense, lexical = prepare_candidates(mem, "exactneedle", "u", as_of=10)
    assert [e.id for e in dense] == [allowed.id]
    assert [e.id for e in lexical] == [allowed.id]
    assert mem.retrieve_episodes("exactneedle", "u", 2, as_of=10) == [allowed]


def test_resume_rejects_changed_configuration_and_nonprefix_rows(tmp_path):
    out = tmp_path / "run.jsonl"
    manifest = {"manifest_id": "one", "selected_qids": ["q1", "q2"], "flags": {"as_of": "none"}}
    assert open_run(out, manifest, False) == []
    out.write_text(json.dumps({"qid": "q1", "manifest_id": "one"}) + "\n")
    assert len(open_run(out, manifest, True)) == 1
    changed = dict(manifest, flags={"as_of": "question-date"})
    with pytest.raises(ValueError, match="manifest mismatch"):
        open_run(out, changed, True)
    out.write_text(json.dumps({"qid": "q2", "manifest_id": "one"}) + "\n")
    with pytest.raises(ValueError, match="valid unique prefix"):
        open_run(out, manifest, True)


def test_embedding_cache_reuses_vectors_across_instances_and_model_identity(tmp_path):
    class Counting(ConstantEmbedder):
        def __init__(self):
            self.calls = 0

        def embed(self, text):
            self.calls += 1
            return super().embed(text)

    inner = Counting()
    first = CachedEmbedder(lambda: inner, {"name": "test", "dim": 2}, tmp_path)
    assert first.embed_batch(["same", "same"]) == [[1., 0.], [1., 0.]]
    assert inner.calls == 1
    second = CachedEmbedder(lambda: pytest.fail("cache miss"), {"name": "test", "dim": 2}, tmp_path)
    assert second.embed("same") == [1., 0.]
    third = CachedEmbedder(lambda: inner, {"name": "different", "dim": 2}, tmp_path)
    third.embed("same")
    assert inner.calls == 2
    assert all("same" not in p.read_text() for p in tmp_path.rglob("*.json"))


def test_evaluation_excludes_abs_and_never_serializes_question_or_answer():
    item = {"question_id": "q", "question": "private query needle", "answer": "secret answer",
            "question_type": "multi-session", "answer_session_ids": ["s1", "s2"],
            "haystack_session_ids": ["s1", "s2"],
            "haystack_sessions": [[{"role": "user", "content": "needle one"}],
                                  [{"role": "user", "content": "needle two"}]]}
    row = evaluate_item(item, ConstantEmbedder())
    assert row["results"]["2"]["baseline"]["all_hit"] == 1
    assert row["full_context_oracle_reference"]["fractional"] == 1
    serialized = json.dumps(row)
    assert item["question"] not in serialized and item["answer"] not in serialized
    assert "needle one" not in serialized
    item["question_id"] = "q_abs"
    assert evaluate_item(item, None)["status"] == "excluded_abs"
