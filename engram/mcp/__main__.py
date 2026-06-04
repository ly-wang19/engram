"""Run the Engram MCP server.

  # local memory (zero external service) over stdio — for Claude Desktop / Claude Code / Cursor:
  python -m engram.mcp
  python -m engram.mcp --namespace work          # a separate memory namespace

  # proxy a running Engram HTTP server (hosted / multi-tenant):
  python -m engram.mcp --api-url http://localhost:8000 --api-key sk-alice-123

  # serve over streamable HTTP instead of stdio:
  python -m engram.mcp --http --port 8765

Claude Desktop config (claude_desktop_config.json):
  {"mcpServers": {"engram": {"command": "python", "args": ["-m", "engram.mcp"]}}}
"""
from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m engram.mcp",
        description="Engram MCP server — long-term memory tools for any MCP client.")
    ap.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1", help="bind host for --http (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="bind port for --http (default 8765)")
    ap.add_argument("--namespace", "-n", help="local memory namespace (default $ENGRAM_NAMESPACE or 'me')")
    ap.add_argument("--api-url", help="proxy a running Engram server (default $ENGRAM_API_URL); else local")
    ap.add_argument("--api-key", help="Bearer key for --api-url (default $ENGRAM_API_KEY)")
    args = ap.parse_args()

    if args.namespace:
        os.environ["ENGRAM_NAMESPACE"] = args.namespace
    if args.api_url:
        os.environ["ENGRAM_API_URL"] = args.api_url
    if args.api_key:
        os.environ["ENGRAM_API_KEY"] = args.api_key

    from .server import backend, mcp

    # Build the backend up front so a misconfiguration (unreachable server, missing embedder extra)
    # surfaces here — NOT mid-tool-call. All status goes to stderr: stdout is the stdio JSON-RPC channel.
    b = backend()
    print(f"engram_mcp ready · {b.describe()}", file=sys.stderr)
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print(f"  transport: streamable-http on http://{args.host}:{args.port}/mcp", file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        print("  transport: stdio", file=sys.stderr)
        mcp.run()


if __name__ == "__main__":
    main()
