"""Agentic iterative retrieval (CLAUDE.md Bet B) — targets the field's weakest spot, multi-session /
multi-hop. Instead of one embedding lookup, an LLM decomposes the question into sub-queries, we retrieve
per sub-query, then do a follow-up round informed by what was found (so 2nd-hop entities surface). This is
a flagged, ablatable layer: if it doesn't move multi-session on the harness, it gets dropped."""
from __future__ import annotations

import json
import re

DECOMP_SYSTEM = (
    "You plan retrieval for a question answered from a user's long chat history. Break the question into "
    "1-4 short, standalone search queries whose results together contain the evidence to answer it. For "
    "multi-hop questions, include the intermediate lookups. Output ONLY a JSON array of query strings."
)
DECOMP_TEMPLATE = "Question: {query}\n{hint}\nJSON queries:"


def _parse_query_list(raw: str, fallback: str) -> list[str]:
    if not raw:
        return [fallback]
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            qs = [str(x).strip() for x in data if str(x).strip()]
            if qs:
                return qs
        except json.JSONDecodeError:
            pass
    return [fallback]


class AgenticRetriever:
    def __init__(self, memory, llm, max_subqueries: int = 4, per_query_k: int = 3, rounds: int = 2):
        self.mem = memory
        self.llm = llm
        self.max_subqueries = max_subqueries
        self.per_query_k = per_query_k
        self.rounds = rounds

    def _subqueries(self, query: str, hint: str = "") -> list[str]:
        hint_block = f"What we've found so far (use it to plan follow-up lookups):\n{hint}\n" if hint else ""
        raw = self.llm.complete(DECOMP_TEMPLATE.format(query=query, hint=hint_block), system=DECOMP_SYSTEM)
        return _parse_query_list(raw, query)[: self.max_subqueries]

    def gather_episodes(self, query: str, user_id: str, k: int):
        """Return up to k episodes gathered across decomposed sub-queries + one follow-up round."""
        seen: dict = {}

        def pull(qs: list[str]):
            for sq in qs:
                for ep in self.mem.retrieve_episodes(sq, user_id, self.per_query_k):
                    seen.setdefault(ep.id, ep)

        pull(self._subqueries(query))
        if self.rounds > 1 and seen:
            hint = "\n".join(ep.content[:200] for ep in list(seen.values())[:5])
            pull(self._subqueries(query, hint=hint))
        return list(seen.values())[:k]
