#!/usr/bin/env python3
"""Significance tests + confidence intervals for the Engram paper, computed directly
from the committed per-question logs. No re-running of any model: this reads the same
results/*.jsonl the headline table comes from, so the statistics are reproducible.

    python paper/compute_stats.py

Outputs: per-system accuracy with Wilson 95% CIs, McNemar paired tests
(engram_lean vs full_context, and vs engram_full), and a bootstrap 95% CI for the gap.
"""
import json, math, os, random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAN = os.path.join(ROOT, "results/longmemeval_s_engram_lean_v2_final.jsonl")
OTHER = os.path.join(ROOT, "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl")


def load(path, system):
    """qid -> (correct:int, tokens, err) for one system."""
    out = {}
    for line in open(path):
        d = json.loads(line)
        info = d["sys"].get(system)
        if info is None:
            continue
        out[d["qid"]] = (1 if info["ok"] else 0, info.get("tok"), info.get("err"), d["cat"])
    return out


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (center - half, center + half)


def mcnemar_exact(b, c):
    """Two-sided exact (binomial) McNemar p-value. b,c = discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    lo = min(b, c)
    # two-sided exact binomial test against p=0.5
    p = 2 * sum(math.comb(n, i) for i in range(lo + 1)) * (0.5 ** n)
    return min(1.0, p)


def mcnemar_cc(b, c):
    """McNemar chi-square with continuity correction (df=1)."""
    if b + c == 0:
        return 0.0
    return (abs(b - c) - 1) ** 2 / (b + c)


def bootstrap_gap_ci(paired, B=20000, seed=20260605):
    rng = random.Random(seed)
    n = len(paired)
    diffs = []
    idx = range(n)
    a_vals = [a for a, _ in paired]
    b_vals = [b for _, b in paired]
    for _ in range(B):
        sa = sb = 0
        for _ in range(n):
            j = rng.randrange(n)
            sa += a_vals[j]
            sb += b_vals[j]
        diffs.append((sa - sb) / n)
    diffs.sort()
    return diffs[int(0.025 * B)], diffs[int(0.975 * B)]


lean = load(LEAN, "engram_lean")
full = load(OTHER, "full_context")
efull = load(OTHER, "engram_full")
common = sorted(set(lean) & set(full) & set(efull))
N = len(common)
print(f"paired questions present in all three systems: {N}")

def acc(d):
    ks = [d[q][0] for q in common]
    return sum(ks), len(ks)

for name, d in [("engram_lean", lean), ("full_context", full), ("engram_full", efull)]:
    k, n = acc(d)
    lo, hi = wilson(k, n)
    errs = sum(1 for q in common if d[q][2] is not None)
    toks = [d[q][1] for q in common if d[q][1] is not None]
    mtok = sum(toks) / len(toks) if toks else 0
    print(f"{name:14s} acc={k/n*100:5.1f}%  ({k}/{n})  Wilson95=[{lo*100:.1f}, {hi*100:.1f}]  "
          f"mean_tokens={mtok:7.0f}  errors={errs}")

# engram_full errored on 1 item; RESULTS.md/the paper report 83.4% = 416/499 (error-free denominator).
ek, en = acc(efull)
eerr = sum(1 for q in common if efull[q][2] is not None)
if eerr:
    print(f"  note: engram_full over its {en - eerr} error-free answers = "
          f"{ek}/{en - eerr} = {ek / (en - eerr) * 100:.1f}% (the 83.4% reported in the paper); "
          f"{ek}/{en} = {ek / en * 100:.1f}% under the strict /500 denominator.")

def discordant(a, b):
    n01 = sum(1 for q in common if a[q][0] == 0 and b[q][0] == 1)  # a wrong, b right
    n10 = sum(1 for q in common if a[q][0] == 1 and b[q][0] == 0)  # a right, b wrong
    return n10, n01

print()
for label, other in [("engram_lean vs full_context", full), ("engram_lean vs engram_full", efull)]:
    n10, n01 = discordant(lean, other)
    p = mcnemar_exact(n01, n10)
    chi = mcnemar_cc(n01, n10)
    print(f"{label}:  lean-only-right={n10}  other-only-right={n01}  "
          f"chi2_cc={chi:.2f}  exact_p={p:.3g}")

paired_lf = [(lean[q][0], full[q][0]) for q in common]
lo, hi = bootstrap_gap_ci(paired_lf)
print(f"\nbootstrap 95% CI for (engram_lean - full_context) accuracy gap: "
      f"[{lo*100:+.1f}, {hi*100:+.1f}] points (point est {(sum(a for a,_ in paired_lf)-sum(b for _,b in paired_lf))/N*100:+.1f})")

# per-category lean accuracy, RAW base category. NOTE: this folds abstention into its base
# category; the paper's 7-way figure uses eval/report.py, which splits out abstention (n=30).
print("\nper-category engram_lean (raw base category; abstention folded in -- the paper's figure"
      "\nuses eval/report.py's official 7-way split, so per-row n differs):")
cats = {}
for q in common:
    c = lean[q][3]
    cats.setdefault(c, [0, 0])
    cats[c][0] += lean[q][0]
    cats[c][1] += 1
for c in sorted(cats, key=lambda x: -cats[x][1]):
    k, n = cats[c]
    print(f"  {c:28s} {k/n*100:5.1f}%  (n={n})")

# is the raw dataset (with question text) available locally for a qualitative example?
import glob
ds = glob.glob(os.path.join(ROOT, "**/longmemeval_s*.json"), recursive=True)
print("\nlocal dataset files (for question text):", ds if ds else "none found")
