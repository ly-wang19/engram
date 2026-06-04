"""LLM conflict DETECTION for the ambiguous tail (the cheap structural rules in conflict.py handle the
high-frequency, high-confidence cases; this catches what they can't enumerate). Runs in System-2 (async
consolidation, off the write path), only over CANDIDATE pairs (cheap embedding pre-filter, not O(n²) LLM
calls), and NEVER auto-resolves — a suspected conflict becomes a pending `Conflict` for the user to
confirm. COEXIST is the default, so we never silently corrupt memory. Opt-in (Config.conflict_detection);
offline / tests stay pure-rule and deterministic.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..embed import Embedder
from ..llm import LLM
from ..types import Conflict, Fact
from ..util import cosine

_JUDGE_SYSTEM = (
    "你判断关于同一个人的两条记忆是否冲突——即其中一条是否更新/否定/取代了另一条(同一属性的新值、"
    "偏好反转、地址/职业/称呼变更等),还是两者可以同时成立(不同的事、互补的偏好)。"
    "只回复一个词:CONFLICT 或 COEXIST。拿不准就回 COEXIST。"
)


def _candidate_pairs(facts: list[Fact], seen: set[tuple], sim_lo: float, sim_hi: float):
    """Cheap pre-filter: same-subject pairs that are embedding-near-but-not-identical (a possible
    rephrased/updated attribute) OR same object under a different predicate (a possible reversal the
    rules missed). Already-judged pairs are skipped, so repeat runs only surface NEW candidates."""
    by_subj: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        by_subj[f.subject.lower()].append(f)
    out: list[tuple[Fact, Fact]] = []
    for fs in by_subj.values():
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                a, b = fs[i], fs[j]
                if tuple(sorted((a.id, b.id))) in seen:
                    continue
                same_obj = a.object.strip().lower() == b.object.strip().lower()
                if same_obj and a.predicate == b.predicate:
                    continue  # identical claim — a dup, not a conflict
                same_obj_diff_pred = same_obj and a.predicate != b.predicate
                near = (a.embedding and b.embedding and sim_lo <= cosine(a.embedding, b.embedding) <= sim_hi)
                if same_obj_diff_pred or near:
                    out.append((a, b))
    return out


def detect_conflicts(facts: list[Fact], llm: LLM, user_id: str, seen: set[tuple],
                     embedder: Optional[Embedder] = None, max_calls: int = 16,
                     sim_lo: float = 0.62, sim_hi: float = 0.94) -> list[Conflict]:
    """Return NEW pending conflicts among `facts` (the user's live set). One LLM call per candidate pair,
    capped at `max_calls`; COEXIST-default so we only flag genuine suspected conflicts."""
    candidates = _candidate_pairs(facts, seen, sim_lo, sim_hi)
    conflicts: list[Conflict] = []
    for a, b in candidates[:max_calls]:
        try:
            verdict = llm.complete(f"A: {a.text}\nB: {b.text}\n\n它们冲突吗?", system=_JUDGE_SYSTEM)
        except Exception:  # noqa: BLE001 -- detection must never break consolidation
            continue
        if "conflict" not in verdict.strip().lower():
            continue  # COEXIST (or unparseable) -> no flag, keep both
        older, newer = (a, b) if a.valid_at <= b.valid_at else (b, a)
        conflicts.append(Conflict(
            older=older.id, newer=newer.id, text_older=older.text, text_newer=newer.text,
            user_id=user_id, reason=verdict.strip()[:80],
        ))
    return conflicts
