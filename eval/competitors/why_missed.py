"""Dump the exact `engram_lean` context for one question, next to the gold evidence, and say whether
the evidence made it in.

Motivation: the neutral scoreboard turned up questions a *competitor* answers and Engram abstains on.
Those are the sharpest possible signal — the evidence is provably reachable, so an "I don't know" is our
retrieval, not a hard question. `diag_facts.py` compares engram-vs-rag contexts; this one reconstructs
the shipped lean read path (the headline system) and checks evidence containment directly.

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python eval/competitors/why_missed.py e47becba
    ... --extractor volcano:doubao-seed-1-6-flash-250615 --data s
"""
from __future__ import annotations

import argparse
import os
import re
import sys

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engram.llm.providers import load_dotenv, make_embedder, make_llm  # noqa: E402
from eval.bench import Rig, SYSTEMS  # noqa: E402
from eval.longmemeval import load_data  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qids", nargs="+")
    ap.add_argument("--data", default="s")
    ap.add_argument("--system", default="engram_lean")
    ap.add_argument("--extractor", default="volcano:doubao-seed-1-6-flash-250615")
    ap.add_argument("--embedder", default="bge-small")
    ap.add_argument("--topk", type=int, default=15)
    ap.add_argument("--chunks", type=int, default=2)
    ap.add_argument("--extract-k", type=int, default=8, dest="extract_k")
    ap.add_argument("--summ-k", type=int, default=28, dest="summ_k")
    ap.add_argument("--n-summaries", type=int, default=28, dest="n_summaries")
    ap.add_argument("--show-context", action="store_true", help="print the whole context, not just the verdict")
    a = ap.parse_args()

    load_dotenv()
    rig = Rig(
        embedder=make_embedder(a.embedder),
        extractor_llm=make_llm(a.extractor),
        answerer_llm=None, judge_llm=None,
        topk=a.topk, chunks=a.chunks, extract_k=a.extract_k,
        reasoning=True, persona=True, summ_k=a.summ_k, n_summaries=a.n_summaries,
    )
    sysobj = SYSTEMS[a.system](rig)

    items = {str(i["question_id"]): i for i in load_data(a.data)}
    for qid in a.qids:
        it = items.get(qid)
        if it is None:
            print(f"[{qid}] not in _{a.data}")
            continue
        gold = str(it.get("answer", ""))
        ctx = sysobj.context(it)

        # Did the gold string survive into the context at all? And did the evidence SESSION survive?
        ids = it.get("haystack_session_ids") or []
        ev_ids = it.get("answer_session_ids") or []
        ev_text = ""
        for sid in ev_ids:
            if sid in ids:
                turns = it["haystack_sessions"][ids.index(sid)]
                ev_text += "\n".join(t.get("content", "") for t in turns)

        low = ctx.lower()
        gold_in = gold.lower() in low
        # a looser check: do most gold content words appear? catches paraphrased-but-present evidence
        words = [w for w in re.findall(r"[a-z]{4,}", gold.lower())]
        word_hits = sum(1 for w in words if w in low)

        print(f"\n===== {qid} [{it.get('question_type')}] =====")
        print(f"Q     : {it['question']}")
        print(f"GOLD  : {gold}")
        print(f"ctx   : {len(ctx.split())} words, {len(ctx)} chars")
        print(f"gold string verbatim in context : {gold_in}")
        print(f"gold content words in context   : {word_hits}/{len(words)}")
        if ev_text:
            # find the gold sentence in the evidence session, then look for it in the context
            sent = next((s for s in re.split(r"(?<=[.!?])\s+", ev_text)
                         if gold.lower() in s.lower()), "")
            if sent:
                print(f"evidence sentence               : {sent.strip()[:220]}")
                print(f"that sentence in context        : {sent.strip().lower()[:60] in low}")
        if a.show_context:
            print("-" * 70)
            print(ctx)


if __name__ == "__main__":
    main()
