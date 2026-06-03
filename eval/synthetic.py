"""Tiny built-in synthetic eval set covering the categories real memory benchmarks test:
single-hop, knowledge-update, multi-hop, temporal (as-of), and abstention (false premise).

Each item is self-contained (its own multi-session history) so items don't interfere. `answer=None`
means the correct behavior is to ABSTAIN. `as_of_day` runs the query at a point in the past.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EvalItem:
    id: str
    category: str
    sessions: list[tuple[str, int]]  # (text, day_offset)
    question: str
    answer: Optional[str]  # None => abstention expected
    as_of_day: Optional[int] = None


ITEMS: list[EvalItem] = [
    EvalItem(
        "sh1", "single_hop",
        [("My name is Ana and I work at Spotify.", 0), ("I really enjoy hiking on weekends.", 1)],
        "Where does Ana work?", "Spotify",
    ),
    EvalItem(
        "sh2", "single_hop",
        [("My name is Cara and my favorite color is blue.", 0)],
        "What is Cara's favorite color?", "blue",
    ),
    EvalItem(
        "ku1", "knowledge_update",
        [("My name is Ben and I live in Boston.", 0), ("I moved — I now live in Denver.", 60)],
        "Where does Ben live?", "Denver",
    ),
    EvalItem(
        "ku2", "knowledge_update",
        [("My name is Cara and my favorite color is blue.", 0), ("My favorite color is green now.", 40)],
        "What is Cara's favorite color?", "green",
    ),
    EvalItem(
        "mh1", "multi_hop",
        [("My name is Dora.", 0), ("My colleague Evan works at Netflix.", 1)],
        "Where does Dora's colleague work?", "Netflix",
    ),
    EvalItem(
        "tmp1", "temporal",
        [("My name is Finn and I work at IBM.", 0), ("I now work at Oracle.", 50)],
        "Where does Finn work?", "IBM", as_of_day=20,
    ),
    EvalItem(
        "tmp2", "temporal",
        [("My name is Finn and I work at IBM.", 0), ("I now work at Oracle.", 50)],
        "Where does Finn work?", "Oracle",  # current (no as_of)
    ),
    EvalItem(
        "abs1", "abstention",
        [("My name is Gina and I work at Adobe.", 0)],
        "What is Gina's favorite food?", None,  # attribute never stated -> abstain
    ),
    EvalItem(
        "abs2", "abstention",
        [("My name is Hugo and I live in Lisbon.", 0)],
        "What is the capital of France?", None,  # unrelated -> abstain
    ),
]
