"""CLI: import an external chat/history export into Engram.

  # local, file-backed memory (zero setup — hashing embedder + rule extractor):
  python -m engram.connectors --file conversations.json --namespace me

  # higher-quality local import (real embeddings / LLM extractor; needs the extras + keys):
  python -m engram.connectors -f conversations.json -n me --embedder bge-small --llm deepseek

  # push into a running Engram server instead of a local store:
  python -m engram.connectors -f conversations.json --api-url http://localhost:8000 --key sk-alice-123

  # from stdin with an explicit format:
  cat log.jsonl | python -m engram.connectors --format jsonl -n me

This module is pure-stdlib; the remote POST uses urllib (no httpx needed) so import works in the
zero-setup install.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import urllib.request

from . import parse, sniff


def _read_input(path: str | None) -> str:
    if path and path != "-":
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return fh.read()
    return sys.stdin.read()


def _post_remote(api_url: str, key: str, sessions: list, consolidate: bool, summarize: bool) -> dict:
    body = json.dumps({
        "sessions": [dataclasses.asdict(s) for s in sessions],
        "consolidate": consolidate,
        "summarize": summarize,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(api_url.rstrip("/") + "/v1/import", data=body,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310 (user-supplied URL is intentional)
        return json.load(resp)


def _import_local(args, sessions) -> dict:
    from .. import Memory  # lazy: keep the connectors package importable without the full facade graph

    embedder = llm = None
    if args.embedder or args.llm:
        from ..llm.providers import load_dotenv, make_embedder, make_llm
        load_dotenv()
        if args.embedder:
            embedder = make_embedder(args.embedder)
        if args.llm:
            llm = make_llm(args.llm)

    os.makedirs(os.path.expanduser(args.data_dir), exist_ok=True)
    safe = "".join(c for c in args.namespace if c.isalnum() or c in "-_.") or "me"
    base = os.path.expanduser(args.data_dir)
    path = os.path.join(base, safe)
    legacy = os.path.join(base, f"{safe}.pkl")
    if os.path.exists(legacy) and not os.path.exists(path):
        path = legacy
    mem = Memory.open(path, embedder=embedder, llm=llm)
    stats = mem.import_messages(sessions, user_id=args.namespace,
                                consolidate=not args.no_consolidate, summarize=not args.no_summarize)
    mem.save()
    stats["store"] = path
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m engram.connectors",
                                 description="Import a chat/history export into Engram memory.")
    ap.add_argument("--file", "-f", help="export file (default: stdin)")
    ap.add_argument("--format", default="auto",
                    help="chatgpt | messages | records | jsonl | transcript | auto (default)")
    ap.add_argument("--namespace", "-n", default="me", help="memory namespace / user id (default: me)")
    ap.add_argument("--data-dir", default=os.environ.get("ENGRAM_DATA_DIR", "~/.engram/data"),
                    help="local store dir (local mode; default ~/.engram/data)")
    ap.add_argument("--api-url", help="push to a running Engram server instead of a local store")
    ap.add_argument("--key", default=os.environ.get("ENGRAM_API_KEY", ""), help="Bearer key for --api-url")
    ap.add_argument("--embedder", help="local mode: real embedder (e.g. bge-small); default hashing")
    ap.add_argument("--llm", help="local mode: LLM for fact extraction (e.g. deepseek); default rule-based")
    ap.add_argument("--no-consolidate", action="store_true", help="skip fact extraction (raw episodes only)")
    ap.add_argument("--no-summarize", action="store_true", help="skip L2 session summaries")
    ap.add_argument("--dry-run", action="store_true", help="parse + report only; don't ingest")
    args = ap.parse_args()

    raw = _read_input(args.file)
    fmt = sniff(raw) if args.format == "auto" else args.format
    sessions = parse(raw, format=args.format)
    n_msgs = sum(len(s.messages) for s in sessions)
    print(f"parsed {len(sessions)} session(s), {n_msgs} message(s) [format: {fmt}]", file=sys.stderr)

    if args.dry_run:
        for s in sessions[:3]:
            preview = s.to_text()[:200].replace("\n", " ")
            print(f"  - {s.session_id}: {preview}…", file=sys.stderr)
        return
    if not sessions:
        print("nothing to import.", file=sys.stderr)
        return

    if args.api_url:
        stats = _post_remote(args.api_url, args.key, sessions,
                             not args.no_consolidate, not args.no_summarize)
    else:
        stats = _import_local(args, sessions)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
