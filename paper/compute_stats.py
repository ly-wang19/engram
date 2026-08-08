#!/usr/bin/env python3
"""Reproducible paper statistics from committed result logs.

No model calls happen here. The script reads ``results/*.jsonl`` and recomputes the headline confidence
intervals, paired tests, bootstrap gap CI, per-category breakdowns, and the committed multi-backbone
headline table.

    python paper/compute_stats.py
    python paper/compute_stats.py --bootstrap-samples 1000
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
HEADLINE = ROOT / "results/headline_500.jsonl"
HISTORICAL_LEAN = ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl"
HISTORICAL_OTHER = ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl"
BACKBONE_RUNS = (
    ("doubao-pro", ROOT / "results/headline_500.jsonl"),
    ("doubao-flash", ROOT / "results/bb_flash.jsonl"),
)


def load(path: Path, system: str) -> dict[str, tuple[int, int | None, str | None, str, str]]:
    """qid -> (correct:int, tokens, err, category, prediction) for one system."""
    out: dict[str, tuple[int, int | None, str | None, str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        info = row["sys"].get(system)
        if info is None:
            continue
        category = "abstention" if str(row.get("qid", "")).endswith("_abs") else row.get("cat", "?")
        out[row["qid"]] = (
            1 if info["ok"] else 0,
            info.get("tok"),
            info.get("err"),
            category,
            info.get("pred", ""),
        )
    return out


def iter_rows(path: Path) -> Iterable[dict]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def system_metrics(path: Path, system: str) -> dict[str, float | int]:
    scored: list[dict] = []
    errors = 0
    for row in iter_rows(path):
        result = row.get("sys", {}).get(system)
        if not result:
            continue
        if result.get("err"):
            errors += 1
            continue
        if result.get("ok") is not None:
            scored.append(result)
    if not scored:
        return {
            "n": 0,
            "errors": errors,
            "accuracy": 0.0,
            "avg_tokens": 0.0,
            "p50_latency_ms": 0,
            "p95_latency_ms": 0,
        }
    latencies = sorted(float(r.get("lat", 0.0)) for r in scored)

    def percentile(p: float) -> int:
        idx = min(len(latencies) - 1, int(round((p / 100.0) * (len(latencies) - 1))))
        return round(latencies[idx])

    return {
        "n": len(scored),
        "errors": errors,
        "accuracy": 100.0 * sum(bool(r["ok"]) for r in scored) / len(scored),
        "avg_tokens": sum(int(r.get("tok", 0)) for r in scored) // len(scored),
        "p50_latency_ms": percentile(50),
        "p95_latency_ms": percentile(95),
    }


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (center - half, center + half)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact (binomial) McNemar p-value. b,c = discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    lo = min(b, c)
    p = 2 * sum(math.comb(n, i) for i in range(lo + 1)) * (0.5 ** n)
    return min(1.0, p)


def mcnemar_cc(b: int, c: int) -> float:
    """McNemar chi-square with continuity correction (df=1)."""
    if b + c == 0:
        return 0.0
    return (abs(b - c) - 1) ** 2 / (b + c)


def bootstrap_gap_ci(
    paired: list[tuple[int, int]],
    *,
    samples: int = 20000,
    seed: int = 20260605,
) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(paired)
    diffs: list[float] = []
    a_vals = [a for a, _ in paired]
    b_vals = [b for _, b in paired]
    for _ in range(samples):
        sa = sb = 0
        for _ in range(n):
            j = rng.randrange(n)
            sa += a_vals[j]
            sb += b_vals[j]
        diffs.append((sa - sb) / n)
    diffs.sort()
    return diffs[int(0.025 * samples)], diffs[int(0.975 * samples)]


def acc(data: dict[str, tuple[int, int | None, str | None, str, str]], qids: list[str]) -> tuple[int, int]:
    values = [data[q][0] for q in qids]
    return sum(values), len(values)


def discordant(
    a: dict[str, tuple[int, int | None, str | None, str, str]],
    b: dict[str, tuple[int, int | None, str | None, str, str]],
    qids: list[str],
) -> tuple[int, int]:
    n01 = sum(1 for q in qids if a[q][0] == 0 and b[q][0] == 1)  # a wrong, b right
    n10 = sum(1 for q in qids if a[q][0] == 1 and b[q][0] == 0)  # a right, b wrong
    return n10, n01


def print_main_stats(bootstrap_samples: int) -> None:
    lean = load(HEADLINE, "engram_lean")
    full = load(HEADLINE, "full_context")
    common = sorted(set(lean) & set(full))
    n_common = len(common)
    print(f"canonical paired questions present in both systems: {n_common}")

    for name, data in [("engram_lean", lean), ("full_context", full)]:
        k, n = acc(data, common)
        lo, hi = wilson(k, n)
        errs = sum(1 for q in common if data[q][2] is not None)
        toks = [data[q][1] for q in common if data[q][1] is not None]
        mtok = sum(toks) // len(toks) if toks else 0
        metrics = system_metrics(HEADLINE, name)
        print(
            f"{name:14s} acc={k/n*100:5.1f}%  ({k}/{n})  Wilson95=[{lo*100:.1f}, {hi*100:.1f}]  "
            f"mean_tokens={mtok:7.0f}  p50/p95_ms={metrics['p50_latency_ms']}/{metrics['p95_latency_ms']}  "
            f"errors={errs}"
        )

    print()
    n10, n01 = discordant(lean, full, common)
    p = mcnemar_exact(n01, n10)
    chi = mcnemar_cc(n01, n10)
    print(
        f"engram_lean vs full_context:  lean-only-right={n10}  other-only-right={n01}  "
        f"chi2_cc={chi:.2f}  exact_p={p:.3g}"
    )

    paired_lf = [(lean[q][0], full[q][0]) for q in common]
    lo, hi = bootstrap_gap_ci(paired_lf, samples=bootstrap_samples)
    point = (sum(a for a, _ in paired_lf) - sum(b for _, b in paired_lf)) / n_common * 100
    print(
        f"\nbootstrap 95% CI for (engram_lean - full_context) accuracy gap: "
        f"[{lo*100:+.1f}, {hi*100:+.1f}] points (point est {point:+.1f}; B={bootstrap_samples})"
    )

    print("\nper-category engram_lean (official split with abstention separated):")
    cats: dict[str, list[int]] = {}
    for q in common:
        category = lean[q][3]
        cats.setdefault(category, [0, 0])
        cats[category][0] += lean[q][0]
        cats[category][1] += 1
    for category in sorted(cats, key=lambda x: -cats[x][1]):
        k, n = cats[category]
        print(f"  {category:28s} {k/n*100:5.1f}%  (n={n})")

    ds = glob.glob(str(ROOT / "**/longmemeval_s*.json"), recursive=True)
    print("\nlocal dataset files (for question text):", ds if ds else "none found")

    markers = (
        "don't know",
        "do not know",
        "not mentioned",
        "no information",
        "cannot find",
        "not sure",
        "unknown",
        "not stated",
        "doesn't mention",
        "isn't mentioned",
        "no answer",
        "does not mention",
    )

    def abstains(pred: str) -> bool:
        p = str(pred).lower()
        return any(marker in p for marker in markers)

    fc_wrong = [q for q in common if not full[q][0]]
    fc_wrong_abstain = [q for q in fc_wrong if abstains(full[q][4])]
    lean_right_full_wrong = [q for q in common if lean[q][0] and not full[q][0]]
    lean_right_full_wrong_abstain = [q for q in lean_right_full_wrong if abstains(full[q][4])]
    print("\nerror analysis (full_context, lost-in-the-middle):")
    print(
        f"  errors: {len(fc_wrong)}/{len(common)}; abstentions despite answer in-window: "
        f"{len(fc_wrong_abstain)} ({len(fc_wrong_abstain) / max(1, len(fc_wrong)) * 100:.0f}% of errors)"
    )
    print(
        f"  where engram_lean right & full_context wrong ({len(lean_right_full_wrong)}): full_context abstained "
        f"{len(lean_right_full_wrong_abstain)} "
        f"({len(lean_right_full_wrong_abstain) / max(1, len(lean_right_full_wrong)) * 100:.0f}%), "
        f"gave wrong value {len(lean_right_full_wrong) - len(lean_right_full_wrong_abstain)}"
    )

    historical_lean = system_metrics(HISTORICAL_LEAN, "engram_lean")
    historical_full = system_metrics(HISTORICAL_OTHER, "full_context")
    print("\nhistorical independent runs (audit only; do not compute a paired gap):")
    print(
        f"  engram_lean: {float(historical_lean['accuracy']):.1f}% @ "
        f"{float(historical_lean['avg_tokens']):.0f} tokens ({HISTORICAL_LEAN.name})"
    )
    print(
        f"  full_context: {float(historical_full['accuracy']):.1f}% @ "
        f"{float(historical_full['avg_tokens']):.0f} tokens ({HISTORICAL_OTHER.name})"
    )


def print_backbone_summary() -> None:
    print("\ncommitted multi-backbone headline runs (same harness, answerer changes):")
    print("  " + "backbone".ljust(16) + "lean".rjust(10) + "full".rjust(10) + "gap".rjust(10) + "token_ratio".rjust(14))
    print("  " + "-" * 60)
    gaps: list[float] = []
    for name, path in BACKBONE_RUNS:
        lean = system_metrics(path, "engram_lean")
        full = system_metrics(path, "full_context")
        gap = float(lean["accuracy"]) - float(full["accuracy"])
        gaps.append(gap)
        ratio = float(full["avg_tokens"]) / float(lean["avg_tokens"]) if lean["avg_tokens"] else 0.0
        print(
            "  "
            + name.ljust(16)
            + f"{float(lean['accuracy']):9.1f}%"
            + f"{float(full['accuracy']):9.1f}%"
            + f"{gap:+9.1f}"
            + f"{ratio:13.1f}x"
        )
    if gaps:
        print(f"  lean-full gap range across committed backbones: {min(gaps):+.1f}..{max(gaps):+.1f} points")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recompute Engram paper statistics from committed logs.")
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=20000,
        help="bootstrap samples for the paired lean-full gap CI (default: 20000)",
    )
    args = parser.parse_args(argv)
    if args.bootstrap_samples <= 0:
        parser.error("--bootstrap-samples must be positive")
    print_main_stats(args.bootstrap_samples)
    print_backbone_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
