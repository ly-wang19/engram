"""Unified benchmark rig (CLAUDE.md Bet D) — the honest, non-cheating way to compare memory systems.

Every system under test is a black box exposing ONE method: context(item) -> str (ingest the question's
sessions, return the context to answer from). A FIXED answerer + FIXED judge + FIXED embedder/extractor
are applied IDENTICALLY to every system, so the only thing that varies is the memory layer itself. We run
competitors (Mem0, Zep) here ourselves rather than citing their self-reported numbers on other harnesses.

    python eval/bench.py --data s --limit 40 --systems engram,full_context,rag \
        --answerer univibe:gemini-2.5-flash --judge univibe:gpt-5.5 --extractor deepseek

Standard rig (default): answerer=gemini-2.5-flash, judge=gpt-5.5, extractor(internal)=deepseek,
embedder=bge-small. Change them, but they apply to ALL systems equally.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

# Disable chromadb/posthog telemetry — it tries to phone home, fails behind a proxy, and stalls runs.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("POSTHOG_DISABLED", "1")
# The BGE embedder's HF tokenizer is invoked from worker threads; HuggingFace tokenizers warns that
# using it after a fork risks a deadlock and disables its own parallelism. Set this explicitly so a
# long parallel run can't be killed mid-flight by that fork/deadlock path (observed: exit 144).
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Memory  # noqa: E402
from engram.llm.providers import load_dotenv, make_embedder, make_llm, make_reranker  # noqa: E402
from eval.longmemeval import (  # noqa: E402
    ANSWER_SYSTEM,
    ANSWER_TEMPLATE,
    FC_CHAR_BUDGET,
    REASONING_SYSTEM,
    all_text,
    answer_self_consistency,
    answer_two_stage_pref,
    build_persona,
    build_session_map,
    extract_answer,
    ingest,
    judge_correct,
    load_data,
    needs_self_consistency,
    needs_two_stage_pref,
    sessions_of,
)


@dataclass
class Rig:
    embedder: object
    extractor_llm: object
    answerer_llm: object
    judge_llm: object
    reranker: object = None
    topk: int = 10
    chunks: int = 5
    extract_k: int = 8
    reasoning: bool = False
    strategies: bool = False
    sc_on: bool = True
    sc_k: int = 5
    persona: bool = False
    session_map: bool = False
    summ_k: int = 25      # engram_lean: how many sessions to summarize (high-recall coverage)
    n_summaries: int = 12  # engram_lean: how many session summaries to retrieve into the lean context
    agentic: bool = False
    timeline: bool = False
    hyde: bool = False
    graph: bool = False
    wiki: bool = False
    summary: bool = False
    verify: bool = False
    intent: bool = False


# ---------------- system adapters (each: context(item) -> str) ----------------
class EngramSystem:
    name = "engram"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        rig, qid, q = self.rig, item["question_id"], item["question"]
        mem = Memory(embedder=rig.embedder, llm=rig.extractor_llm, reranker=rig.reranker)
        ingest(mem, item, qid)
        if rig.extract_k > 0:
            mem.engine.consolidate(mem.retrieve_episodes(q, qid, rig.extract_k))
        else:
            mem.consolidate()
        return mem.context_for(q, user_id=qid, top_k=rig.topk, k_chunks=rig.chunks,
                               agentic=rig.agentic, timeline=rig.timeline, hyde=rig.hyde,
                               graph=rig.graph, wiki=rig.wiki,
                               summary=rig.summary, verify=rig.verify, intent=rig.intent)


class FullContextSystem:
    name = "full_context"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        return all_text(item)[:FC_CHAR_BUDGET]


class RAGSystem:
    """Pure retrieval baseline: embed raw sessions, return top-k chunks. No extraction, no facts, no
    bi-temporal — isolates exactly what Engram's consolidation layer adds over plain RAG."""

    name = "rag"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        rig, qid, q = self.rig, item["question_id"], item["question"]
        mem = Memory(embedder=rig.embedder, reranker=rig.reranker)  # no llm -> no extraction
        ingest(mem, item, qid)
        eps = mem.retrieve_episodes(q, qid, rig.chunks)
        return "\n\n".join(f"[{e.metadata.get('date', '?')}]\n{e.content}" for e in eps)


