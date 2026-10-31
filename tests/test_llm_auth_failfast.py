"""A provider may report a permanent auth failure using HTTP 400 rather than 401."""
from types import SimpleNamespace

import pytest

from engram.llm.litellm_llm import LiteLLMClient


def test_authentication_error_in_bad_request_is_not_retried():
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        raise RuntimeError('{"type":"authentication_error","message":"Authentication Fails"}')

    client = LiteLLMClient.__new__(LiteLLMClient)
    client._litellm = SimpleNamespace(completion=completion)
    client.model, client.temperature, client.max_tokens = "test", 0, 32
    client.max_attempts, client.retry_base, client.retry_cap = 7, 0, 0
    client.kwargs = {}
    with pytest.raises(RuntimeError, match="authentication_error"):
        client.complete("synthetic probe")
    assert len(calls) == 1
