"""Seed the Engram console with LongMemEval_S so a deployed service has rich, realistic memory to
browse and test — ONE user namespace per question (key = its number).

Why local-build-then-upload: the server uses bge-small, so we build the per-user stores HERE with the
same embedder (vectors are compatible) and just copy the .pkl files into the server's ENGRAM_DATA_DIR.
The server isn't hammered, and there's no model mismatch.

Hybrid extraction (cost vs. quality): the bulk uses the FREE offline rule extractor; a representative
subset — the first `--featured-per-cat` questions of EACH category — uses the configured LLM for
high-quality facts. Everything else (episodes, L2 summaries, timeline, graph, retrieval) is populated
either way.

Run from the repo root (so .env with the provider key is picked up):
  python eval/seed_console.py \
      --data ~/.cache/huggingface/hub/datasets--xiaowu0162--longmemeval/snapshots/*/longmemeval_s \
      --out /tmp/console_pkls --featured-per-cat 3 --llm volcano:doubao-seed-1-6-flash-250615
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone

from engram import Memory
from engram.util import DAY, fmt_date

BASE = 1_700_000_000.0


# --- LongMemEval ingest (inlined from eval/longmemeval.py to avoid importing the LLM-eval stack) ---
def sessions_of(item: dict):
    sessions = item.get("haystack_sessions", [])
    ids = item.get("haystack_session_ids") or [f"sess_{i}" for i in range(len(sessions))]
    return list(zip(ids, sessions))


def parse_date(s):
    if not s:
        return None
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    tm = re.search(r"(\d{1,2}):(\d{2})", s)
    hh, mm = (int(tm.group(1)), int(tm.group(2))) if tm else (0, 0)
    try:
        return datetime(y, mo, d, hh, mm, tzinfo=timezone.utc).timestamp(), f"{y:04d}-{mo:02d}-{d:02d}"
    except ValueError:
        return None


def ingest(mem: Memory, item: dict, user_id: str) -> None:
    """One episode per session (concatenated turns), batch-embedded in a single encode call."""
    dates = item.get("haystack_dates") or []
    texts, meta = [], []
    for idx, (sid, turns) in enumerate(sessions_of(item)):
        parsed = parse_date(dates[idx] if idx < len(dates) else None)
        event_time, dstr = parsed if parsed else (BASE + idx * DAY, None)
        text = "\n".join(f"{t.get('role', 'user')}: {t.get('content', '')}"
                         for t in turns if t.get("content"))
        if text.strip():
            texts.append(text)
            meta.append((sid, event_time, dstr))
    if not texts:
        return
    vecs = mem.embedder.embed_batch(texts)
    for (sid, event_time, dstr), text, vec in zip(meta, texts, vecs):
        ep = mem.add(text, user_id=user_id, session_id=sid, speaker="session",
                     event_time=event_time, embedding=vec)
        ep.metadata["date"] = dstr or fmt_date(event_time)


def pick_featured(items: list[dict], per_cat: int) -> set[int]:
    """First `per_cat` questions of each category — a representative spread across the 6 task types."""
    seen: dict[str, int] = {}
    featured: set[int] = set()
    for i, it in enumerate(items):
        cat = it.get("question_type", "?")
        if seen.get(cat, 0) < per_cat:
            seen[cat] = seen.get(cat, 0) + 1
            featured.add(i)
    return featured


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="path to longmemeval_s json")
    ap.add_argument("--out", default="/tmp/console_pkls")
    ap.add_argument("--embedder", default="bge-small")
    ap.add_argument("--llm", default="volcano:doubao-seed-1-6-flash-250615")
    ap.add_argument("--featured-per-cat", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--device", default=None, help="embedder device: mps | cpu | cuda (default auto)")
    ap.add_argument("--batch", type=int, default=64, help="embedder batch size")
    ap.add_argument("--shard", default=None, help="i/N — only process items where index %% N == i (parallel)")
    args = ap.parse_args()

    from engram.llm.providers import load_dotenv, make_embedder, make_llm

    load_dotenv()
    os.makedirs(args.out, exist_ok=True)

    with open(os.path.expanduser(args.data)) as fh:
        items = json.load(fh)
    if args.limit:
        items = items[: args.limit]

    shard_i, shard_n = (None, None)
    if args.shard:
        shard_i, shard_n = (int(x) for x in args.shard.split("/"))

    embedder = make_embedder(args.embedder, device=args.device, batch_size=args.batch)
    llm = make_llm(args.llm) if args.llm else None
    featured = pick_featured(items, args.featured_per_cat) if llm else set()
    print(f"{len(items)} questions | featured (LLM): {len(featured)} | offline: {len(items) - len(featured)}")

    manifest = []
    t0 = time.time()
    for i, item in enumerate(items):
        if i < args.offset:
            continue
        if shard_n is not None and i % shard_n != shard_i:
            continue
        key = str(i + 1)
        is_feat = i in featured
        mem = Memory(embedder=embedder, llm=(llm if is_feat else None))
        ingest(mem, item, user_id=key)
        try:
            mem.consolidate()
            mem.summarize_episodes(list(mem.episodes_doc.values()))
        except Exception as exc:  # noqa: BLE001 -- never let one bad item abort the whole seed
            print(f"  [{key}] consolidate degraded: {type(exc).__name__}: {exc}")
        n_live = sum(1 for f in mem.fact_store.values() if f.is_live())
        mem.save(os.path.join(args.out, f"{key}.pkl"))
        manifest.append({
            "key": key,
            "category": item.get("question_type", "?"),
            "question": item.get("question", ""),
            "answer": str(item.get("answer", "")),
            "sessions": len(sessions_of(item)),
            "facts_live": n_live,
            "extractor": "llm" if is_feat else "offline",
        })
        if (i + 1) % 10 == 0 or i + 1 == len(items):
            rate = (i + 1 - args.offset) / max(1e-6, time.time() - t0)
            print(f"  {i + 1}/{len(items)}  ({rate:.1f}/s)  last={key} facts={n_live} "
                  f"{'★llm' if is_feat else ''}")

    # When sharded, each worker writes its own slice; merge_manifest() combines them afterward.
    mfile = f"manifest.shard{shard_i}.json" if shard_n is not None else "manifest.json"
    with open(os.path.join(args.out, mfile), "w") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    if shard_n is not None:
        print(f"shard {shard_i}/{shard_n} done in {time.time() - t0:.0f}s -> {mfile} ({len(manifest)} users)")
        return
    # human-readable table
    lines = ["# Engram console seed — LongMemEval_S\n",
             f"{len(manifest)} users. Log in with the **key** (the number). ★ = LLM-extracted.\n",
             "| key | cat | facts | question | answer |", "|--:|---|--:|---|---|"]
    for m in manifest:
        star = " ★" if m["extractor"] == "llm" else ""
        q = m["question"].replace("|", "\\|")[:90]
        a = m["answer"].replace("|", "\\|").replace("\n", " ")[:60]
        lines.append(f"| {m['key']}{star} | {m['category']} | {m['facts_live']} | {q} | {a} |")
    with open(os.path.join(args.out, "manifest.md"), "w") as fh:
        fh.write("\n".join(lines))
    print(f"done in {time.time() - t0:.0f}s -> {args.out} (manifest.json + manifest.md)")


if __name__ == "__main__":
    main()
