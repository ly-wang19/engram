"""Memory spaces + access control (CLAUDE.md §6 — multi-agent / team-shared memory).

A *space* is a memory namespace — exactly what `MemoryService` already keys on. A single API key
(a *principal*) can WRITE one space and READ several: its own + a shared team space + an end-user's.
This is the user/agent/session scoping Mem0/Letta/Zep expose, layered on top of the existing
per-namespace stores WITHOUT touching the memory engine: the service is already space-keyed, so a write
just targets a (validated) namespace string and a read fans out + fuses across several.

Richer principals are configured with ENGRAM_SPACES (JSON):

    ENGRAM_SPACES='{"sk-team":{"home":"agentA","read":["agentA","team"],"write":["agentA","team"]}}'

`read`/`write` default to [home] when omitted; the home space is always readable+writable by its owner.
Single-space auth (ENGRAM_API_KEYS / ENGRAM_OPEN) is unchanged and handled by the server's `auth` —
this module only resolves ENGRAM_SPACES principals, so all of the server's token-comparison hardening
stays in one place and multi-space remains purely additive.
"""
from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Principal:
    """Who is calling and which spaces they may touch. `home` is the default read/write namespace."""

    home: str
    readable: list[str] = field(default_factory=list)
    writable: list[str] = field(default_factory=list)

    def can_read(self, space: str) -> bool:
        return space in self.readable

    def can_write(self, space: str) -> bool:
        return space in self.writable

    @classmethod
    def single(cls, user: str) -> "Principal":
        """The back-compatible one-key-one-namespace principal (ENGRAM_API_KEYS / open mode)."""
        return cls(home=user, readable=[user], writable=[user])


def load_spaces() -> dict[str, dict]:
    """ENGRAM_SPACES (JSON) -> {api_key: {home, read[], write[]}}. Malformed JSON is treated as unset —
    a broken value must not silently grant or widen access."""
    raw = os.environ.get("ENGRAM_SPACES", "").strip()
    if not raw:
        return {}
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _principal_from_spec(spec: dict) -> Optional[Principal]:
    if not isinstance(spec, dict):
        return None
    home = str(spec.get("home") or "").strip()
    if not home:
        return None  # a space principal must declare its home namespace
    read = [str(s) for s in (spec.get("read") or [home])]
    write = [str(s) for s in (spec.get("write") or [home])]
    # the owner can always read+write its own home, regardless of how read/write were listed
    if home not in read:
        read = [home, *read]
    if home not in write:
        write = [home, *write]
    return Principal(home=home, readable=read, writable=write)


def principal_for_token(token: str) -> Optional[Principal]:
    """Resolve a bearer token against ENGRAM_SPACES, or None when it matches no configured space key.
    Uses constant-time comparison like the server's key auth (same hardening, same reason)."""
    token = (token or "").strip()
    if not token:
        return None
    for key, spec in load_spaces().items():
        if hmac.compare_digest(token, str(key)):
            return _principal_from_spec(spec)
    return None
