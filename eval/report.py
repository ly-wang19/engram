"""Honest results reporter (CLAUDE.md Bet D): from a bench.py output JSONL, print the TRIPLE —
accuracy + tokens + latency — per category and overall, for every system, next to the public SOTA.

    python eval/report.py data/star.jsonl [more.jsonl ...]

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


def _percent(values: list[bool]) -> float:
    return 100.0 * sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[idx]


def _is_synthetic_harness_log(rows: list[dict]) -> bool:
    return bool(rows) and any(r.get("type") == "summary" for r in rows) and any("naive_ok" in r for r in rows)


def format_synthetic_report(path: str, rows: list[dict]) -> str:
    item_rows = [r for r in rows if r.get("type") != "summary"]
    cats = sorted({r.get("category", "?") for r in item_rows})
    lines = [f"\nFILE: {path}   ({len(item_rows)} items + summary)\n"]
    lines.append("  Synthetic offline harness (engram / naive-recall)")
    lines.append("")
    lines.append("  " + "category".ljust(26) + "engram".ljust(16) + "naive-recall")
    lines.append("  " + "-" * 56)
    for cat in cats:
        subset = [r for r in item_rows if r.get("category", "?") == cat]
        e = _percent([bool(r.get("ok")) for r in subset])
        n = _percent([bool(r.get("naive_ok")) for r in subset])
        lines.append(f"  {cat.ljust(26)}{f'{e:.1f}% ({len(subset)})'.ljust(16)}{n:.1f}% ({len(subset)})")
    lines.append("  " + "-" * 56)
    overall = _percent([bool(r.get("ok")) for r in item_rows])
    naive_overall = _percent([bool(r.get("naive_ok")) for r in item_rows])
    overall_cell = f"{overall:.1f}% ({len(item_rows)})"
    lines.append(
        f"  {'OVERALL'.ljust(26)}"
        f"{overall_cell.ljust(16)}"
        f"{naive_overall:.1f}% ({len(item_rows)})"
    )
    avg_tokens = sum(r.get("tokens", 0) for r in item_rows) / max(1, len(item_rows))
    naive_avg_tokens = sum(r.get("naive_tokens", 0) for r in item_rows) / max(1, len(item_rows))
    latencies = [float(r.get("latency_ms", 0.0)) for r in item_rows]
    p50_latency = _percentile(latencies, 50)
    p95_latency = _percentile(latencies, 95)
    lines.append(
        f"  {'avg context tokens'.ljust(26)}{f'{avg_tokens:.1f}'.ljust(16)}{naive_avg_tokens:.1f}"
    )
    lines.append(
        f"  {'p50 latency ms'.ljust(26)}{f'{p50_latency:.2f}'.ljust(16)}-"
    )
    lines.append(
        f"  {'p95 latency ms'.ljust(26)}{f'{p95_latency:.2f}'.ljust(16)}-"
    )
    lines.append("")
    lines.append("  Note: synthetic logs are for harness-shape regression, not public benchmark claims.")
    lines.append("")
    return "\n".join(lines)


def format_bench_report(path: str, rows: list[dict]) -> str:
    if _is_synthetic_harness_log(rows):
        return format_synthetic_report(path, rows)
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
        cat = "abstention" if str(r.get("qid", "")).endswith("_abs") else r.get("cat") or r.get("pref_type", "?")
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
    cat_w = max(26, *(len(c) for c in cats), len("avg context tokens")) + 2
    w = max(14, *(len(s) for s in systems)) if systems else 14
    lines = [f"\nFILE: {path}   ({len(rows)} items written)\n"]
    header = "  " + "category".ljust(cat_w) + "".join(s.ljust(w + 2) for s in systems)
    lines.append(header)
    lines.append("  " + "-" * (cat_w + (w + 2) * len(systems)))
    for cat in cats:
        line = "  " + cat.ljust(cat_w)
        for s in systems:
            vs = acc[s].get(cat, [])
            line += (f"{100*sum(vs)/len(vs):.1f}% ({len(vs)})" if vs else "-").ljust(w + 2)
        lines.append(line)
    lines.append("  " + "-" * (cat_w + (w + 2) * len(systems)))
    line = "  " + "OVERALL".ljust(cat_w)
    overall: dict[str, float] = {}
    scored_counts: dict[str, int] = {}
    for s in systems:
        allv = [v for vs in acc[s].values() for v in vs]
        scored_counts[s] = len(allv)
        overall[s] = 100 * sum(allv) / len(allv) if allv else 0.0
        line += (f"{overall[s]:.1f}% ({len(allv)})" if allv else "-").ljust(w + 2)
    lines.append(line)
    line = "  " + "scored items".ljust(cat_w)
    for s in systems:
        line += (f"{scored_counts[s]}/{len(rows)}" if rows else "-").ljust(w + 2)
    lines.append(line)
    line = "  " + "avg context tokens".ljust(cat_w)
    for s in systems:
        line += (f"{sum(toks[s])//len(toks[s])}" if toks[s] else "-").ljust(w + 2)
    lines.append(line)
    line = "  " + "p50 latency ms".ljust(cat_w)
    for s in systems:
        if lats[s]:
            line += f"{_percentile(lats[s], 50):.0f}".ljust(w + 2)
        else:
            line += "-".ljust(w + 2)
    lines.append(line)
    line = "  " + "p95 latency ms".ljust(cat_w)
    for s in systems:
        if lats[s]:
            line += f"{_percentile(lats[s], 95):.0f}".ljust(w + 2)
        else:
            line += "-".ljust(w + 2)
    lines.append(line)
    line = "  " + "errors".ljust(cat_w)
    for s in systems:
        line += str(errs[s]).ljust(w + 2)
    lines.append(line)

    is_personamem = any("pref_type" in r for r in rows)
    if not is_personamem:
        lines.append("\n  Public LongMemEval_S SOTA (overall):")
        for name, score in SOTA.items():
            best = max(overall.values()) if overall else 0.0
            flag = ""
            if best:
                flag = "  <- WE BEAT THIS" if best >= score else f"  (gap {score - best:+.1f})"
            lines.append(f"    {name:22s} {score:.1f}{flag}")
    else:
        lines.append("\n  Note: PersonaMem-v2 is a multiple-choice personalization benchmark; LongMemEval_S SOTA rows do not apply.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python eval/report.py <bench_output.jsonl> [more.jsonl ...]")
        return
    for path in sys.argv[1:]:
        print(format_bench_report(path, load(path)))


if __name__ == "__main__":
    main()
