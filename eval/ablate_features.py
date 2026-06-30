"""Offline algorithm ablations for the newest read-path evidence features.

This is not a public benchmark claim. It is a zero-key smoke proof that each feature adds evidence the
disabled variant cannot surface:

    python eval/ablate_features.py

The real publishable gate remains eval/bench.py on LongMemEval/LOCOMO with raw JSONL logs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Config, Memory  # noqa: E402
from engram.types import Fact  # noqa: E402
from engram.util import DAY  # noqa: E402

BASE = 1_700_000_000.0


@dataclass(frozen=True)
class AblationResult:
    feature: str
    enabled_hit: bool
    disabled_hit: bool
    improved: bool
    enabled_tokens: int
    disabled_tokens: int
    enabled_latency_ms: float
    disabled_latency_ms: float
    target: str


def _tokens(text: str) -> int:
    return len(text.split())


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000.0


def _chain_context(enabled: bool) -> str:
    mem = Memory(config=Config(chain_evidence=enabled))
    mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)
    return mem.lean_context(
        "Where does Wei work?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )


def _temporal_history_context(enabled: bool) -> str:
    mem = Memory(config=Config(temporal_history_queries=enabled))
    mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)
    res = mem.search("Where did Wei work before Moonshot AI?", user_id="u1")
    return res.answer()


def _summary_fallback_context(enabled: bool) -> str:
    mem = Memory(config=Config(summary_fallback=enabled))
    ep = mem.add(
        "To rotate the PAT: open security settings, regenerate the token, then update CI secrets.",
        user_id="u1",
        session_id="pat-runbook",
        event_time=BASE,
    )
    mem.summarize_episodes([ep])
    res = mem.search("How do I rotate the PAT?", user_id="u1")
    return f"{res.via}\n{res.answer()}"


def _raw_context(enabled: bool) -> str:
    mem = Memory(config=Config(evidence_planner=False, provenance_evidence=enabled))
    ep = mem.add(
        "The Apollo launch code is A17. Keep the printed checklist near the blue binder.",
        user_id="u1",
        session_id="apollo",
        event_time=BASE,
    )
    fact = Fact(
        subject="Apollo",
        predicate="launch_code",
        object="A17",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[ep.id],
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)
    return mem.lean_context(
        "What is Apollo's launch code?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )


def _provenance_chunk_context(enabled: bool) -> str:
    mem = Memory(
        config=Config(
            evidence_planner=False,
            provenance_evidence=False,
            provenance_chunk_promotion=enabled,
        )
    )
    source = mem.add(
        "A17 is written on the tag tucked inside the blue binder.",
        user_id="u1",
        session_id="apollo-source",
        event_time=BASE,
    )
    for i in range(5):
        mem.add(
            f"Apollo launch code rehearsal note {i}: the team reviewed old checklist formats.",
            user_id="u1",
            session_id=f"apollo-distractor-{i}",
            event_time=BASE + (i + 1) * DAY,
        )
    fact = Fact(
        subject="Apollo",
        predicate="launch_code",
        object="A17",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[source.id],
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)
    return mem.lean_context(
        "What is Apollo's launch code?",
        user_id="u1",
        persona=False,
        n_summaries=0,
        n_chunks=1,
        char_budget=10_000,
    )


def _evidence_budget_context(enabled: bool) -> str:
    mem = Memory(config=Config(evidence_budgeting=enabled))
    ep = mem.add(
        "The Apollo launch code is A17. Keep the printed checklist near the blue binder.",
        user_id="u1",
        session_id="apollo",
        event_time=BASE,
    )
    fact = Fact(
        subject="Apollo",
        predicate="launch_code",
        object="A17",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[ep.id],
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)
    for i in range(12):
        mem.add_fact(
            "Apollo",
            "project_note",
            f"background filler note {i} with operational chatter that is not the checklist location",
            user_id="u1",
            valid_at=BASE + (i + 1) * DAY,
        )
    return mem.lean_context(
        "What is Apollo's launch code and where is the printed checklist?",
        user_id="u1",
        persona=False,
        n_summaries=0,
        n_chunks=0,
        char_budget=360,
    )


def _graph_context(enabled: bool) -> str:
    mem = Memory(config=Config(graph_proximity=enabled))
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)
    return mem.context_for("Tell me about Wei", user_id="u1", k_chunks=0, graph=True)


def _graph_relation_context(enabled: bool) -> str:
    mem = Memory(config=Config(graph_relation_awareness=enabled))
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Lin", "likes", "jazz", user_id="u1", valid_at=BASE + 2 * DAY)
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 3 * DAY)
    return mem.context_for("Where is Wei's colleague's company based?", user_id="u1", k_chunks=0, graph=True)


def _graph_reinforcement_context(enabled: bool) -> str:
    mem = Memory(config=Config(graph_path_reinforcement=enabled))
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "mentor", "Maya", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Lin", "works_on", "Atlas", user_id="u1", valid_at=BASE + 2 * DAY)
    mem.add_fact("Maya", "works_on", "Atlas", user_id="u1", valid_at=BASE + 3 * DAY)
    mem.add_fact("Lin", "works_on", "Zephyr", user_id="u1", valid_at=BASE + 4 * DAY)
    mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + 5 * DAY)
    mem.add_fact("Zephyr", "based_in", "Lisbon", user_id="u1", valid_at=BASE + 6 * DAY)
    return mem.context_for("Where is Wei's project based?", user_id="u1", k_chunks=0, graph=True)


def _graph_self_anchor_context(enabled: bool) -> str:
    mem = Memory(config=Config(graph_self_anchor=enabled))
    mem.add_fact("u1", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + DAY)
    return mem.context_for("Where is my project based?", user_id="u1", k_chunks=0, graph=True)


def _graph_entity_alias_context(enabled: bool) -> str:
    mem = Memory(config=Config(graph_entity_alias_anchor=enabled))
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE)
    return mem.context_for("Where is Moonshot based?", user_id="u1", k_chunks=0, graph=True)


def _graph_negative_context(enabled: bool) -> str:
    mem = Memory(config=Config(graph_negative_constraints=enabled))
    mem.add_fact("Wei", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_on", "Zephyr", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + 2 * DAY)
    mem.add_fact("Zephyr", "based_in", "Lisbon", user_id="u1", valid_at=BASE + 3 * DAY)
    return mem.context_for("Where is Wei's project not in Lisbon based?", user_id="u1", k_chunks=0, graph=True)


def _planner_location_context(enabled: bool) -> str:
    mem = Memory(config=Config(planner_location_chains=enabled))
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)
    res = mem.search("Where is Wei's colleague's company based?", user_id="u1")
    return res.answer()


def _planner_project_context(enabled: bool) -> str:
    mem = Memory(config=Config(planner_project_chains=enabled))
    mem.add_fact("Wei", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + DAY)
    res = mem.search("Where is Wei's project based?", user_id="u1")
    return res.answer()


def _contains(marker: str, target: str):
    return lambda ctx: marker in ctx and target in ctx


def _before(target: str, distractor: str):
    def judge(ctx: str) -> bool:
        scope = ctx.split("RELATED FACTS (graph traversal):", 1)[-1]
        return target in scope and distractor in scope and scope.index(target) < scope.index(distractor)

    return judge


def run_ablation() -> tuple[list[AblationResult], dict]:
    cases = [
        (
            "chain_evidence",
            _chain_context,
            _contains("FACT EVOLUTION (retrieved supersession chain):", "Tencent"),
            "Tencent",
        ),
        (
            "temporal_history_queries",
            _temporal_history_context,
            lambda answer: "Tencent" in answer,
            "natural-language previous-value query reads supersession history",
        ),
        (
            "summary_fallback",
            _summary_fallback_context,
            lambda answer: "summary" in answer and "security settings" in answer and "pat-runbook" in answer,
            "derived session summary answers fact-miss how-to query",
        ),
        (
            "provenance_evidence",
            _raw_context,
            _contains("PROVENANCE RAW EVIDENCE (source episodes for retrieved facts):", "blue binder"),
            "blue binder",
        ),
        (
            "provenance_chunk_promotion",
            _provenance_chunk_context,
            _contains("RELEVANT CONVERSATIONS (full detail):", "blue binder"),
            "source episode promoted into full-detail raw chunk",
        ),
        (
            "evidence_budgeting",
            _evidence_budget_context,
            lambda ctx: "blue binder" in ctx,
            "tight budget keeps exact raw evidence",
        ),
        (
            "graph_proximity",
            _graph_context,
            _contains("RELATED FACTS (graph traversal):", "Moonshot AI based in Beijing"),
            "Moonshot AI based in Beijing",
        ),
        (
            "graph_relation_awareness",
            _graph_relation_context,
            _before("Moonshot AI based in Beijing", "Lin likes jazz"),
            "target relation before same-node distractor",
        ),
        (
            "graph_path_reinforcement",
            _graph_reinforcement_context,
            _before("Atlas based in Reykjavik", "Zephyr based in Lisbon"),
            "multi-path target before single-path distractor",
        ),
        (
            "graph_self_anchor",
            _graph_self_anchor_context,
            _contains("RELATED FACTS (graph traversal):", "Atlas based in Reykjavik"),
            "first-person query anchors to user graph node",
        ),
        (
            "graph_entity_alias_anchor",
            _graph_entity_alias_context,
            _contains("RELATED FACTS (graph traversal):", "Moonshot AI based in Beijing"),
            "unique short name anchors to full graph entity",
        ),
        (
            "graph_negative_constraints",
            _graph_negative_context,
            lambda ctx: "Atlas based in Reykjavik" in ctx and "Zephyr based in Lisbon" not in ctx,
            "negative query constraint filters excluded graph path",
        ),
        (
            "planner_location_chains",
            _planner_location_context,
            lambda answer: "Beijing" in answer,
            "multi-hop planner reaches company location",
        ),
        (
            "planner_project_chains",
            _planner_project_context,
            lambda answer: "Reykjavik" in answer,
            "multi-hop planner reaches project location",
        ),
    ]
    rows: list[AblationResult] = []
    for name, builder, judge, target in cases:
        enabled_ctx, enabled_lat = _timed(lambda b=builder: b(True))
        disabled_ctx, disabled_lat = _timed(lambda b=builder: b(False))
        enabled_hit = judge(enabled_ctx)
        disabled_hit = judge(disabled_ctx)
        rows.append(
            AblationResult(
                feature=name,
                enabled_hit=enabled_hit,
                disabled_hit=disabled_hit,
                improved=enabled_hit and not disabled_hit,
                enabled_tokens=_tokens(enabled_ctx),
                disabled_tokens=_tokens(disabled_ctx),
                enabled_latency_ms=enabled_lat,
                disabled_latency_ms=disabled_lat,
                target=target,
            )
        )
    summary = {
        "type": "summary",
        "n": len(rows),
        "improved": sum(1 for r in rows if r.improved),
        "enabled_hits": sum(1 for r in rows if r.enabled_hit),
        "disabled_hits": sum(1 for r in rows if r.disabled_hit),
    }
    return rows, summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Run zero-key feature ablations for Engram read-path evidence.")
    ap.add_argument("--jsonl", help="write per-feature rows plus a summary as JSONL")
    args = ap.parse_args()

    rows, summary = run_ablation()
    print("Engram feature ablation smoke -- offline, zero API keys")
    print("feature                  enabled  disabled  improved  target")
    print("-" * 78)
    for r in rows:
        print(
            f"{r.feature:24} {'HIT' if r.enabled_hit else '--':7} "
            f"{'HIT' if r.disabled_hit else '--':8} {'YES' if r.improved else 'NO':8} {r.target}"
        )
    print("-" * 78)
    print(f"improved {summary['improved']}/{summary['n']} features")

    if args.jsonl:
        with open(args.jsonl, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r.__dict__, ensure_ascii=False, sort_keys=True) + "\n")
            fh.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"raw ablation log written to {args.jsonl}")


if __name__ == "__main__":
    main()
