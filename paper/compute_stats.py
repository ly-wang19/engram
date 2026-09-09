#!/usr/bin/env python3
"""Reproducible paper statistics from committed result logs.

No model calls happen here. The script reads ``results/*.jsonl`` and recomputes every number the paper
cites: the headline accuracies, Wilson intervals, the paired McNemar test, a bootstrap CI for the
lean-full gap, per-category breakdowns, the error analysis, the multi-backbone table and the LOCOMO
slice. ``paper_numbers()`` returns all of them as one dict so ``check_numbers.py`` and
``figs/make_figs.py`` consume the same values the paper does.

    python paper/compute_stats.py
    python paper/compute_stats.py --bootstrap-samples 1000

Log row shape: {"qid", "cat", "sys": {"<system>": {"ok", "tok", "lat", "err", "pred", "gold"}}}.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]

# The headline run: current code, answerer doubao-seed-2.0-pro, judge = DeepSeek official API.
HEADLINE = ROOT / "results/run500_v3_main_deepseekjudge.jsonl"
# Same harness, same 500 questions; the answerer changes. The two June-27 runs (commit 4a2aa0e) were
# graded by the since-retired ARK judge on that revision's code, so each row is a within-run comparison
# and the table names the code revision and judge per row rather than pretending they match.
BACKBONE_RUNS = (
    ("doubao-pro", HEADLINE, "current main (2026-08-28)", "deepseek-chat (official API)"),
    ("doubao-pro-june", ROOT / "results/headline_500.jsonl", "4a2aa0e (2026-06-27)", "ARK deepseek-v3-2 (retired)"),
    ("doubao-flash", ROOT / "results/bb_flash.jsonl", "4a2aa0e (2026-06-27)", "ARK deepseek-v3-2 (retired)"),
)
# LOCOMO, first 200 of the 1986 QA items (same answerer/judge/extractor as the headline).
LOCOMO_SLICE = ROOT / "results/locomo200_main_deepseekjudge.jsonl"
# The arXiv v1 headline run (judge served by a since-retired third-party endpoint). Kept only so the
# judge-migration footnote can cite the v1 figures; nothing else in the paper uses it.
ARXIV_V1_LEAN = ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl"
ARXIV_V1_FULL = ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl"
# Other full-500 runs of the (unchanged) full-context baseline, cited in the footnote for the judge drift:
# two under the retired ARK judge, two under the current official-API judge before the run of record.
BASELINE_RUNS_RETIRED_JUDGE = (ARXIV_V1_FULL, ROOT / "results/headline_500.jsonl")
BASELINE_RUNS_CURRENT_JUDGE = (
    ROOT / "results/run500_semanticfloor_deepseekjudge.jsonl",
    ROOT / "results/run500_final_main_deepseekjudge.jsonl",
)

ABSTAIN_MARKERS = (
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


# --------------------------------------------------------------------------- log access

def iter_rows(path: Path) -> Iterable[dict]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def load(path: Path, system: str) -> dict[str, dict]:
    """qid -> {"ok", "tok", "lat", "err", "cat", "pred", "abs"} for one system."""
    out: dict[str, dict] = {}
    for row in iter_rows(path):
        info = row["sys"].get(system)
        if info is None:
            continue
        out[row["qid"]] = {
            "ok": 1 if info.get("ok") else 0,
            "tok": info.get("tok"),
            "lat": info.get("lat"),
            "err": info.get("err"),
            "cat": row.get("cat", "?"),
            "pred": info.get("pred", ""),
            # abstention items keep their host category in `cat`; the qid suffix marks them
            "abs": str(row.get("qid", "")).endswith("_abs"),
        }
    return out


def system_metrics(path: Path, system: str) -> dict[str, float | int]:
    data = load(path, system)
    scored = [d for d in data.values() if not d["err"]]
    errors = sum(1 for d in data.values() if d["err"])
    if not scored:
        return {"n": 0, "errors": errors, "accuracy": 0.0, "avg_tokens": 0.0, "p50_s": 0.0, "p95_s": 0.0}
    lats = sorted(float(d["lat"]) for d in scored if d["lat"] is not None)
    return {
        "n": len(scored),
        "correct": sum(d["ok"] for d in scored),
        "errors": errors,
        "accuracy": 100.0 * sum(d["ok"] for d in scored) / len(scored),
        "avg_tokens": sum(int(d["tok"] or 0) for d in scored) / len(scored),
        "p50_s": lats[len(lats) // 2] / 1000.0 if lats else 0.0,
        "p95_s": lats[int(0.95 * len(lats))] / 1000.0 if lats else 0.0,
    }


def per_category(path: Path, systems: tuple[str, ...]) -> dict[str, dict]:
    """category -> {"n", "<system>": accuracy%} using the log's own `cat` field (abstention folded in)."""
    tables = {s: load(path, s) for s in systems}
    cats: dict[str, dict] = {}
    for s, data in tables.items():
        for d in data.values():
            entry = cats.setdefault(d["cat"], {"n": 0})
            entry.setdefault(s, [0, 0])
            entry[s][0] += d["ok"]
            entry[s][1] += 1
    out: dict[str, dict] = {}
    for cat, entry in cats.items():
        row: dict = {"n": entry[systems[0]][1]}
        for s in systems:
            k, n = entry[s]
            row[s] = 100.0 * k / n
        out[cat] = row
    return out


