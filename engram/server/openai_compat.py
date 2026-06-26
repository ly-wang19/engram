"""OpenAI-compatible chat endpoint with transparent long-term memory (CLAUDE.md §6 adoption layer).

Point any OpenAI client's `base_url` at an Engram server and you get memory for free: before the model
answers, Engram recalls a small relevant slice of the user's history and injects it; after, it remembers
the turn (off the critical path). This is the single biggest drop-in adoption lever — change one URL,
keep your existing OpenAI SDK code.

    from openai import OpenAI
    client = OpenAI(base_url="http://localhost:8000/v1", api_key="sk-alice-123")
    client.chat.completions.create(model="engram", messages=[{"role":"user","content":"where do I live?"}])

This module is deliberately FastAPI-free (pure orchestration over MemoryService + the LLM interface), so
the assembly logic is unit-testable without the web stack. The route wiring lives in engram/server/app.py.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..connectors.base import extract_text  # reuse: flattens str | multimodal parts | dict content
from ..util import gen_id, now


class NoLLMConfigured(RuntimeError):
    """Raised when generation is requested but the server has no LLM backend (set ENGRAM_LLM)."""


# The instruction that frames the injected memory for the model.
_MEMORY_PREAMBLE = (
    "You have access to long-term memory about this user, retrieved below. Treat it as known context "
    "and use it when relevant; prefer the most recent dated fact when values conflict. If the memory "
    "doesn't cover the question, answer normally.\n\nRELEVANT MEMORY:\n"
)


def _content(msg: dict) -> str:
    return extract_text(msg.get("content"))


def latest_user_text(messages: list) -> str:
    """The most recent user turn — what we recall against and (optionally) remember."""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            text = _content(m).strip()
            if text:
                return text
    return ""


def build_prompt(messages: list, memory_context: str) -> tuple[Optional[str], str]:
    """Render the request's messages into a (system, prompt) pair for the LLM.complete interface:
      * system = the injected memory block + any of the request's own system messages
      * prompt = the single user turn, or the full transcript for a multi-turn conversation
    Backend-agnostic on purpose: it works with any LLM (incl. the offline FakeLLM in tests), not only
    a litellm chat model."""
    system_parts: list[str] = []
    if memory_context.strip():
        system_parts.append(_MEMORY_PREAMBLE + memory_context.strip())
    system_parts += [c for c in (_content(m) for m in messages
                                 if isinstance(m, dict) and m.get("role") == "system") if c.strip()]
    system = "\n\n".join(system_parts) or None

    convo = [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not convo:
        prompt = ""
    elif len(convo) == 1:
        prompt = _content(convo[0])
    else:
        rendered = "\n".join(f"{m['role'].capitalize()}: {_content(m)}" for m in convo)
        prompt = rendered + "\nAssistant:"
    return system, prompt


def _est_tokens(text: str) -> int:
    return len((text or "").split())


def chat_completion(
    svc: Any,
    user: str,
    body: dict,
    n_chunks: int = 6,
    do_recall: bool = True,
    as_of: Optional[float] = None,
    redact_sensitive: bool = False,
) -> dict:
    """Recall → inject → generate → return an OpenAI ChatCompletion object (with an `engram` extension
    describing what memory was used). Does NOT write memory — the route schedules that off the critical
    path. Raises NoLLMConfigured if generation is requested without an LLM backend."""
    messages = body.get("messages") or []
    model = body.get("model") or "engram"
    query = latest_user_text(messages) or (_content(messages[-1]) if messages else "")

    memory_context = ""
    if do_recall and query:
        memory_context = (
            svc.recall(
                user,
                query,
                lean=True,
                n_chunks=n_chunks,
                as_of=as_of,
                redact_sensitive=redact_sensitive,
            ).get("context") or ""
        )

    if svc.llm is None:
        raise NoLLMConfigured(
            "no LLM configured for generation — set ENGRAM_LLM (e.g. 'deepseek'), or use /v1/recall "
            "for retrieval-only.")

    system, prompt = build_prompt(messages, memory_context)
    content = svc.llm.complete(prompt, system=system)

    p_tokens = _est_tokens((system or "") + " " + prompt)
    c_tokens = _est_tokens(content)
    return {
        "id": gen_id("chatcmpl"),
        "object": "chat.completion",
        "created": int(now()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": p_tokens, "completion_tokens": c_tokens,
                  "total_tokens": p_tokens + c_tokens},
        # Engram extension — transparency about the memory layer (ignored by standard OpenAI clients).
        "engram": {
            "recalled": bool(memory_context.strip()),
            "memory_tokens_est": _est_tokens(memory_context),
            "as_of": as_of,
            "redacted_sensitive": redact_sensitive,
            "remembered": False,  # the route flips this to True when it schedules the write
        },
    }


def iter_sse(response: dict, slice_size: int = 48):
    """Yield a minimal, valid OpenAI SSE stream for `stream=true` clients. Generation isn't truly
    incremental (the LLM interface returns a full string), so we slice the finished content into chunks
    — the reassembled stream is byte-identical to the non-streamed content."""
    base = {"id": response["id"], "object": "chat.completion.chunk",
            "created": response["created"], "model": response["model"]}

    def chunk(delta: dict, finish: Optional[str]):
        payload = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    content = response["choices"][0]["message"]["content"] or ""
    yield chunk({"role": "assistant"}, None)
    for i in range(0, len(content), slice_size):
        yield chunk({"content": content[i:i + slice_size]}, None)
    yield chunk({}, "stop")
    yield "data: [DONE]\n\n"
