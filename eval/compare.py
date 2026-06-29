"""Cross-log comparison for the multi-backbone matrix (CLAUDE.md Bet D, M2_RUNBOOK §3).

`report.py` turns one bench log into a table. This script answers the question that needs TWO+ logs:
when the same qids were run under different backbones (or systems), where do they agree, where do they
disagree, and what's the oracle-ensemble upper bound? That complementarity is what makes a multi-backbone
matrix worth running — and what justifies (or falsifies) per-backbone routing.

Reads only what the runs actually scored; never fabricates. Partial runs are obviously partial because
every number is reported with its denominator.

    python eval/compare.py results/file_a.jsonl results/file_b.jsonl [more.jsonl ...]
    python eval/compare.py results/file_a.jsonl results/file_b.jsonl --system engram_lean
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path


def load(path: str) -> dict[str, dict]:
    """qid -> {system_name: {ok, err, tok, lat, cat, pred, gold}}; cat lives on the row, hoisted in."""
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            qid = row.get("qid")
            if not qid:
                continue
            cat = row.get("cat") or row.get("pref_type") or "?"
            entry = {"_cat": cat}
            for sys_name, res in (row.get("sys") or {}).items():
                entry[sys_name] = res
            out[qid] = entry
    return out


def _systems_in(log: dict[str, dict]) -> list[str]:
    seen: list[str] = []
    for entry in log.values():
        for k in entry:
            if not k.startswith("_") and k not in seen:
                seen.append(k)
    return seen


def _scored(entry: dict, system: str) -> bool:
    """A scored item: the system has a result and it's not an error. Abstentions count as scored (ok=False)."""
    res = entry.get(system)
    return bool(res) and not res.get("err")


def _ok(entry: dict, system: str) -> bool:
    res = entry.get(system) or {}
    return bool(res.get("ok")) and not res.get("err")


def _short(path: str, width: int = 40) -> str:
    p = Path(path).name
    return p if len(p) <= width else "…" + p[-(width - 1):]


