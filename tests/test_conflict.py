"""Conflict-detection unit tests (CLAUDE.md Bet C). Cover the two regressions that made the facts layer
inert on knowledge-update: (1) single-valued was a 3-item allow-list so most updates never fired;
(2) free-form LLM predicates for the same attribute ("attends_yoga" vs "does_yoga") never shared a slot.

These exercise ConflictResolver directly with hand-set embeddings, so they're deterministic and need no
model. `cosine` is used as-is; the embedder arg is only a presence gate for the semantic path."""
from __future__ import annotations

from engram.consolidate.conflict import ConflictResolver, is_single_valued
from engram.types import Fact

_SENTINEL_EMBEDDER = object()  # non-None: enables the semantic path (no .embed() call is made)


def _fact(subject, predicate, obj, t, emb=None):
    return Fact(subject=subject, predicate=predicate, object=obj, user_id="u",
                valid_at=float(t), created_at=float(t), embedding=emb)


def test_single_valued_is_default_not_an_allowlist():
    # the old bug: only works_at/lives_in/name_is/favorite_* counted. Now state predicates default to
    # single-valued; only the explicit accumulating set is multi-valued.
    assert is_single_valued("studying")
    assert is_single_valued("current_city")
    assert is_single_valued("job_title")
    assert not is_single_valued("likes")
    assert not is_single_valued("owns")
    assert not is_single_valued("visited")
    # goals/projects/events accumulate (tuned out of single-valued after diagnostics)
    assert not is_single_valued("goal")
    assert not is_single_valued("working_on")
    assert not is_single_valued("booked")


def test_exact_slot_update_invalidates_non_allowlist_predicate():
    r = ConflictResolver()  # no embedder needed for exact-slot
    old = _fact("user", "studying", "biology", 1)
    new = _fact("user", "studying", "chemistry", 2)
    action, inv = r.reconcile(new, [old])
    assert action == "add"
    assert inv == [old] and old.invalid_at == new.valid_at and new.supersedes == old.id


def test_multivalued_predicate_accumulates():
    r = ConflictResolver(_SENTINEL_EMBEDDER)
    old = _fact("user", "likes", "pizza", 1, emb=[1.0, 0.0])
    new = _fact("user", "likes", "pasta", 2, emb=[1.0, 0.0])  # near-identical embedding, but 'likes' accrues
    action, inv = r.reconcile(new, [old])
    assert action == "add" and inv == []  # both kept


def test_semantic_same_subject_different_predicate_supersedes():
    # the yoga case: same attribute, different free-form predicate, different value, later in time.
    r = ConflictResolver(_SENTINEL_EMBEDDER, sim_threshold=0.80)
    old = _fact("user", "does_yoga", "twice a week", 1, emb=[1.0, 0.0])
    new = _fact("user", "attends_yoga", "three times a week", 2, emb=[0.99, 0.01])
    action, inv = r.reconcile(new, [old])
    assert inv == [old] and not old.is_live() and new.supersedes == old.id


def test_semantic_below_threshold_keeps_both():
    r = ConflictResolver(_SENTINEL_EMBEDDER, sim_threshold=0.80)
    old = _fact("user", "job", "teacher", 1, emb=[1.0, 0.0])      # orthogonal embedding -> unrelated
    new = _fact("user", "hobby", "painting", 2, emb=[0.0, 1.0])
    action, inv = r.reconcile(new, [old])
    assert inv == []


def test_semantic_off_without_embedder():
    # offline/hashing path: only exact-slot fires, so a near-but-different-predicate pair is NOT merged.
    r = ConflictResolver(None)
    old = _fact("user", "does_yoga", "twice a week", 1, emb=[1.0, 0.0])
    new = _fact("user", "attends_yoga", "three times a week", 2, emb=[1.0, 0.0])
    action, inv = r.reconcile(new, [old])
    assert inv == []


def test_semantic_never_supersedes_across_subjects():
    r = ConflictResolver(_SENTINEL_EMBEDDER, sim_threshold=0.50)
    old = _fact("wei", "lives_in", "Shenzhen", 1, emb=[1.0, 0.0])
    new = _fact("lin", "lives_in", "Beijing", 2, emb=[1.0, 0.0])  # different person, near embedding
    action, inv = r.reconcile(new, [old])
    assert inv == []  # different subjects never merge