def abstention_split(path: Path, systems: tuple[str, ...]) -> dict[str, float | int]:
    """Accuracy of each system on the `_abs` (unanswerable) items, graded by the official unanswerable judge."""
    out: dict[str, float | int] = {}
    for s in systems:
        data = [d for d in load(path, s).values() if d["abs"]]
        out["n"] = len(data)
        out[s] = 100.0 * sum(d["ok"] for d in data) / len(data) if data else 0.0
    return out


# --------------------------------------------------------------------------- statistics

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


def discordant(a: dict[str, dict], b: dict[str, dict], qids: list[str]) -> tuple[int, int]:
    n10 = sum(1 for q in qids if a[q]["ok"] == 1 and b[q]["ok"] == 0)  # a right, b wrong
    n01 = sum(1 for q in qids if a[q]["ok"] == 0 and b[q]["ok"] == 1)  # a wrong, b right
    return n10, n01


def abstains(pred: str) -> bool:
    p = str(pred).lower()
    return any(marker in p for marker in ABSTAIN_MARKERS)


# --------------------------------------------------------------------------- the paper's numbers

def paired_stats(path: Path, a: str, b: str, bootstrap_samples: int) -> dict:
    da, db = load(path, a), load(path, b)
    common = sorted(set(da) & set(db))
    ka = sum(da[q]["ok"] for q in common)
    kb = sum(db[q]["ok"] for q in common)
    n = len(common)
    n10, n01 = discordant(da, db, common)
    lo, hi = bootstrap_gap_ci([(da[q]["ok"], db[q]["ok"]) for q in common], samples=bootstrap_samples)
    wa, wb = wilson(ka, n), wilson(kb, n)
    return {
        "n": n,
        f"{a}_correct": ka,
        f"{b}_correct": kb,
        f"{a}_wilson": (100 * wa[0], 100 * wa[1]),
        f"{b}_wilson": (100 * wb[0], 100 * wb[1]),
        "a_only_right": n10,
        "b_only_right": n01,
        "mcnemar_chi2_cc": mcnemar_cc(n01, n10),
        "mcnemar_exact_p": mcnemar_exact(n01, n10),
        "gap_points": 100.0 * (ka - kb) / n,
        "gap_ci": (100 * lo, 100 * hi),
    }


def error_analysis(path: Path, lean: str = "engram_lean", full: str = "full_context") -> dict:
    dl, df = load(path, lean), load(path, full)
    common = sorted(set(dl) & set(df))
    fc_wrong = [q for q in common if not df[q]["ok"]]
    fc_wrong_abstain = [q for q in fc_wrong if abstains(df[q]["pred"])]
    lean_right_full_wrong = [q for q in common if dl[q]["ok"] and not df[q]["ok"]]
    lrfw_abstain = [q for q in lean_right_full_wrong if abstains(df[q]["pred"])]
    lean_abstain = [q for q in common if abstains(dl[q]["pred"])]
    lean_abstain_on_abs = [q for q in lean_abstain if dl[q]["abs"]]
    return {
        "n": len(common),
        "full_errors": len(fc_wrong),
        "full_errors_abstain": len(fc_wrong_abstain),
        "full_errors_abstain_pct": 100.0 * len(fc_wrong_abstain) / max(1, len(fc_wrong)),
        "lean_right_full_wrong": len(lean_right_full_wrong),
        "lean_right_full_wrong_abstain": len(lrfw_abstain),
        "lean_right_full_wrong_abstain_pct": 100.0 * len(lrfw_abstain) / max(1, len(lean_right_full_wrong)),
        "lean_right_full_wrong_value": len(lean_right_full_wrong) - len(lrfw_abstain),
        "lean_abstain": len(lean_abstain),
        "lean_abstain_on_unanswerable": len(lean_abstain_on_abs),
    }


