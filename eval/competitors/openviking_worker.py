"""Out-of-process worker that answers one LongMemEval question's *context* from ByteDance OpenViking.

This file is executed by OpenViking's own virtualenv, NOT by the Engram harness's interpreter. OpenViking
0.3.x is a heavy install (Rust RAGFS + a C++ vector engine + its own pinned litellm/pydantic), and
importing it into the harness process risks resolving those pins against Engram's. The harness therefore
shells out: JSON item in on stdin, assembled context text out on stdout. Anything the worker prints to
stderr is diagnostics; only stdout is the context.

    <openviking>/.venv/bin/python openviking_worker.py < item.json > context.txt

Input JSON:
    {"qid": str, "question": str, "topk": int, "workspace": str|null, "read_chars": int,
     "sessions": [{"sid": str, "created_at": str|null, "turns": [{"role","content"}]}]}

Per-question isolation is by OpenViking's own tenant key (UserIdentifier account/user/agent), which is
how the engine is designed to keep users apart — each LongMemEval question is its own user.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import traceback


def log(msg: str) -> None:
    print(f"[ov_worker] {msg}", file=sys.stderr, flush=True)


async def build_context(item: dict) -> str:
    import openviking
    from openviking.client.local import LocalClient  # noqa: F401  (type only)

    qid = str(item["qid"])
    safe = re.sub(r"[^A-Za-z0-9_]", "_", qid)[:48] or "q"
    topk = int(item.get("topk") or 15)
    read_chars = int(item.get("read_chars") or 1500)

    # Isolation has to be by storage path, not by tenant: AsyncOpenViking.__init__ hard-codes
    # `self.user = UserIdentifier.the_default_user()` and forwards only `path` to LocalClient, so a
    # `user=` argument is silently ignored and every question would share `viking://user/default`.
    # One workspace directory per question, one process per question, no cross-question leakage.
    kwargs = {}
    ws = item.get("workspace")
    if ws:
        path = os.path.join(ws, safe)
        os.makedirs(path, exist_ok=True)
        kwargs["path"] = path
    cli = openviking.AsyncOpenViking(**kwargs)
    await cli.initialize()
    try:
        for s in item["sessions"]:
            sid = re.sub(r"[^A-Za-z0-9_]", "_", str(s["sid"]))[:48]
            msgs = []
            for t in s["turns"]:
                content = (t.get("content") or "").strip()
                if not content:
                    continue
                m = {"role": t.get("role", "user"), "content": content}
                if s.get("created_at"):
                    m["created_at"] = s["created_at"]
                msgs.append(m)
            if not msgs:
                continue
            try:
                await cli.create_session(session_id=sid)
            except Exception:  # noqa: BLE001 — AlreadyExists: leftover from an interrupted run
                try:
                    await cli.delete_session(sid)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await cli.create_session(session_id=sid)
                except Exception as e:  # noqa: BLE001
                    log(f"create_session {sid}: {type(e).__name__}: {e}")
                    continue
            # `batch_add_messages` exists in the OpenViking source tree but not in the 0.3.22 wheel's
            # AsyncOpenViking facade, so fall back to the per-message call that every version has.
            batch = getattr(cli, "batch_add_messages", None)
            if batch is not None:
                await batch(session_id=sid, messages=msgs)
            else:
                for m in msgs:
                    await cli.add_message(session_id=sid, role=m["role"], content=m["content"],
                                          created_at=m.get("created_at"))
            # add_message only buffers into the live session. commit_session is what archives it and
            # runs extraction — without this the engine stores nothing and `search` can only return the
            # empty memory tree's own directory abstracts.
            try:
                await cli.commit_session(sid)
            except Exception as e:  # noqa: BLE001
                log(f"commit_session {sid}: {type(e).__name__}: {e}")

        # OpenViking consolidates asynchronously (L0/L1 abstracts, extraction). Reading before that
        # settles measures an engine mid-write, so block on its own completion signal.
        try:
            await cli.wait_processed(timeout=1800)
        except Exception as e:  # noqa: BLE001
            log(f"wait_processed: {type(e).__name__}: {e}")

        res = await cli.search(item["question"], limit=topk)

        # OpenViking's search is a *planner*: it returns URI-Trie pointers — almost always the
        # `.abstract.md` / `.overview.md` node that describes a directory — not answering content. The
        # agent using it is expected to descend from those pointers and read the leaves. Stopping at the
        # pointers would score their directory listing instead of their memory, so we descend the way an
        # agent would: pointer -> parent directory -> leaf .md files.
        blocks: list[str] = []
        seen: set[str] = set()

        async def take(uri: str) -> None:
            if not uri or uri in seen or len(blocks) >= topk:
                return
            seen.add(uri)
            try:
                body = (await cli.read(uri, limit=read_chars) or "").strip()
            except Exception:  # noqa: BLE001 — directories and pruned nodes are not readable
                return
            if body:
                blocks.append(f"[{uri}]\n{body[:read_chars]}")

        pointers: list[str] = []
        for bucket in ("memories", "resources"):
            for mc in (getattr(res, bucket, None) or []):
                u = getattr(mc, "uri", None)
                if u:
                    pointers.append(u)

        for uri in pointers:
            base = uri.rsplit("/", 1)[0] if uri.rsplit("/", 1)[-1].startswith(".") else uri
            # leaves first: the real content lives in the non-dot files under the pointed directory
            try:
                nodes = await cli.tree(base)
            except Exception:  # noqa: BLE001
                nodes = []
            leaves = [n.get("uri") for n in (nodes or [])
                      if isinstance(n, dict) and not n.get("isDir")
                      and not str(n.get("name", "")).startswith(".")]
            for leaf in leaves:
                await take(leaf)
                if len(blocks) >= topk:
                    break
            if len(blocks) >= topk:
                break

        # Only if descending found nothing real do we fall back to the abstracts, so an empty memory
        # tree is visible as an empty context instead of being papered over by folder descriptions.
        if not blocks:
            for mc in (getattr(res, "memories", None) or []):
                txt = (getattr(mc, "abstract", "") or getattr(mc, "overview", "") or "").strip()
                if txt:
                    blocks.append(f"[{getattr(mc, 'uri', '?')}]\n{txt[:read_chars]}")
                if len(blocks) >= topk:
                    break

        log(f"{qid}: {len(item['sessions'])} sessions -> {len(pointers)} pointers -> {len(blocks)} blocks")
        return "RETRIEVED CONTEXT:\n\n" + "\n\n".join(blocks) if blocks else ""
    finally:
        try:
            await cli.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    item = json.load(sys.stdin)
    # OpenViking's own config sets log.output=stdout, so anything it logs lands in the middle of the
    # context we are supposed to be returning. Hand stdout to stderr for the duration and deliver the
    # context through a file the caller names — that channel cannot be polluted.
    out_path = item.get("out_path")
    real_stdout, sys.stdout = sys.stdout, sys.stderr
    try:
        ctx = asyncio.run(build_context(item))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(3)
    finally:
        sys.stdout = real_stdout
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(ctx)
    else:
        sys.stdout.write(ctx)


if __name__ == "__main__":
    main()