class Mem0System:
    """Competitor: Mem0, configured with the SAME internal LLM (deepseek) + embedder as Engram, so the
    comparison is fair. Lazy import; if mem0 isn't installed the rig skips it."""

    name = "mem0"

    def __init__(self, rig: Rig):
        self.rig = rig
        from mem0 import Memory as Mem0Memory  # noqa: F401  (validated when --systems includes mem0)

        self._Mem0 = Mem0Memory
        os.environ.setdefault("OPENAI_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
        self._dk = os.environ.get("DEEPSEEK_API_KEY")

    def context(self, item: dict) -> str:
        # Fair config: SAME internal LLM (deepseek) + embedder (bge-small) as Engram. Per-question chroma
        # path = isolation + thread-safety (each question's memory is independent, as LongMemEval requires).
        qid, q = item["question_id"], item["question"]
        safe = qid.replace("/", "_")
        cfg = {
            "llm": {"provider": "openai", "config": {"model": "deepseek-chat",
                    "openai_base_url": "https://api.deepseek.com/v1", "api_key": self._dk, "temperature": 0.0}},
            "embedder": {"provider": "huggingface", "config": {"model": "BAAI/bge-small-en-v1.5"}},
            "vector_store": {"provider": "chroma", "config": {"collection_name": "mem0_lme", "path": f"data/mem0c/{safe}"}},
            # per-question SQLite history too — else the shared default db locks under parallel workers.
            "history_db_path": f"data/mem0c/{safe}/history.db",
        }
        m = self._Mem0.from_config(cfg)
        for sid, turns in sessions_of(item):
            msgs = [{"role": t.get("role", "user"), "content": t.get("content", "")} for t in turns if t.get("content")]
            if msgs:
                m.add(messages=msgs, user_id=qid)
        hits = m.search(query=q, user_id=qid, limit=self.rig.topk)
        results = hits.get("results", hits) if isinstance(hits, dict) else hits
        return "FACTS:\n" + "\n".join(f"- {r.get('memory', r)}" for r in results)


class EngramFullSystem:
    """Engram's extracted/conflict-resolved facts as a 'memory index', prepended to the full conversation
    history. Combines 100% session recall (full-context) with Engram's bi-temporal structure (facts first,
    most recent first). If this matches OMEGA-level scores, it proves the memory layer adds value on top
    of full-context. Use --systems engram_full for the strongest single-system run."""

    name = "engram_full"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        rig, qid, q = self.rig, item["question_id"], item["question"]
        mem = Memory(embedder=rig.embedder, llm=rig.extractor_llm, reranker=rig.reranker)
        ingest(mem, item, qid)
        # Extract from top-k retrieved sessions (same as EngramSystem)
        if rig.extract_k > 0:
            mem.engine.consolidate(mem.retrieve_episodes(q, qid, rig.extract_k))
        # Assemble: extracted facts (most-recent first) as the index, full history below
        from engram.util import fmt_date
        facts = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve(qid) and f.is_live()]
        facts.sort(key=lambda f: f.valid_at, reverse=True)
        fact_lines = "\n".join(f"- [{fmt_date(f.valid_at)}] {f.text}" for f in facts) or "(none extracted)"
        full_hist = all_text(item)[:FC_CHAR_BUDGET]

        # L2/L3 abstraction layers (built once over the full history with the cheap extractor LLM).
        blocks = []
        if rig.persona:  # L3: user profile — grounds preference/recommendation answers
            persona = build_persona(rig.extractor_llm, full_hist)
            if persona:
                blocks.append(f"USER PROFILE (preferences, habits, possessions):\n{persona}")
        blocks.append(f"MEMORY INDEX (extracted facts, most recent first):\n{fact_lines}")
        if rig.session_map:  # L2: complete session-by-session digest — aids multi-session aggregation
            smap = build_session_map(rig.extractor_llm, full_hist)
            if smap:
                blocks.append(f"SESSION MAP (every conversation, chronological):\n{smap}")
        blocks.append(f"FULL CONVERSATION HISTORY:\n{full_hist}")
        return "\n\n".join(blocks)


