"""Engram MCP server — long-term memory tools for any Model Context Protocol client.

    pip install "engram-memory[mcp]"
    python -m engram.mcp

See engram/mcp/server.py for the tools and engram/mcp/backends.py for local vs. remote modes.
"""
from __future__ import annotations

__all__ = ["mcp", "backend", "set_backend", "make_backend",
           "Backend", "LocalBackend", "RemoteBackend"]


def __getattr__(name: str):  # PEP 562 — defer importing the MCP SDK until actually used
    if name in ("mcp", "backend", "set_backend"):
        from . import server

        return getattr(server, name)
    if name in ("make_backend", "Backend", "LocalBackend", "RemoteBackend"):
        from . import backends

        return getattr(backends, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