def format_pair_compare(
    paths: list[str], system: str | None, per_category: bool
) -> str:
    logs = [load(p) for p in paths]
    # Pick the system to compare: explicit, or the only one shared across all files.
    if system is None:
        common: list[str] = []
        for sys_name in _systems_in(logs[0]):
            if all(sys_name in _systems_in(l) for l in logs[1:]):
                common.append(sys_name)
        if not common:
            return (
                "No system name is present in every file. Pass --system to pick one "
                "(e.g. engram_lean)."
            )
        if len(common) > 1:
            return (
                f"Multiple system names are shared across all files ({common}). "
                f"Pass --system to pick one."
            )
        system = common[0]
    assert system is not None

    # Restrict to qids that EVERY file scored under this system. Anything else is apples-to-oranges.
    shared = sorted(set.intersection(*[set(l.keys()) for l in logs]))
    shared = [q for q in shared if all(_scored(l[q], system) for l in logs)]
    if not shared:
        return (
            f"No qid was scored by {system!r} in ALL {len(logs)} files "
            f"(file sizes: {[len(l) for l in logs]})."
        )

    labels = [_short(p) for p in paths]
    w = max(14, *(len(l) for l in labels))
    cat_w = 32
    lines: list[str] = []
    lines.append(
        f"\nCOMPARE: system={system!r}   shared scored qids: {len(shared)}   "
        f"(file sizes: {[len(l) for l in logs]})\n"
    )

    # Per-file accuracy on the shared set, overall + per-category.
    by_file_cat_ok: dict[str, dict[str, list[bool]]] = {label: collections.defaultdict(list) for label in labels}
    for q in shared:
        for label, log in zip(labels, logs):
            cat = log[q]["_cat"]
            by_file_cat_ok[label][cat].append(_ok(log[q], system))

    lines.append("  " + "category".ljust(cat_w) + "".join(l.ljust(w + 2) for l in labels))
    lines.append("  " + "-" * (cat_w + (w + 2) * len(labels)))
    cats = sorted({c for f in by_file_cat_ok.values() for c in f})
    for cat in cats:
        row = "  " + cat.ljust(cat_w)
        for label in labels:
            vs = by_file_cat_ok[label].get(cat, [])
            row += (f"{100*sum(vs)/len(vs):.1f}% ({len(vs)})" if vs else "-").ljust(w + 2)
        lines.append(row)
    lines.append("  " + "-" * (cat_w + (w + 2) * len(labels)))
    row = "  " + "OVERALL (shared)".ljust(cat_w)
    for label in labels:
        allv = [v for vs in by_file_cat_ok[label].values() for v in vs]
        row += (f"{100*sum(allv)/len(allv):.1f}% ({len(allv)})" if allv else "-").ljust(w + 2)
    lines.append(row)
    lines.append("")

    # Disagreement breakdown (only meaningful for 2 files; for 3+ show pairwise vs file 0).
    if len(labels) == 2:
        a, b = labels
        la, lb = logs
        both_right = both_wrong = a_only = b_only = 0
        per_cat: dict[str, dict[str, int]] = collections.defaultdict(
            lambda: {"both_right": 0, "both_wrong": 0, "a_only": 0, "b_only": 0}
        )
        oracle = 0
        for q in shared:
            ao, bo = _ok(la[q], system), _ok(lb[q], system)
            cat = la[q]["_cat"]
            if ao and bo:
                both_right += 1; per_cat[cat]["both_right"] += 1
            elif not ao and not bo:
                both_wrong += 1; per_cat[cat]["both_wrong"] += 1
            elif ao:
                a_only += 1; per_cat[cat]["a_only"] += 1
            else:
                b_only += 1; per_cat[cat]["b_only"] += 1
            if ao or bo:
                oracle += 1
        lines.append(f"  disagreement ({a} vs {b}):")
        lines.append(f"    both right:  {both_right}")
        lines.append(f"    both wrong:  {both_wrong}")
        lines.append(f"    {a.split('.')[0]:<30s} only: {a_only}")
        lines.append(f"    {b.split('.')[0]:<30s} only: {b_only}")
        lines.append(
            f"    oracle (per-q max): {oracle}/{len(shared)} = {100*oracle/len(shared):.1f}%   "
            f"[{100*both_right/len(shared):.1f}% agreement, {100*(a_only+b_only)/len(shared):.1f}% disagreement]"
        )
        if per_category:
            lines.append("")
            lines.append("  per-category disagreement:")
            sub_w = 12
            lines.append("  " + "category".ljust(cat_w) + "both_right".rjust(sub_w) + "both_wrong".rjust(sub_w)
                         + "a_only".rjust(sub_w) + "b_only".rjust(sub_w) + "oracle".rjust(sub_w))
            lines.append("  " + "-" * (cat_w + sub_w * 5))
            for cat in sorted(per_cat):
                v = per_cat[cat]
                tot = sum(v.values())
                ora = v["both_right"] + v["a_only"] + v["b_only"]
                lines.append(
                    "  " + cat.ljust(cat_w)
                    + str(v["both_right"]).rjust(sub_w)
                    + str(v["both_wrong"]).rjust(sub_w)
                    + str(v["a_only"]).rjust(sub_w)
                    + str(v["b_only"]).rjust(sub_w)
                    + f"{ora}/{tot}".rjust(sub_w)
                )
        lines.append("")
    else:
        # N files: pairwise oracle (per-q any-file correct).
        oracle = sum(
            1 for q in shared if any(_ok(l[q], system) for l in logs)
        )
        lines.append(
            f"  oracle ensemble (per-q max across {len(logs)} files): "
            f"{oracle}/{len(shared)} = {100*oracle/len(shared):.1f}%"
        )
        per_file_only: dict[str, int] = {label: 0 for label in labels}
        all_wrong = 0
        for q in shared:
            oks = [_ok(l[q], system) for l in logs]
            if not any(oks):
                all_wrong += 1
                continue
            if sum(oks) == 1:
                # uniquely correct — attribute to the winning file
                for label, ok in zip(labels, oks):
                    if ok:
                        per_file_only[label] += 1
        lines.append(f"  all wrong: {all_wrong}   uniquely-correct attribution: {per_file_only}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="bench result JSONL files to compare")
    parser.add_argument(
        "--system",
        default=None,
        help="system name to compare (e.g. engram_lean). If omitted, requires exactly one shared system.",
    )
    parser.add_argument(
        "--per-category",
        action="store_true",
        help="also print the per-category disagreement breakdown (2-file mode only).",
    )
    args = parser.parse_args()
    if len(args.files) < 2:
        sys.exit("compare.py needs at least 2 files.")
    print(format_pair_compare(args.files, args.system, args.per_category))


if __name__ == "__main__":
    main()