class EngramLeanSystem:
    """The scalable memory system (CLAUDE.md Bet A/E): build L1 facts + L2 session summaries + L3 persona
    during consolidation, then answer from a LEAN retrieved slice — NOT the full history. This is the real
    win condition: beat full-context on accuracy while using a fraction of the tokens, so it still works
    when the history far exceeds the model's window. Unlike engram_full, the raw haystack never enters the
    prompt; only a small, organized, retrieved context does."""

    name = "engram_lean"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        rig, qid, q = self.rig, item["question_id"], item["question"]
        mem = Memory(embedder=rig.embedder, llm=rig.extractor_llm, reranker=rig.reranker)
        ingest(mem, item, qid)
        # L1 facts from the top-k retrieved sessions; L2 summaries over a high-recall set (recall@25≈98%
        # on _S, so summarizing the top-summ_k retrieved sessions covers the evidence while staying lean).
        retrieved = mem.retrieve_episodes(q, qid, max(rig.extract_k, rig.summ_k))
        mem.consolidate_full(
            fact_episodes=retrieved[: rig.extract_k] if rig.extract_k > 0 else retrieved,
            summary_episodes=retrieved[: rig.summ_k],
        )
        return mem.lean_context(
            q, user_id=qid, n_summaries=rig.n_summaries, n_facts=rig.topk,
            n_chunks=rig.chunks, persona=rig.persona, agentic=rig.agentic,
        )


SYSTEMS = {"engram": EngramSystem, "full_context": FullContextSystem, "rag": RAGSystem,
           "mem0": Mem0System, "engram_full": EngramFullSystem, "engram_lean": EngramLeanSystem}


def eval_item(item, systems, rig):
    qid, cat, q = item["question_id"], item.get("question_type", "?"), item["question"]
    qdate = item.get("question_date", "")
    out = {"qid": qid, "cat": cat, "sys": {}}
    sys_prompt = REASONING_SYSTEM if rig.reasoning else ANSWER_SYSTEM
    for sysobj in systems:
        try:
            t0 = time.perf_counter()
            ctx = sysobj.context(item)
            # Strategy routing (rig.strategies): pick the answer method from the QUESTION TEXT (generalizes;
            # never peeks at the benchmark's category label). Counting/duration -> self-consistency vote;
            # recommendation/preference -> two-stage (surface user history, then answer grounded in it).
            if rig.strategies and rig.sc_on and rig.reasoning and needs_self_consistency(q):
                prompt = ANSWER_TEMPLATE.format(qdate=qdate, context=ctx, question=q)
                pred_raw = answer_self_consistency(rig.answerer_llm, prompt, sys_prompt, k=rig.sc_k)
            elif rig.strategies and rig.reasoning and needs_two_stage_pref(q):
                pred_raw = answer_two_stage_pref(rig.answerer_llm, ctx, q, qdate, sys_prompt)
            else:
                pred_raw = rig.answerer_llm.complete(
                    ANSWER_TEMPLATE.format(qdate=qdate, context=ctx, question=q), system=sys_prompt
                )
            # reasoning mode: judge + abstention check operate on the extracted final ANSWER line,
            # not the chain-of-thought, so reasoning text doesn't false-positive abstention markers.
            pred = extract_answer(pred_raw) if rig.reasoning else pred_raw
            lat = (time.perf_counter() - t0) * 1000.0
            ok = judge_correct(item, pred, rig.judge_llm)
            # save the (truncated) prediction + gold so a finished run can be error-analyzed without re-running
            out["sys"][sysobj.name] = {"ok": ok, "tok": len(ctx.split()), "lat": lat, "err": None,
                                       "pred": pred[:300], "gold": str(item.get("answer", ""))[:200]}
        except Exception as e:  # noqa: BLE001
            out["sys"][sysobj.name] = {"ok": None, "tok": 0, "lat": 0, "err": f"{type(e).__name__}: {str(e)[:80]}"}
    return out


