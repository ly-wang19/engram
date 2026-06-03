"""Engram quickstart -- the full pipeline, zero setup, no API keys.

    python examples/quickstart.py

It ingests a short multi-session history (including a job change), consolidates it into bi-temporal
facts + a knowledge graph, and then demonstrates: single-hop QA, knowledge-update / conflict handling,
multi-hop reasoning over the graph, point-in-time ("as-of") queries, and abstention.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Memory  # noqa: E402
from engram.util import DAY  # noqa: E402

BASE = 1_700_000_000.0  # a fixed epoch so the demo is deterministic


def main() -> None:
    mem = Memory()

    history = [
        ("My name is Wei and I work at Tencent.", 0),
        ("I live in Shenzhen.", 1),
        ("My favorite programming language is Python.", 2),
        ("Actually I just switched jobs — I now work at Moonshot AI.", 30),
        ("My colleague Lin works at Moonshot AI too.", 31),
    ]

    print("== Ingest (System-1, fast write path) ==")
    for text, day in history:
        mem.add(text, user_id="wei", event_time=BASE + day * DAY)
        print(f"   day {day:>2}: {text}")

    print("\n== Consolidate (System-2: extract facts, build graph, resolve conflicts) ==")
    print("  ", mem.consolidate())

    print("\n== Current profile (L3 identity) ==")
    print("  ", mem.profile("wei"))

    print("\n== Queries ==")
    for q in [
        "Where does Wei work?",  # single-hop, must reflect the job change
        "What is my favorite programming language?",  # first-person attribute
        "Which city does Wei live in?",
        "Where does Wei's colleague work?",  # multi-hop: Wei -> Lin -> Moonshot
        "What is the capital of France?",  # not in memory -> abstain
    ]:
        r = mem.search(q, user_id="wei")
        print(f"   [{r.via:9}] {q}\n              -> {r.answer()!r}")

    print("\n== Knowledge update: history of Wei's employer (nothing is deleted) ==")
    for f in mem.history("Wei", "works_at", user_id="wei"):
        state = "current" if f.is_live() else "superseded"
        print(f"   {f.object:12} [{state}]  supersedes={f.supersedes}")

    print("\n== Bi-temporal as-of query: what did we believe on day 10 (before the switch)? ==")
    r = mem.as_of("Where does Wei work?", BASE + 10 * DAY, user_id="wei")
    print(f"   day 10 -> {r.answer()!r}   (today -> {mem.search('Where does Wei work?', user_id='wei').answer()!r})")

    print("\n== Identity resolution: merge two handles into one person ==")
    mem.link_identity("wei", "wei@moonshot.ai")
    r = mem.search("Where does Wei work?", user_id="wei@moonshot.ai")
    print(f"   query as 'wei@moonshot.ai' -> {r.answer()!r}  (same memories, resolved identity)")


if __name__ == "__main__":
    main()
