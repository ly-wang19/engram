"""A no-network LLM stand-in. It does not actually reason; it exists so code paths that *optionally*
call an LLM don't crash when none is configured. Consolidation's real offline behavior lives in the
rule-based components, not here."""
from __future__ import annotations

from .base import LLM


class OfflineLLM(LLM):
    def complete(self, prompt: str, **kwargs) -> str:  # noqa: ARG002
        return ""
