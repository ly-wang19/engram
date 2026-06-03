"""Honest results reporter (CLAUDE.md Bet D): from a bench.py output JSONL, print the TRIPLE —
accuracy + tokens + latency — per category and overall, for every system, next to the public SOTA.

    python eval/report.py data/star.jsonl

A number we cannot reproduce does not exist; this is the one place that turns raw run logs into the
table we'd publish. It never fabricates: it reads only what the run actually scored, and it shows the
completed-item count so partial runs are obviously partial.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

# Public LongMemEval_S leaderboard (overall), for context. These used each system's own harness/judge;
# our numbers use the OFFICIAL category judge so they're comparable to within judge/answerer differences.
SOTA = {"OMEGA": 95.4, "Mem0-2026": 94.4, "Hunyuan Hy-Memory": 85.2}


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python eval/report.py <bench_output.jsonl>")
        return
    rows = load(sys.argv[1])
    systems: list[str] = []
    for r in rows:
        for s in r.get("sys", {}):
            if s not in systems:
                systems.append(s)

    # accuracy needs a non-None ok; tokens/latency averaged over scored items only
    acc: dict[str, dict[str, list[bool]]] = {s: defaultdict(list) for s in systems}
    toks: dict[str, list[int]] = {s: [] for s in systems}
    lats: dict[str, list[float]] = {s: [] for s in systems}
    errs: dict[str, int] = {s: 0 for s in systems}
    for r in rows:
        # abstention items (qid ..._abs) are graded by the official 'unanswerable' judge — break them out
        # as their own bucket so the 'do-not-refuse' prompt tension is visible, not hidden in the category.
        cat = "abstention" if str(r.get("qid", "")).endswith("_abs") else r.get("cat", "?")
        for s in systems:
            res = r.get("sys", {}).get(s)
            if not res:
                continue
            if res.get("err"):
                errs[s] += 1
            elif res.get("ok") is not None:
                acc[s][cat].append(bool(res["ok"]))
                toks[s].append(res.get("tok", 0))
                lats[s].append(res.get("lat", 0.0))

    cats = sorted({c for s in systems for c in acc[s]})
    w = max(14, *(len(s) for s in systems)) if systems else 14
    print(f"\nFILE: {sys.argv[1]}   ({len(rows)} items written)\n")
    header = "  " + "category".ljust(26) + "".join(s.ljust(w + 2) for s in systems)
    print(header)
    print("  " + "-" * (26 + (w + 2) * len(systems)))
    for cat in cats:
        line = "  " + cat.ljust(26)
        for s in systems:
            vs = acc[s].get(cat, [])
            line += (f"{100*sum(vs)/len(vs):.1f}% ({len(vs)})" if vs else "-").ljust(w + 2)
        print(line)
    print("  " + "-" * (26 + (w + 2) * len(systems)))
    line = "  " + "OVERALL".ljust(26)
    overall: dict[str, float] = {}
    for s in systems:
        allv = [v for vs in acc[s].values() for v in vs]
        overall[s] = 100 * sum(allv) / len(allv) if allv else 0.0
        line += (f"{overall[s]:.1f}% ({len(allv)})" if allv else "-").ljust(w + 2)
    print(line)
    line = "  " + "avg context tokens".ljust(26)
    for s in systems:
        line += (f"{sum(toks[s])//len(toks[s])}" if toks[s] else "-").ljust(w + 2)
    print(line)
    line = "  " + "p50 latency ms".ljust(26)
    for s in systems:
        if lats[s]:
            srt = sorted(lats[s])
            line += f"{srt[len(srt)//2]:.0f}".ljust(w + 2)
        else:
            line += "-".ljust(w + 2)
    print(line)
    line = "  " + "errors".ljust(26)
    for s in systems:
        line += str(errs[s]).ljust(w + 2)
    print(line)

    print("\n  Public LongMemEval_S SOTA (overall):")
    for name, score in SOTA.items():
        best = max(overall.values()) if overall else 0.0
        flag = ""
        if best:
            flag = "  <- WE BEAT THIS" if best >= score else f"  (gap {score - best:+.1f})"
        print(f"    {name:22s} {score:.1f}{flag}")
    print()


if __name__ == "__main__":
    main()
