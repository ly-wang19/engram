"""Embedders. The offline HashingEmbedder loads eagerly (zero deps); the real backends are exposed
lazily so plain `import engram` never pulls in torch / litellm."""
from .base import Embedder
from .hashing import HashingEmbedder

__all__ = ["Embedder", "HashingEmbedder", "SentenceTransformerEmbedder", "LiteLLMEmbedder"]


def __getattr__(name: str):  # PEP 562 lazy submodule attributes
    if name == "SentenceTransformerEmbedder":
        from .sentence_transformers_embed import SentenceTransformerEmbedder

        return SentenceTransformerEmbedder
    if name == "LiteLLMEmbedder":
        from .litellm_embed import LiteLLMEmbedder

        return LiteLLMEmbedder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