def paper_numbers(bootstrap_samples: int = 20000) -> dict:
    """Every number the paper cites, keyed by name, computed from the committed logs."""
    systems = ("engram_lean", "full_context")
    out: dict = {"headline_log": str(HEADLINE.relative_to(ROOT))}
    for s in systems:
        out[f"headline_{s}"] = system_metrics(HEADLINE, s)
    out["headline_paired"] = paired_stats(HEADLINE, "engram_lean", "full_context", bootstrap_samples)
    out["headline_token_ratio"] = (
        out["headline_full_context"]["avg_tokens"] / out["headline_engram_lean"]["avg_tokens"]
    )
    out["headline_percat"] = per_category(HEADLINE, systems)
    out["headline_abstention"] = abstention_split(HEADLINE, systems)
    out["headline_errors"] = error_analysis(HEADLINE)

    backbones: dict[str, dict] = {}
    for name, path, code, judge in BACKBONE_RUNS:
        lean = system_metrics(path, "engram_lean")
        full = system_metrics(path, "full_context")
        backbones[name] = {
            "log": str(path.relative_to(ROOT)),
            "code": code,
            "judge": judge,
            "lean": lean,
            "full": full,
            "gap": float(lean["accuracy"]) - float(full["accuracy"]),
            "token_ratio": float(full["avg_tokens"]) / float(lean["avg_tokens"]) if lean["avg_tokens"] else 0.0,
            "percat": per_category(path, systems),
        }
    out["backbones"] = backbones

    out["locomo_log"] = str(LOCOMO_SLICE.relative_to(ROOT))
    for s in systems:
        out[f"locomo_{s}"] = system_metrics(LOCOMO_SLICE, s)
    out["locomo_token_ratio"] = out["locomo_full_context"]["avg_tokens"] / out["locomo_engram_lean"]["avg_tokens"]
    out["locomo_percat"] = per_category(LOCOMO_SLICE, systems)

    out["arxiv_v1"] = {
        "lean": system_metrics(ARXIV_V1_LEAN, "engram_lean"),
        "full": system_metrics(ARXIV_V1_FULL, "full_context"),
    }
    out["arxiv_v1"]["gap"] = out["arxiv_v1"]["lean"]["accuracy"] - out["arxiv_v1"]["full"]["accuracy"]
    out["arxiv_v1"]["token_ratio"] = out["arxiv_v1"]["full"]["avg_tokens"] / out["arxiv_v1"]["lean"]["avg_tokens"]
    out["baseline_runs_retired_judge"] = [system_metrics(p, "full_context")["accuracy"] for p in BASELINE_RUNS_RETIRED_JUDGE]
    out["baseline_runs_current_judge"] = [system_metrics(p, "full_context")["accuracy"] for p in BASELINE_RUNS_CURRENT_JUDGE]
    return out


# --------------------------------------------------------------------------- report

