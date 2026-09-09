#!/usr/bin/env python3
"""Assert that every measured number in paper/main.tex traces to a committed results log.

The script recomputes the paper's quantities from ``results/*.jsonl`` (via ``compute_stats.py``), then
scans the main text of ``main.tex`` (from ``\\begin{document}`` to ``\\appendix``) for every percentage,
signed gap, token figure (``7.9k``), token ratio (``10$\\times$``) and latency (``53s``). A number passes
only if some recomputed quantity of the same kind rounds to it at the precision the paper prints.

Exit status 0 = every number traced and the headline/footnote invariants hold; 1 otherwise.

    python paper/check_numbers.py            # verify
    python paper/check_numbers.py --list     # also print every number it found and what it matched

Conventions the tex must follow:
  * A line that quotes arXiv-v1 (retired-judge) figures ends with the comment ``% arxiv-v1``; only such
    lines may use numbers from the v1 logs.
  * Benchmark constants that are not our measurements (e.g. the ~115k-token haystack size quoted from the
    LongMemEval paper) are listed in ``CONSTANTS`` below with a reason.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compute_stats import paper_numbers  # noqa: E402

TEX = HERE / "main.tex"
V1_MARK = "% arxiv-v1"

# Numbers in the main text that are *not* measurements of ours. Each needs a reason.
CONSTANTS = {
    "percent": {95.0: "confidence level of the Wilson / bootstrap intervals"},
    "tokens_k": {115.0: "LongMemEval_S haystack size quoted from the benchmark paper (~115k tokens)"},
    "ratio": {},
    "gap": {},
    "latency_s": {},
}

# (kind, regex). Each regex has exactly one capturing group holding the number.
PATTERNS = [
    ("percent", re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\\%")),
    ("gap", re.compile(r"\$([+-]\d+\.\d)\$")),
    ("tokens_k", re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)k(?![\w])")),
    ("ratio", re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\$?\\times")),
    ("latency_s", re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\\,s\b")),
    ("percent", re.compile(r"\[(\d+\.\d),\\,\d+\.\d\]")),   # Wilson interval, lower bound
    ("percent", re.compile(r"\[\d+\.\d,\\,(\d+\.\d)\]")),   # Wilson interval, upper bound
]


def decimals(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def quantities(nums: dict) -> tuple[dict[str, dict[float, str]], dict[str, dict[float, str]]]:
    """Two typed value->label maps: numbers from the current-judge logs, and arXiv-v1 numbers."""
    cur: dict[str, dict[float, str]] = {k: dict(v) for k, v in CONSTANTS.items()}
    v1: dict[str, dict[float, str]] = {k: {} for k in CONSTANTS}

    def add(kind: str, value: float, label: str, store=cur) -> None:
        store[kind][float(value)] = label

    hl, hf = nums["headline_engram_lean"], nums["headline_full_context"]
    pair = nums["headline_paired"]
    for s, m in (("engram_lean", hl), ("full_context", hf)):
        add("percent", m["accuracy"], f"headline {s} accuracy [{nums['headline_log']}]")
        add("tokens_k", m["avg_tokens"] / 1000, f"headline {s} avg tokens")
        add("latency_s", m["p50_s"], f"headline {s} p50 latency")
        add("latency_s", m["p95_s"], f"headline {s} p95 latency")
        for bound in pair[f"{s}_wilson"]:
            add("percent", bound, f"headline {s} Wilson bound")
    add("gap", pair["gap_points"], "headline gap")
    for bound in pair["gap_ci"]:
        add("gap", bound, "headline bootstrap CI bound")
    add("ratio", nums["headline_token_ratio"], "headline token ratio")
    for cat, r in nums["headline_percat"].items():
        add("percent", r["engram_lean"], f"headline per-category lean {cat}")
        add("percent", r["full_context"], f"headline per-category full {cat}")
        add("gap", r["engram_lean"] - r["full_context"], f"headline per-category gap {cat}")
    ab = nums["headline_abstention"]
    add("percent", ab["engram_lean"], "headline abstention (broken out) lean")
    add("percent", ab["full_context"], "headline abstention (broken out) full")
    add("gap", ab["engram_lean"] - ab["full_context"], "headline abstention gap")
    e = nums["headline_errors"]
    add("percent", e["full_errors_abstain_pct"], "error analysis: share of full_context errors that abstain")
    add("percent", e["lean_right_full_wrong_abstain_pct"], "error analysis: full_context abstain share on lean-right items")

    for name, b in nums["backbones"].items():
        add("percent", b["lean"]["accuracy"], f"backbone {name} lean [{b['log']}]")
        add("percent", b["full"]["accuracy"], f"backbone {name} full")
        add("gap", b["gap"], f"backbone {name} gap")
        add("ratio", b["token_ratio"], f"backbone {name} token ratio")
        add("tokens_k", b["lean"]["avg_tokens"] / 1000, f"backbone {name} lean tokens")
        add("tokens_k", b["full"]["avg_tokens"] / 1000, f"backbone {name} full tokens")
        for cat, r in b["percat"].items():
            add("percent", r["engram_lean"], f"backbone {name} lean {cat}")
            add("percent", r["full_context"], f"backbone {name} full {cat}")
            add("gap", r["engram_lean"] - r["full_context"], f"backbone {name} gap {cat}")

    ll, lf = nums["locomo_engram_lean"], nums["locomo_full_context"]
    for s, m in (("engram_lean", ll), ("full_context", lf)):
        add("percent", m["accuracy"], f"LOCOMO slice {s} accuracy [{nums['locomo_log']}]")
        add("tokens_k", m["avg_tokens"] / 1000, f"LOCOMO slice {s} tokens")
        add("latency_s", m["p50_s"], f"LOCOMO slice {s} p50 latency")
    add("gap", ll["accuracy"] - lf["accuracy"], "LOCOMO slice gap")
    add("ratio", nums["locomo_token_ratio"], "LOCOMO slice token ratio")
    for cat, r in nums["locomo_percat"].items():
        add("percent", r["engram_lean"], f"LOCOMO lean {cat}")
        add("percent", r["full_context"], f"LOCOMO full {cat}")
        add("gap", r["engram_lean"] - r["full_context"], f"LOCOMO gap {cat}")

    v = nums["arxiv_v1"]
    add("percent", v["lean"]["accuracy"], "arXiv v1 lean (retired judge)", v1)
    add("percent", v["full"]["accuracy"], "arXiv v1 full (retired judge)", v1)
    add("gap", v["gap"], "arXiv v1 gap", v1)
    add("ratio", v["token_ratio"], "arXiv v1 token ratio", v1)
    add("tokens_k", v["lean"]["avg_tokens"] / 1000, "arXiv v1 lean tokens", v1)
    add("tokens_k", v["full"]["avg_tokens"] / 1000, "arXiv v1 full tokens", v1)
    for x in nums["baseline_runs_retired_judge"] + nums["baseline_runs_current_judge"]:
        add("percent", x, "other full-500 full_context run cited in the judge footnote", v1)
    return cur, v1


def match(kind: str, text: str, table: dict[float, str]) -> str | None:
    target = float(text)
    d = decimals(text)
    for value, label in table.items():
        if round(value, d) == target or (d == 0 and round(value) == target):
            return label
    return None


def main_text(tex: str) -> list[tuple[int, str]]:
    lines = tex.splitlines()
    start = next(i for i, l in enumerate(lines) if "\\begin{document}" in l)
    end = next((i for i, l in enumerate(lines) if l.strip().startswith("\\appendix")), len(lines))
    return [(i + 1, l) for i, l in enumerate(lines[start:end], start)]


def strip_comment(line: str) -> str:
    out = []
    prev = ""
    for ch in line:
        if ch == "%" and prev != "\\":
            break
        out.append(ch)
        prev = ch
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", action="store_true", help="print every number found and its source")
    parser.add_argument("--tex", type=Path, default=TEX)
    parser.add_argument("--bootstrap-samples", type=int, default=20000)
    args = parser.parse_args(argv)

    nums = paper_numbers(args.bootstrap_samples)
    cur, v1 = quantities(nums)
    tex = args.tex.read_text(encoding="utf-8")
    body = main_text(tex)
    body_text = "\n".join(strip_comment(l) for _, l in body)

    failures: list[str] = []
    found = 0
    for lineno, raw in body:
        is_v1 = raw.rstrip().endswith(V1_MARK)
        line = strip_comment(raw)
        for kind, pat in PATTERNS:
            for m in pat.finditer(line):
                found += 1
                text = m.group(1)
                label = match(kind, text, cur[kind])
                if label is None and is_v1:
                    label = match(kind, text, v1[kind])
                    label = f"[arxiv-v1 line] {label}" if label else None
                if label is None:
                    failures.append(f"line {lineno}: {kind} {text!r} traces to nothing  <- {line.strip()[:90]}")
                elif args.list:
                    print(f"  ok  line {lineno:4d}  {kind:9s} {text:>6}  = {label}")

    # ---- invariants beyond "every number traces" -------------------------------------------------
    hl = nums["headline_engram_lean"]["accuracy"]
    hf = nums["headline_full_context"]["accuracy"]
    gap = nums["headline_paired"]["gap_points"]
    abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body_text, re.S)
    abstract_text = abstract.group(1) if abstract else ""
    for needle, why in (
        (f"{hl:.1f}\\%", "abstract must state the headline lean accuracy"),
        (f"{hf:.1f}\\%", "abstract must state the headline full-context accuracy"),
        (f"${gap:+.1f}$", "abstract must state the headline gap"),
    ):
        if needle not in abstract_text:
            failures.append(f"abstract: missing {needle} ({why})")

    v1lean = f"{nums['arxiv_v1']['lean']['accuracy']:.1f}"
    stale = [f"{v1lean}\\%", f"{nums['arxiv_v1']['full']['accuracy']:.1f}\\%", f"{nums['arxiv_v1']['gap']:+.1f}$"]
    for lineno, raw in body:
        if raw.rstrip().endswith(V1_MARK):
            continue
        line = strip_comment(raw)
        for s in stale:
            if s in line:
                failures.append(f"line {lineno}: stale arXiv-v1 figure {s!r} outside a '{V1_MARK}' line")

    fn = re.search(r"\\footnote\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", body_text.replace("\n", " "))
    footnotes = re.findall(r"\\footnote\{", body_text)
    judge_fn_lines = [l for _, l in body if l.rstrip().endswith(V1_MARK)]
    judge_fn = " ".join(strip_comment(l) for l in judge_fn_lines)
    if not footnotes or not judge_fn_lines:
        failures.append("no judge-migration footnote: expected a \\footnote whose lines carry the '% arxiv-v1' marker")
    else:
        for needle in ("retired", "arXiv", v1lean, f"{hl:.1f}", f"{hf:.1f}"):
            if needle not in judge_fn:
                failures.append(f"judge-migration footnote does not mention {needle!r}")
    del fn

    p_exact = nums["headline_paired"]["mcnemar_exact_p"]
    if f"$p={p_exact:.3f}$" not in body_text:
        failures.append(f"missing McNemar p-value string $p={p_exact:.3f}$")

    fl = nums["backbones"]["doubao-flash"]
    for needle, why in (
        (f"{fl['lean']['accuracy']:.1f}\\%", "second-backbone table: flash lean accuracy"),
        (f"{fl['full']['accuracy']:.1f}\\%", "second-backbone table: flash full accuracy"),
        (f"${fl['gap']:+.1f}$", "second-backbone table: flash gap"),
        (f"{fl['token_ratio']:.1f}$\\times$", "second-backbone table: flash token ratio"),
    ):
        if needle not in body_text:
            failures.append(f"missing {needle} ({why})")

    lo = nums["locomo_engram_lean"]["accuracy"]
    lf_ = nums["locomo_full_context"]["accuracy"]
    for needle, why in (
        (f"{lo:.1f}\\%", "LOCOMO slice: lean accuracy"),
        (f"{lf_:.1f}\\%", "LOCOMO slice: full accuracy"),
        ("200", "LOCOMO slice must be labelled as a 200-item slice"),
    ):
        if needle not in body_text:
            failures.append(f"missing {needle} ({why})")

    print(f"checked {found} numbers in {args.tex.name} main text against {nums['headline_log']}, "
          f"{nums['backbones']['doubao-flash']['log']}, {nums['locomo_log']}")
    if failures:
        print(f"FAIL: {len(failures)} problem(s)")
        for f in failures:
            print("  - " + f)
        return 1
    print(f"PASS: headline {hl:.1f} / {hf:.1f} / {gap:+.1f}; judge migration footnoted; every number traced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
