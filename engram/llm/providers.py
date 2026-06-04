"""Convenience: load a local .env and build LiteLLMClient/embedders for friendly provider names.

This keeps API keys out of code and out of git (.env is gitignored). Used by the eval harness.
"""
from __future__ import annotations

import os


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency). Does not overwrite already-set env vars."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def make_llm(name: str, **overrides):
    """Build a LiteLLMClient from a friendly name:

    deepseek | deepseek-reasoner | qwen-plus | qwen-turbo | qwen-max |
    univibe:<model> (OpenAI relay) | univibe-claude:<model> | <raw litellm model id>
    """
    from .litellm_llm import LiteLLMClient

    n = name.strip()
    if n in ("deepseek", "deepseek-chat"):
        return LiteLLMClient("deepseek/deepseek-chat", **overrides)
    if n == "deepseek-reasoner":
        return LiteLLMClient("deepseek/deepseek-reasoner", **overrides)
    if n.startswith("qwen"):
        return LiteLLMClient(
            f"openai/{n}",
            api_base=os.environ.get("ALI_BASE_URL"),
            api_key=os.environ.get("ALI_API_KEY") or os.environ.get("DASHSCOPE_API_KEY"),
            **overrides,
        )
    if n.startswith("univibe-claude:"):
        return LiteLLMClient(
            f"anthropic/{n.split(':', 1)[1]}",
            api_base=os.environ.get("UNIVIBE_ANTHROPIC_BASE"),
            api_key=os.environ.get("UNIVIBE_API_KEY"),
            **overrides,
        )
    if n.startswith("univibe:"):
        return LiteLLMClient(
            f"openai/{n.split(':', 1)[1]}",
            api_base=os.environ.get("UNIVIBE_OPENAI_BASE"),
            api_key=os.environ.get("UNIVIBE_API_KEY"),
            **overrides,
        )
    # Volcano Engine / 火山方舟 (ByteDance Ark) — OpenAI-compatible; models by name or endpoint id (ep-...)
    if n.startswith(("volcano:", "ark:", "doubao:")):
        return LiteLLMClient(
            f"openai/{n.split(':', 1)[1]}",
            api_base=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            api_key=os.environ.get("ARK_API_KEY"),
            **overrides,
        )
    return LiteLLMClient(n, **overrides)


def make_embedder(name: str = "bge-small", **overrides):
    """hashing (zero-dep offline) | bge-small | bge-m3 | bge-large | st:<model> (sentence-transformers)
    | openai:<model> (LiteLLM)."""
    n = name.strip()
    # The deterministic, dependency-free fallback — lets the server/MCP run with NO model download
    # (ENGRAM_EMBEDDER=hashing) and keeps tests offline. Not benchmark-grade; lexical overlap only.
    if n in ("hashing", "hash", "offline", "none"):
        from ..config import Config
        from ..embed import HashingEmbedder

        dim = overrides.get("dim") or Config().embed_dim
        return HashingEmbedder(dim)
    presets = {
        "bge-small": "BAAI/bge-small-en-v1.5",
        "bge-large": "BAAI/bge-large-en-v1.5",
        "bge-m3": "BAAI/bge-m3",
    }
    if n in presets or n.startswith("st:"):
        from ..embed import SentenceTransformerEmbedder

        model = presets.get(n) or n.split(":", 1)[1]
        return SentenceTransformerEmbedder(model, **overrides)
    if n.startswith("openai:"):
        from ..embed import LiteLLMEmbedder

        return LiteLLMEmbedder(
            n.split(":", 1)[1],
            **overrides,
        )
    from ..embed import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder(n, **overrides)


def make_reranker(name: str = "none", **overrides):
    """none/off -> None; bge-reranker | bge-reranker-large | bge-reranker-v2 | <raw HF cross-encoder>."""
    n = (name or "none").strip()
    if n in ("none", "off", ""):
        return None
    presets = {
        "bge-reranker": "BAAI/bge-reranker-base",
        "bge-reranker-large": "BAAI/bge-reranker-large",
        "bge-reranker-v2": "BAAI/bge-reranker-v2-m3",
    }
    from ..retrieve.rerank import CrossEncoderReranker

    return CrossEncoderReranker(presets.get(n, n), **overrides)
