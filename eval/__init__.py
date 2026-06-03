"""Engram's reproducible evaluation harness (CLAUDE.md §4, Bet D).

M0 ships a tiny BUILT-IN synthetic set so `python eval/harness.py` produces real per-category numbers
with zero downloads and no LLM -- it exists to lock in the harness *shape* (per-category accuracy +
tokens + latency, multiple systems, fully reproducible). M1 plugs LOCOMO / LongMemEval datasets and an
LLM judge into this same harness.
"""
