"""A deterministic, no-network LLM for tests and demos. Drive it with a handler(prompt, system)->str
or a fixed list of responses. Records calls for assertions."""
from __future__ import annotations

from typing import Callable, Optional

from .base import LLM


class FakeLLM(LLM):
    def __init__(
        self,
        handler: Optional[Callable[[str, Optional[str]], str]] = None,
        responses: Optional[list[str]] = None,
    ) -> None:
        self._handler = handler
        self._responses = list(responses or [])
        self.calls: list[dict] = []

    def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:  # noqa: ARG002
        self.calls.append({"prompt": prompt, "system": system})
        if self._handler is not None:
            return self._handler(prompt, system)
        if self._responses:
            return self._responses.pop(0)
        return ""
