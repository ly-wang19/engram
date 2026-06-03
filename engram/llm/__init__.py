"""LLM backends. OfflineLLM + FakeLLM load eagerly (no deps); LiteLLMClient is lazy so plain
`import engram` never imports litellm."""
from .base import LLM
from .fake import FakeLLM
from .offline import OfflineLLM

__all__ = ["LLM", "OfflineLLM", "FakeLLM", "LiteLLMClient"]


def __getattr__(name: str):
    if name == "LiteLLMClient":
        from .litellm_llm import LiteLLMClient

        return LiteLLMClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
