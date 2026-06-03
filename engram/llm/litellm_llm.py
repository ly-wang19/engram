"""LLM backend via LiteLLM -- one interface to OpenAI, Anthropic, DeepSeek, Moonshot/Kimi, Gemini,
Qwen, OpenRouter, local servers, etc. Requires the matching provider key in the environment."""
from __future__ import annotations

from typing import Optional

from .base import LLM


class LiteLLMClient(LLM):
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        max_attempts: int = 7,
        retry_base: float = 1.0,
        retry_cap: float = 30.0,
        **kwargs,
    ) -> None:
        import litellm  # lazy

        self._litellm = litellm
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Outer-loop resilience knobs. Kept separate from litellm's own `num_retries` (which stays in
        # kwargs and handles fast in-process retries): these govern the SLOW relay-recovery backoff.
        self.max_attempts = max_attempts
        self.retry_base = retry_base
        self.retry_cap = retry_cap
        self.kwargs = kwargs

    # Substrings (lowercased) marking an error as PERMANENT — config/auth/request problems that retrying
    # cannot fix. Everything else (connection reset, timeout, SSL EOF, 429/5xx, "overloaded") is treated
    # as transient and retried, because that is empirically ~100% of what kills a parallel relay run.
    _PERMANENT = (
        "authenticationerror", "invalid api key", "invalid_api_key", "incorrect api key",
        "permissiondenied", "permission denied", "no api key", "api key not",
    )

    def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        import random
        import time

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        params = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        params.update(self.kwargs)
        params.update(kwargs)
        # Outer backoff loop on top of litellm's own num_retries. Relays (e.g. univibe) and provider APIs
        # (deepseek) drop connections under concurrency; litellm's fast in-process retries exhaust before
        # the relay recovers. Earlier this loop was 4 tries / linear 1.5s backoff and a 500-question run at
        # 6 workers still LOST ~53% of items to transient errors — invalidating the whole benchmark. Now:
        # more attempts, exponential backoff with full jitter (defeats the thundering-herd lockstep across
        # workers), retry on empty content (the relay sometimes 200s with an empty body), and a fast-fail
        # only on genuinely permanent auth errors so we don't burn the budget on a misconfig.
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_attempts):
            try:
                resp = self._litellm.completion(**params)
                content = resp.choices[0].message.content
                if content:
                    return content
                # Empty body from a flaky relay: treat as transient rather than silently returning "".
                last_exc = RuntimeError("empty completion content")
            except Exception as e:  # noqa: BLE001 - classify, then retry transient / fast-fail permanent
                last_exc = e
                msg = f"{type(e).__name__}: {e}".lower()
                if any(p in msg for p in self._PERMANENT):
                    raise
            if attempt < self.max_attempts - 1:
                delay = min(self.retry_base * (2 ** attempt), self.retry_cap)
                time.sleep(delay * (0.5 + random.random()))  # full jitter: 0.5x..1.5x
        raise last_exc  # type: ignore[misc]