def emit_item(item, systems, rig):
    """Produce each system's hypothesis (no judging — that's the official harness's job)."""
    q, qdate = item["question"], item.get("question_date", "")
    hyps = {}
    for sysobj in systems:
        try:
            ctx = sysobj.context(item)
            pred = rig.answerer_llm.complete(
                ANSWER_TEMPLATE.format(qdate=qdate, context=ctx, question=q), system=ANSWER_SYSTEM
            )
            hyps[sysobj.name] = pred.strip()
        except Exception as e:  # noqa: BLE001
            hyps[sysobj.name] = f"[ERROR {type(e).__name__}]"
    return {"qid": item["question_id"], "hyps": hyps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="s")
    ap.add_argument("--category", default=None,
                    help="filter to one question_type (e.g. knowledge-update) BEFORE --limit, to A/B a "
                         "category-specific change without paying for the whole set")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--systems", default="engram,full_context,rag")
    ap.add_argument("--answerer", default="univibe:gemini-2.5-flash")
    ap.add_argument("--judge", default="univibe:gpt-5.5")
    ap.add_argument("--extractor", default="deepseek")
    ap.add_argument("--embedder", default="bge-small")
    ap.add_argument("--reranker", default="none",
                    help="none | bge-reranker | bge-reranker-large | bge-reranker-v2 (cross-encoder over chunk pool)")
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--chunks", type=int, default=5)
    ap.add_argument("--extract-k", type=int, default=8, dest="extract_k")
    ap.add_argument("--reasoning", action="store_true",
                    help="answer with explicit EVIDENCE+REASON+ANSWER chain-of-thought (Phase 1: lifts "
                         "multi-evidence categories — temporal, multi-session, knowledge-update — which "
                         "fail because single-shot answering can't aggregate)")
    ap.add_argument("--strategies", action="store_true",
                    help="route by question text: self-consistency vote on counting/duration questions, "
                         "two-stage (surface user history -> answer) on preference questions (needs --reasoning)")
    ap.add_argument("--sc-k", type=int, default=5, dest="sc_k", help="self-consistency vote count (default 5)")
    ap.add_argument("--no-self-consistency", action="store_false", dest="sc_on",
                    help="with --strategies: keep two-stage preference but DISABLE self-consistency voting")
    ap.add_argument("--persona", action="store_true",
                    help="L3 user-profile (preferences/habits) — prepended in engram_full / engram_lean")
    ap.add_argument("--session-map", action="store_true", dest="session_map",
                    help="engram_full: insert an L2 complete session-by-session digest (multi-session aggregation)")
    ap.add_argument("--summ-k", type=int, default=25, dest="summ_k",
                    help="engram_lean: number of sessions to summarize for the L2 index (default 25)")
    ap.add_argument("--n-summaries", type=int, default=12, dest="n_summaries",
                    help="engram_lean: number of session summaries to pull into the lean context (default 12)")
    ap.add_argument("--agentic", action="store_true", help="engram: LLM-decomposed iterative retrieval (M2a)")
    ap.add_argument("--timeline", action="store_true", help="engram: prepend a chronological timeline (M2b)")
    ap.add_argument("--hyde", action="store_true", help="engram: HyDE query expansion for recall (M2c)")
    ap.add_argument("--graph", action="store_true", help="engram: entity-graph traversal (L2)")
    ap.add_argument("--wiki", action="store_true", help="engram: LLM-curated entity notes (L4)")
    ap.add_argument("--summary", action="store_true", help="engram: L5 synthesis summary")
    ap.add_argument("--verify", action="store_true", help="engram: self-verify -> re-retrieve gap")
    ap.add_argument("--intent", action="store_true", help="engram: L6 intent hint (benchmark-neutral)")
    ap.add_argument("--full", action="store_true",
                    help="engram: turn ON ALL differentiators (agentic+timeline+hyde+graph+wiki+summary+verify+intent)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--answerer-timeout", type=int, default=90, dest="answerer_timeout",
                    help="per-call timeout (s) for the answerer. Raise to ~180 for gemini-2.5-pro on the "
                         "124k-token _S full context — pro is slow and 90s times out under load.")
    ap.add_argument("--out", default=None)
    ap.add_argument("--shuffle", action="store_true",
                    help="deterministically shuffle items (seeded) so any partial run is category-balanced")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true",
                    help="skip items already present in --out and append (survives a killed run; relaunch to continue)")
    ap.add_argument("--emit", default=None,
                    help="emit {question_id,hypothesis} JSONL per system (to <emit>.<system>.jsonl) for the "
                         "OFFICIAL LongMemEval evaluate_qa.py judge — instead of self-judging here")
    args = ap.parse_args()
    if args.full:  # one switch to turn ON every engram differentiator
        args.agentic = args.timeline = args.hyde = args.graph = args.wiki = True
        args.summary = args.verify = args.intent = True

    load_dotenv()
    items = load_data(args.data)
    if args.category:
        items = [it for it in items if it.get("question_type") == args.category]
        print(f"  CATEGORY filter: {len(items)} '{args.category}' items")
    if args.limit and args.limit < len(items):
        stride = max(1, len(items) // args.limit)
        items = items[::stride][: args.limit]
    if args.shuffle:
        import random

        random.Random(args.seed).shuffle(items)  # deterministic: partials stay category-balanced
    # resume: skip items already scored in --out, so a killed run can be relaunched to continue.
    done_qids = set()
    if args.out and args.resume and os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as fh:
            for line in fh:
                try:
                    done_qids.add(json.loads(line)["qid"])
                except Exception:  # noqa: BLE001
                    pass
        n_before = len(items)
        items = [it for it in items if it["question_id"] not in done_qids]
        print(f"  RESUME: {len(done_qids)} already done; {len(items)} of {n_before} remaining")

    # reasoning mode generates EVIDENCE+REASON+ANSWER; reasoning-model backbones (doubao-seed, deepseek-r1)
    # also emit a thinking trace, so give generous headroom to avoid truncating before the ANSWER line.
    ans_max_tok = 1500 if args.reasoning else 256
    rig = Rig(
        embedder=make_embedder(args.embedder),
        extractor_llm=make_llm(args.extractor, max_tokens=512, num_retries=3, timeout=60),
        answerer_llm=make_llm(args.answerer, max_tokens=ans_max_tok, num_retries=3, timeout=args.answerer_timeout),
        judge_llm=make_llm(args.judge, max_tokens=8, num_retries=3, timeout=60),
        reranker=make_reranker(args.reranker),
        topk=args.topk, chunks=args.chunks, extract_k=args.extract_k,
        reasoning=args.reasoning, strategies=args.strategies, sc_on=args.sc_on, sc_k=args.sc_k,
        persona=args.persona, session_map=args.session_map, summ_k=args.summ_k, n_summaries=args.n_summaries,
        agentic=args.agentic, timeline=args.timeline, hyde=args.hyde, graph=args.graph, wiki=args.wiki,
        summary=args.summary, verify=args.verify, intent=args.intent,
    )

    systems = []
    for name in args.systems.split(","):
        name = name.strip()
        if not name:
            continue
        try:
            systems.append(SYSTEMS[name](rig))
        except Exception as e:  # noqa: BLE001
            print(f"  (skipping system '{name}': {type(e).__name__}: {str(e)[:80]})")
    sys_names = [s.name for s in systems]

    print(f"UNIFIED RIG | {len(items)} items from _{args.data} | systems={sys_names}")
    print(f"  answerer={args.answerer}  judge={args.judge}  extractor={args.extractor}  embedder={args.embedder}")
    print(f"  topk={args.topk} chunks={args.chunks} extract_k={args.extract_k} workers={args.workers}\n")

    if args.emit:
        files = {s: open(f"{args.emit}.{s}.jsonl", "w", encoding="utf-8") for s in sys_names}
        lock = threading.Lock()
        done = {"n": 0}

        def handle_emit(r):
            with lock:
                done["n"] += 1
                for name, hyp in r["hyps"].items():
                    files[name].write(json.dumps({"question_id": r["qid"], "hypothesis": hyp}, ensure_ascii=False) + "\n")
                    files[name].flush()
                if done["n"] % 20 == 0 or done["n"] <= 3:
                    print(f"  emitted {done['n']}/{len(items)}", flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for fut in as_completed([ex.submit(emit_item, it, systems, rig) for it in items]):
                handle_emit(fut.result())
        for f in files.values():
            f.close()
        print("\nhypotheses written:")
        for s in sys_names:
            print(f"  {s}: {args.emit}.{s}.jsonl")
        print("\nNow score with the OFFICIAL judge, e.g.:")
        print("  OPENAI_API_KEY=$UNIVIBE_API_KEY OPENAI_BASE_URL=https://api.univibe.cc/openai/v1 \\")
        print(f"  python external/LongMemEval/src/evaluation/evaluate_qa.py gpt-5.5 {args.emit}.engram.jsonl <ref.json>")
        return

    hits = {s: defaultdict(list) for s in sys_names}
    toks = {s: [] for s in sys_names}
    lats = {s: [] for s in sys_names}
    errs = {s: 0 for s in sys_names}
    lock = threading.Lock()
    outfh = open(args.out, "a" if args.resume else "w", encoding="utf-8") if args.out else None
    done = {"n": 0}

    def handle(r):
        with lock:
            done["n"] += 1
            for name, res in r["sys"].items():
                if res["err"]:
                    errs[name] += 1
                elif res["ok"] is not None:
                    hits[name][r["cat"]].append(res["ok"])
                    toks[name].append(res["tok"])
                    lats[name].append(res["lat"])
            if done["n"] <= 3 or done["n"] % 20 == 0:
                snap = {n: f"{100*sum(v for vs in hits[n].values() for v in vs)/max(1,sum(len(v) for v in hits[n].values())):.0f}%" for n in sys_names}
                print(f"  [{done['n']}/{len(items)}] {snap}", flush=True)
            if outfh:
                outfh.write(json.dumps(r, ensure_ascii=False) + "\n")
                outfh.flush()

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for fut in as_completed([ex.submit(eval_item, it, systems, rig) for it in items]):
                handle(fut.result())
    else:
        for it in items:
            handle(eval_item(it, systems, rig))
    if outfh:
        outfh.close()

    cats = sorted({c for n in sys_names for c in hits[n]})
    print("\n=== ACCURACY BY SYSTEM (same rig, same items, same answerer+judge) ===")
    header = "  " + "category".ljust(26) + "".join(n.ljust(16) for n in sys_names)
    print(header)
    for c in cats:
        row = "  " + c.ljust(26)
        for n in sys_names:
            v = hits[n][c]
            row += (f"{100*sum(v)/len(v):.1f}% ({len(v)})").ljust(16) if v else "-".ljust(16)
        print(row)
    print("  " + "-" * (26 + 16 * len(sys_names)))
    row = "  " + "OVERALL".ljust(26)
    for n in sys_names:
        flat = [v for vs in hits[n].values() for v in vs]
        row += (f"{100*sum(flat)/len(flat):.1f}%" if flat else "-").ljust(16)
    print(row)
    row = "  " + "avg context tokens".ljust(26)
    for n in sys_names:
        row += (f"{sum(toks[n])/len(toks[n]):.0f}" if toks[n] else "-").ljust(16)
    print(row)
    row = "  " + "avg latency ms".ljust(26)
    for n in sys_names:
        row += (f"{sum(lats[n])/len(lats[n]):.0f}" if lats[n] else "-").ljust(16)
    print(row)
    if any(errs.values()):
        print("  errors:", {n: errs[n] for n in sys_names if errs[n]})


if __name__ == "__main__":
    main()
