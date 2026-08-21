"""Image captioning helpers (CLAUDE.md §6 — multimodal ingestion).

Engram's retrieval is text-based, so an image enters memory the same way Mem0/Supermemory do it: a
vision model writes a dense caption, and THAT text is what we store, embed, and retrieve. These helpers
are pure-stdlib — they operate on a *captioner* object (anything exposing `.caption(image_ref, prompt)`,
e.g. `LiteLLMClient`) passed in by the service layer, so this module never imports a model backend and
the zero-setup path (no captioner) degrades to a placeholder instead of failing.
"""
from __future__ import annotations

from typing import Any, Optional


def caption_image(captioner: Any, image_ref: str, prompt: Optional[str] = None) -> str:
    """Caption one image (a data: URL or http(s) URL). Returns "" when there's no vision-capable captioner
    or the call fails — the caller substitutes a placeholder, so a bad/missing model never breaks ingest."""
    if captioner is None or not image_ref:
        return ""
    fn = getattr(captioner, "caption", None)
    if not callable(fn):  # a plain text LLM with no vision path
        return ""
    try:
        return (fn(image_ref, prompt) or "").strip()
    except Exception:  # noqa: BLE001 -- a non-vision model / transient error -> placeholder, never crash
        return ""


def image_refs(content: Any) -> list[str]:
    """Pull image references out of an OpenAI multimodal `content` array — the inverse of the text-only
    `connectors.extract_text` (which drops images). Each ref is a data:/http(s) URL ready for caption_image."""
    refs: list[str] = []
    if isinstance(content, (list, tuple)):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                u = part.get("image_url")
                if isinstance(u, dict):
                    u = u.get("url")
                if isinstance(u, str) and u:
                    refs.append(u)
    return refs


def caption_content(captioner: Any, content: Any, placeholder: str = "[image]") -> list[str]:
    """Caption every image part in a multimodal content array. Returns one line per image — the caption
    when available, else the bare placeholder so the image still leaves a searchable provenance trace."""
    lines: list[str] = []
    for ref in image_refs(content):
        cap = caption_image(captioner, ref)
        lines.append(f"[image] {cap}" if cap else placeholder)
    return lines
