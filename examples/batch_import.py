"""Batch-import a chat history into Engram -- zero setup, no API keys.

    python examples/batch_import.py

Demonstrates the §6 adoption layer's import path: take an exported history (here a tiny inline
ChatGPT-style export and an OpenAI messages array), normalize it with engram.connectors, ingest it in
ONE batched pass, then recall from it. The same `parse(...)` powers `python -m engram.connectors`, the
`POST /v1/import` endpoint, and the MCP `engram_import` tool.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Memory  # noqa: E402
from engram.connectors import parse, sniff  # noqa: E402

# A miniature ChatGPT `conversations.json` (one conversation, an active 2-node thread).
CHATGPT_EXPORT = [{
    "title": "Moving to Berlin",
    "create_time": 1_700_000_000.0,
    "current_node": "b",
    "mapping": {
        "a": {"id": "a", "parent": None, "children": ["b"],
              "message": {"author": {"role": "user"}, "create_time": 1_700_000_000.0,
                          "content": {"content_type": "text",
                                      "parts": ["I'm moving to Berlin next month for a job at Acme."]}}},
        "b": {"id": "b", "parent": "a", "children": [],
              "message": {"author": {"role": "assistant"}, "create_time": 1_700_000_100.0,
                          "content": {"content_type": "text", "parts": ["Exciting! Berlin is great."]}}},
    },
}]

# An OpenAI-style messages array (a separate "session").
OPENAI_MESSAGES = [
    {"role": "user", "content": "By the way, I'm vegetarian and allergic to peanuts."},
    {"role": "assistant", "content": "Noted — I'll keep that in mind."},
]


def main() -> None:
    mem = Memory()  # offline: hashing embedder + rule extractor, zero setup

    print("== Detect + parse two different export formats ==")
    chatgpt_sessions = parse(CHATGPT_EXPORT)  # auto-sniffed
    openai_sessions = parse(OPENAI_MESSAGES, format="messages")
    print(f"   chatgpt export  -> sniffed as '{sniff(CHATGPT_EXPORT)}', {len(chatgpt_sessions)} session(s)")
    print(f"   openai messages -> {len(openai_sessions)} session(s)")

    print("\n== Batch-import both (one consolidation pass each) ==")
    print("  ", mem.import_messages(chatgpt_sessions, user_id="me"))
    print("  ", mem.import_messages(openai_sessions, user_id="me"))

    print("\n== Recall from the imported memory ==")
    for q in ["Where am I moving and why?", "What are my dietary restrictions?"]:
        ctx = mem.lean_context(q, user_id="me", n_chunks=2)
        snippet = " ".join(ctx.split())[:160]
        print(f"   Q: {q}\n      -> {snippet}…\n")

    # One-liner equivalent: mem.import_data(raw_text_or_object, format="auto", user_id="me")


if __name__ == "__main__":
    main()
