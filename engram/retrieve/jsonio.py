"""Robust JSON extraction from LLM output — tolerates code fences and surrounding prose.

LLMs wrap JSON in ```json fences, prepend explanation, or add a trailing period. These helpers pull the
first well-formed array/object out of that noise and fall back to None on failure, so a chatty or malformed
model response degrades to "no plan" instead of crashing the read path. Shared by the multi-hop planner;
the agentic retriever uses the same parse shape inline.
"""
from __future__ import annotations

import json
import re
from typing import Optional


def _strip_fences(raw: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()


def loads_array(raw: str) -> Optional[list]:
    """The first JSON array in `raw`, or None if there isn't a parseable one."""
    if not raw:
        return None
    text = _strip_fences(raw)
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return None


def loads_object(raw: str) -> Optional[dict]:
    """The first JSON object in `raw`, or None if there isn't a parseable one."""
    if not raw:
        return None
    text = _strip_fences(raw)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None
