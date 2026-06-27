"""Exercise the user-facing Engram lifecycle for one agent session.

Zero-server local smoke test:

    python examples/cross_agent_lifecycle.py --local

Run an Engram server first:

    ENGRAM_OPEN=1 ENGRAM_EMBEDDER=hashing uvicorn engram.server.app:app --port 8000

Then simulate one Codex thread:

    python examples/cross_agent_lifecycle.py \
        --base http://localhost:8000 \
        --key me \
        --agent codex \
        --project super-memory \
        --thread demo

This demonstrates the product loop an agent client should follow:
  1. agent_status: content-free wiring/status check
  2. recall: fetch relevant user/project memory before work
  3. remember: save durable facts/decisions and short-lived working state
  4. close_session: drain consolidation and clear working state at session end
  5. session_report: audit what this session saved
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


Call = Callable[[str, Optional[dict], str], dict]
DURABLE_MEMORIES = [
    "Project rule: benchmark claims require committed raw logs before public copy changes.",
    "Project preference: keep Engram memory integrations automatic; users should not manually decide every remember call.",
]
WORKING_MEMORY = (
    "Current task state: testing the Codex adapter lifecycle; verify the session report before final reply."
)
VERIFY_QUERY = "What project rule should I follow for benchmark claims?"
VERIFY_PHRASE = "committed raw logs"


def http_call(base: str, key: str) -> Call:
    base = base.rstrip("/")

    def call(path: str, body: dict | None = None, method: str = "POST") -> dict:
        req = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"Engram returned HTTP {exc.code} for {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Could not reach Engram at {base}: {exc}") from exc

    return call


def _first_query_value(query: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = query.get(key)
    return values[0] if values else default


def _bool_query_value(query: dict[str, list[str]], key: str, default: bool = False) -> bool:
    value = _first_query_value(query, key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def local_call(key: str, data_dir: str) -> Call:
    from engram.service import MemoryService

    svc = MemoryService(data_dir=data_dir, embedder_name="hashing", llm_name="")

    def call(path: str, body: dict | None = None, method: str = "POST") -> dict:
        body = body or {}
        parsed = urlparse(path)
        query = parse_qs(parsed.query)
        route = parsed.path
        if route == "/v1/agent/status":
            return svc.agent_status(key, session_id=_first_query_value(query, "session_id"))
        if route == "/v1/sessions/report":
            session_id = _first_query_value(query, "session_id")
            if not session_id:
                raise ValueError("session_id is required for /v1/sessions/report")
            return svc.session_report(
                key,
                session_id,
                include_sensitive=_bool_query_value(query, "include_sensitive"),
            )
        if method != "POST":
            raise ValueError(f"local lifecycle smoke only supports GET status/report and POST writes, got {method}")
        if route == "/v1/remember":
            return svc.remember(
                key,
                body["content"],
                session_id=body.get("session_id", "default"),
                scope=body.get("scope", "auto"),
            )
        if route == "/v1/recall":
            return svc.recall(
                key,
                body["query"],
                lean=body.get("lean", True),
                n_chunks=body.get("n_chunks", 3),
                session_id=body.get("session_id"),
                as_of=body.get("as_of"),
                redact_sensitive=body.get("redact_sensitive", False),
                answer=True,
            )
        if route == "/v1/sessions/close":
            return svc.close_session(
                key,
                body.get("session_id", "default"),
                summarize=body.get("summarize", True),
                clear_working=body.get("clear_working", True),
            )
        raise ValueError(f"local lifecycle smoke does not implement {route}")

    return call


def _status_path(session_id: str) -> str:
    return "/v1/agent/status?" + urlencode({"session_id": session_id})


def _report_path(session_id: str, include_sensitive: bool = False) -> str:
    return "/v1/sessions/report?" + urlencode({
        "session_id": session_id,
        "include_sensitive": "true" if include_sensitive else "false",
    })


def exercise_lifecycle(
    call: Call,
    *,
    namespace: str,
    agent: str = "codex",
    project: str = "super-memory",
    thread: str = "demo",
    no_close: bool = False,
    output: Callable[[str], None] = print,
) -> dict:
    session_id = f"{agent}:{project}:{thread}"

    output(f"namespace: {namespace}")
    output(f"session_id: {session_id}")

    output("\n== 1. Agent status before work ==")
    status_before = call(_status_path(session_id), None, "GET")
    session_before = status_before.get("session") or {}
    output(
        "agent_status: "
        f"user={status_before.get('user')} "
        f"episodes={session_before.get('episodes', 0)} "
        f"working_live={session_before.get('working_live', 0)}"
    )

    output("\n== 2. Recall before work ==")
    recalled_before = call("/v1/recall", {
        "query": f"Current rules and preferences for project {project}",
        "session_id": session_id,
        "n_chunks": 3,
    }, "POST")
    context_before = (recalled_before.get("context") or "").strip()
    output(context_before[:600] if context_before else "(no relevant memory yet)")

    output("\n== 3. Remember durable memory and working state ==")
    remembered: list[dict] = []
    for text in DURABLE_MEMORIES:
        out = call("/v1/remember", {
            "content": text,
            "session_id": session_id,
            "scope": "long",
        }, "POST")
        remembered.append(out)
        output(f"- long ({out.get('extracted', 0)} extracted): {text}")

    working = call("/v1/remember", {
        "content": WORKING_MEMORY,
        "session_id": session_id,
        "scope": "working",
    }, "POST")
    output(f"- working ({working.get('kind', 'state')}): {WORKING_MEMORY}")

    output("\n== 4. Recall the saved durable memory ==")
    recalled_after = call("/v1/recall", {
        "query": VERIFY_QUERY,
        "session_id": session_id,
        "n_chunks": 3,
    }, "POST")
    context_after = (recalled_after.get("context") or "").strip()
    output(recalled_after.get("answer") or context_after[:1000] or "(no relevant memory found)")

    status_after_write = call(_status_path(session_id), None, "GET")
    session_after_write = status_after_write.get("session") or {}
    output(
        "agent_status_after_write: "
        f"episodes={session_after_write.get('episodes', 0)} "
        f"working_live={session_after_write.get('working_live', 0)}"
    )

    closed = None
    if not no_close:
        output("\n== 5. Close the agent session ==")
        closed = call("/v1/sessions/close", {
            "session_id": session_id,
            "summarize": True,
            "clear_working": True,
        }, "POST")
        output(json.dumps(closed, ensure_ascii=False, indent=2))

    output("\n== 6. Session report: what this session saved ==")
    report = call(_report_path(session_id), None, "GET")
    facts = report.get("facts") or []
    output(
        "session_report: "
        f"episodes={report.get('episodes', 0)} "
        f"facts={len(facts)} "
        f"redacted={report.get('redacted', 0)}"
    )
    for fact in facts[:8]:
        output(f"- {fact.get('display') or fact.get('text')}")

    found = VERIFY_PHRASE in context_after or any(
        VERIFY_PHRASE in ((fact.get("text") or "") + " " + (fact.get("display") or ""))
        for fact in facts
    )
    if found:
        output("\nPASS: lifecycle saved durable memory and the session report audits it.")
    if closed and closed.get("working_cleared", 0) > 0:
        output("PASS: close_session cleared this session's working memory.")

    return {
        "session_id": session_id,
        "status_before": status_before,
        "recalled_before": recalled_before,
        "context_before": context_before,
        "remembered": remembered,
        "working": working,
        "recalled_after": recalled_after,
        "context_after": context_after,
        "status_after_write": status_after_write,
        "closed": closed,
        "report": report,
        "found": found,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Exercise Engram's agent memory lifecycle.")
    ap.add_argument("--local", action="store_true",
                    help="use local MemoryService storage instead of an HTTP server")
    ap.add_argument("--data-dir", default=None,
                    help="local Engram data dir for --local (default: temporary smoke-test dir)")
    ap.add_argument("--base", default="http://localhost:8000", help="Engram HTTP base URL")
    ap.add_argument("--key", default="me", help="Bearer key = user memory namespace")
    ap.add_argument("--agent", default="codex", help="agent name used in the session id")
    ap.add_argument("--project", default="super-memory", help="project/workspace name")
    ap.add_argument("--thread", default="demo", help="thread/conversation id")
    ap.add_argument("--no-close", action="store_true", help="skip /v1/sessions/close")
    ap.add_argument("--no-verify", action="store_true", help="do not fail if recall/report misses the test phrase")
    args = ap.parse_args(argv)

    if args.local:
        data_dir = args.data_dir or tempfile.mkdtemp(prefix="engram-lifecycle.")
        print("mode: local")
        print(f"data_dir: {data_dir}")
        call = local_call(args.key, data_dir)
    else:
        base = args.base.rstrip("/")
        print("mode: http")
        print(f"base: {base}")
        call = http_call(base, args.key)

    result = exercise_lifecycle(
        call,
        namespace=args.key,
        agent=args.agent,
        project=args.project,
        thread=args.thread,
        no_close=args.no_close,
    )

    if not result["found"] and not args.no_verify:
        print("\nFAIL: lifecycle did not recall or report the saved durable memory.", file=sys.stderr)
        raise SystemExit(1)

    if args.local:
        print("\nFor a real local Codex setup, point its MCP config at the same Engram namespace/data dir.")
    else:
        print(f"\nInspect in the console: {args.base.rstrip('/')}/ui/  (key: {args.key})")


if __name__ == "__main__":
    main()