def print_report(nums: dict) -> None:
    lean, full, pair = nums["headline_engram_lean"], nums["headline_full_context"], nums["headline_paired"]
    print(f"headline log: {nums['headline_log']}  (paired questions: {pair['n']})")
    for name, m, w in (("engram_lean", lean, pair["engram_lean_wilson"]), ("full_context", full, pair["full_context_wilson"])):
        print(
            f"{name:14s} acc={m['accuracy']:5.1f}%  ({m['correct']}/{m['n']})  Wilson95=[{w[0]:.1f}, {w[1]:.1f}]  "
            f"mean_tokens={m['avg_tokens']:7.0f}  p50={m['p50_s']:.1f}s  p95={m['p95_s']:.1f}s  errors={m['errors']}"
        )
    print(f"token ratio full/lean = {nums['headline_token_ratio']:.1f}x")
    print(
        f"engram_lean vs full_context:  lean-only-right={pair['a_only_right']}  full-only-right={pair['b_only_right']}  "
        f"chi2_cc={pair['mcnemar_chi2_cc']:.2f}  exact_p={pair['mcnemar_exact_p']:.3g}"
    )
    lo, hi = pair["gap_ci"]
    print(f"bootstrap 95% CI for (engram_lean - full_context) gap: [{lo:+.1f}, {hi:+.1f}] points (point est {pair['gap_points']:+.1f})")

    print("\nper-category (log `cat`; the 30 unanswerable items stay in their host categories):")
    print(f"  {'category':28s}{'lean':>8}{'full':>8}{'gap':>8}{'n':>6}")
    pc = nums["headline_percat"]
    for cat in sorted(pc, key=lambda c: -pc[c]["n"]):
        r = pc[cat]
        print(f"  {cat:28s}{r['engram_lean']:7.1f}%{r['full_context']:7.1f}%{r['engram_lean'] - r['full_context']:+7.1f} {r['n']:5d}")
    ab = nums["headline_abstention"]
    print(f"  {'abstention (broken out)':28s}{ab['engram_lean']:7.1f}%{ab['full_context']:7.1f}%{ab['engram_lean'] - ab['full_context']:+7.1f} {ab['n']:5d}")

    e = nums["headline_errors"]
    print("\nerror analysis (full_context, lost-in-the-middle):")
    print(
        f"  full_context errors: {e['full_errors']}/{e['n']}; abstentions despite answer in-window: "
        f"{e['full_errors_abstain']} ({e['full_errors_abstain_pct']:.0f}% of errors)"
    )
    print(
        f"  lean right & full wrong ({e['lean_right_full_wrong']}): full abstained {e['lean_right_full_wrong_abstain']} "
        f"({e['lean_right_full_wrong_abstain_pct']:.0f}%), gave wrong value {e['lean_right_full_wrong_value']}"
    )
    print(f"  engram_lean abstains on {e['lean_abstain']}/{e['n']}, {e['lean_abstain_on_unanswerable']} of them on genuinely unanswerable items")

    print("\ncommitted multi-backbone headline runs (same harness and 500 questions; each row is within-run):")
    print("  " + "backbone".ljust(16) + "lean".rjust(10) + "full".rjust(10) + "gap".rjust(10) + "token_ratio".rjust(14) + "  code / judge / log")
    print("  " + "-" * 60)
    for name, b in nums["backbones"].items():
        print(
            "  " + name.ljust(16) + f"{b['lean']['accuracy']:9.1f}%" + f"{b['full']['accuracy']:9.1f}%"
            + f"{b['gap']:+9.1f}" + f"{b['token_ratio']:13.1f}x" + f"  {b['code']} / {b['judge']} / {b['log']}"
        )
    gaps = [b["gap"] for b in nums["backbones"].values()]
    print(f"  lean-full gap range across committed backbones: {min(gaps):+.1f}..{max(gaps):+.1f} points")
    flash = nums["backbones"]["doubao-flash"]["percat"]
    print("  doubao-flash per-category gap: " + ", ".join(
        f"{c} {flash[c]['engram_lean'] - flash[c]['full_context']:+.1f}" for c in sorted(flash, key=lambda c: -flash[c]['n'])))

    print(f"\nLOCOMO 200-item slice ({nums['locomo_log']}):")
    ll, lf = nums["locomo_engram_lean"], nums["locomo_full_context"]
    print(
        f"  engram_lean {ll['accuracy']:.1f}% ({ll['correct']}/{ll['n']}, {ll['avg_tokens']:.0f} tok, p50 {ll['p50_s']:.1f}s)  "
        f"full_context {lf['accuracy']:.1f}% ({lf['correct']}/{lf['n']}, {lf['avg_tokens']:.0f} tok, p50 {lf['p50_s']:.1f}s)  "
        f"ratio {nums['locomo_token_ratio']:.1f}x  errors {ll['errors']}/{lf['errors']}"
    )
    lp = nums["locomo_percat"]
    for cat in sorted(lp, key=lambda c: -lp[c]["n"]):
        r = lp[cat]
        print(f"  {cat:28s}{r['engram_lean']:7.1f}%{r['full_context']:7.1f}%{r['engram_lean'] - r['full_context']:+7.1f} {r['n']:5d}")

    v1 = nums["arxiv_v1"]
    print(
        f"\narXiv v1 (retired judge): lean {v1['lean']['accuracy']:.1f}% vs full {v1['full']['accuracy']:.1f}% "
        f"({v1['gap']:+.1f}), tokens {v1['lean']['avg_tokens']:.0f} vs {v1['full']['avg_tokens']:.0f} ({v1['token_ratio']:.1f}x)"
    )
    print("full_context baseline across full-500 runs -- retired judge: "
          + " / ".join(f"{x:.1f}" for x in nums["baseline_runs_retired_judge"])
          + "; current judge (before the run of record): "
          + " / ".join(f"{x:.1f}" for x in nums["baseline_runs_current_judge"]))


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
    print_report(paper_numbers(args.bootstrap_samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
