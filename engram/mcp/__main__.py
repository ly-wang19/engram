"""Run the Engram MCP server.

  # local memory (zero external service) over stdio — for Claude Desktop / Claude Code / Cursor:
  python -m engram.mcp
  python -m engram.mcp --namespace work          # a separate memory namespace

  # proxy a running Engram HTTP server (hosted / multi-tenant):
  python -m engram.mcp --api-url http://localhost:8000 --api-key sk-alice-123

  # serve over streamable HTTP instead of stdio (loopback only unless a token is set):
  python -m engram.mcp --http --port 8765
  python -m engram.mcp --http --host 0.0.0.0 --http-token <random-secret>   # remote MCP clients

Claude Desktop config (claude_desktop_config.json):
  {"mcpServers": {"engram": {"command": "python", "args": ["-m", "engram.mcp"]}}}
"""
from __future__ import annotations

import argparse
import hmac
import os
import sys

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class _BearerGate:
    """Minimal ASGI gate: require `Authorization: Bearer <token>` on every HTTP request.

    The MCP streamable-HTTP transport has no authentication of its own, so anyone who can reach the
    port can read and write the memory namespace behind it. Loopback binding is the default guard;
    this gate is what makes a non-loopback bind safe. Same failure-closed philosophy as the REST
    server's ENGRAM_API_KEYS.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        auth = ""
        for key, value in scope.get("headers") or []:
            if key.decode("latin-1").lower() == "authorization":
                auth = value.decode("latin-1")
                break
        supplied = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not (supplied and hmac.compare_digest(supplied, self.token)):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.app(scope, receive, send)


def _require_http_token(host: str, token: str, allow_open: bool) -> None:
    """Fail closed: refuse a non-loopback --http bind without a token unless explicitly opened."""
    if host in _LOOPBACK_HOSTS or token or allow_open:
        return
    raise SystemExit(
        f"refusing to serve unauthenticated MCP over HTTP on non-loopback host {host!r}: anyone who can "
        "reach this port could read and write this memory namespace. Set --http-token (or "
        "ENGRAM_MCP_HTTP_TOKEN), keep the default 127.0.0.1 bind behind a reverse proxy, or set "
        "ENGRAM_MCP_HTTP_OPEN=1 only if an external layer already enforces access control."
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m engram.mcp",
        description="Engram MCP server — long-term memory tools for any MCP client.")
    ap.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1", help="bind host for --http (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="bind port for --http (default 8765)")
    ap.add_argument("--http-token", default=os.environ.get("ENGRAM_MCP_HTTP_TOKEN", ""),
                    help="Bearer token required on every --http request "
                         "(default $ENGRAM_MCP_HTTP_TOKEN; mandatory for non-loopback binds)")
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
        allow_open = os.environ.get("ENGRAM_MCP_HTTP_OPEN", "").strip().lower() in {"1", "true", "yes", "on"}
        _require_http_token(args.host, args.http_token, allow_open)
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        guard = "Bearer token required" if args.http_token else "no token (loopback/explicit-open)"
        print(f"  transport: streamable-http on http://{args.host}:{args.port}/mcp · {guard}",
              file=sys.stderr)
        if args.http_token:
            import uvicorn

            uvicorn.run(_BearerGate(mcp.streamable_http_app(), args.http_token),
                        host=args.host, port=args.port)
        else:
            mcp.run(transport="streamable-http")
    else:
        print("  transport: stdio", file=sys.stderr)
        mcp.run()


if __name__ == "__main__":
    main()
