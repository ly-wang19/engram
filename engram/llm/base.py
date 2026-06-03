"""LLM interface. Production extraction/adjudication/summarization route through a real model
(via LiteLLM: Kimi, DeepSeek, GPT, Qwen, local). Offline, consolidation falls back to deterministic
rules, so an LLM is optional for the demo + tests."""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLM(ABC):
    @abstractmethod
    def complete(self, prompt: str, **kwargs) -> str: ...
