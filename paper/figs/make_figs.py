#!/usr/bin/env python3
"""Generate the two data figures for the Engram paper as vector PDFs.

Every number is read from the committed results/*.jsonl logs through paper/compute_stats.py, so the
figures cannot drift from the tables. Run:

    python3 paper/figs/make_figs.py   (writes fig_acc_tokens.pdf, fig_percat.pdf alongside)
"""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from compute_stats import paper_numbers  # noqa: E402

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.linewidth": 0.8,
    "pdf.fonttype": 42,   # embed TrueType, not Type3 (arXiv-friendly)
    "ps.fonttype": 42,
})

ENGRAM   = "#1f5c8b"   # deep blue
ENGRAM_L = "#9bb7d4"   # light blue
BASELINE = "#b0413e"   # brick red
BASE_L   = "#d9a3a1"   # light red

# The bootstrap CI is not drawn, so a small sample count keeps regeneration fast.
N = paper_numbers(bootstrap_samples=200)
pro = N["backbones"]["doubao-pro"]
flash = N["backbones"]["doubao-flash"]


def k(tokens: float) -> float:
    return tokens / 1000.0


# ---------------------------------------------------------------- Fig: accuracy vs tokens, two backbones
fig, ax = plt.subplots(figsize=(5.2, 3.6))

for name, b, star_c, sq_c, dy in (
    ("doubao-seed-2.0-pro", pro, ENGRAM, BASELINE, 5.2),
    ("doubao-seed-1.6-flash", flash, ENGRAM_L, BASE_L, 7.6),
):
    lx, ly = k(b["lean"]["avg_tokens"]), b["lean"]["accuracy"]
    fx, fy = k(b["full"]["avg_tokens"]), b["full"]["accuracy"]
    ax.scatter([lx], [ly], marker="*", s=420, color=star_c, zorder=6, edgecolor="black", linewidth=0.5)
    ax.scatter([fx], [fy], marker="s", s=75, color=sq_c, zorder=6, edgecolor="black", linewidth=0.5)
    ax.annotate("", xy=(lx + 3.0, ly - 0.4), xytext=(fx - 3.0, fy + 0.4),
                arrowprops=dict(arrowstyle="-|>", color="0.45", lw=1.2,
                                connectionstyle="arc3,rad=0.15"), zorder=4)
    ax.text((lx + fx) / 2, (ly + fy) / 2 + dy, f"{b['gap']:+.1f} pts, {b['token_ratio']:.0f}x fewer tokens",
            color="0.30", fontsize=8.0, ha="center", va="center", style="italic")
    ax.annotate(f"{ly:.1f}% @ {lx:.1f}k", (lx, ly), textcoords="offset points", xytext=(10, -12),
                fontsize=8.2, color=star_c if star_c == ENGRAM else "#5b7ea3", fontweight="bold")
    ax.annotate(f"{fy:.1f}% @ {fx:.0f}k", (fx, fy), textcoords="offset points", xytext=(-6, -13),
                fontsize=8.0, color=sq_c if sq_c == BASELINE else "#a3605e", ha="right")

ax.text(2, 88.5, r"$\Leftarrow$ better: more accurate, fewer tokens", fontsize=8.0, color="0.4")
ax.set_xlabel("Avg. context tokens (thousands)")
ax.set_ylabel("LongMemEval$_S$ accuracy (%)")
ax.set_xlim(0, 90)
ax.set_ylim(48, 91)
ax.grid(True, ls=":", lw=0.6, alpha=0.6)
handles = [
    plt.Line2D([], [], marker="*", ms=13, ls="", color=ENGRAM, mec="black", mew=0.5, label="Engram (engram_lean)"),
    plt.Line2D([], [], marker="s", ms=7, ls="", color=BASELINE, mec="black", mew=0.5, label="full-context baseline"),
    Patch(facecolor=ENGRAM, label="answerer doubao-seed-2.0-pro"),
    Patch(facecolor=ENGRAM_L, label="answerer doubao-seed-1.6-flash (small)"),
]
ax.legend(handles=handles, loc="lower left", fontsize=7.2, frameon=True, framealpha=0.95)
fig.tight_layout()
out1 = os.path.join(HERE, "fig_acc_tokens.pdf")
fig.savefig(out1)
print("wrote", out1)

# ---------------------------------------------------------------- Fig: per-category, lean vs full
pc = N["headline_percat"]
order = sorted(pc, key=lambda c: -pc[c]["engram_lean"])
lean_vals = [pc[c]["engram_lean"] for c in order]
full_vals = [pc[c]["full_context"] for c in order]
ns = [pc[c]["n"] for c in order]
highlight = {"knowledge-update", "temporal-reasoning"}  # bi-temporal categories
overall = N["headline_engram_lean"]["accuracy"]

fig, ax = plt.subplots(figsize=(6.2, 3.7))
ypos = list(range(len(order)))
h = 0.36
ax.barh([y - h / 2 for y in ypos], lean_vals, height=h,
        color=[ENGRAM if c in highlight else ENGRAM_L for c in order], edgecolor="black", linewidth=0.4)
ax.barh([y + h / 2 for y in ypos], full_vals, height=h, color=BASE_L, edgecolor="black", linewidth=0.4)
ax.invert_yaxis()
ax.axvline(overall, ls="--", color=ENGRAM, lw=1.1)
ax.text(overall + 1.0, len(order) - 0.55, f"lean overall {overall:.1f}%", color=ENGRAM, fontsize=7.8, ha="left")
for y, lv, fv, n in zip(ypos, lean_vals, full_vals, ns):
    ax.text(101, y - h / 2, f"{lv:.1f}%", va="center", fontsize=7.6, color=ENGRAM)
    ax.text(101, y + h / 2, f"{fv:.1f}%  (n={n})", va="center", fontsize=7.6, color="0.35")

ax.set_yticks(ypos)
ax.set_yticklabels(order, fontsize=8.5)
ax.set_xlabel("Accuracy (%)")
ax.set_xlim(0, 128)
ax.set_xticks([0, 20, 40, 60, 80, 100])
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
legend_handles = [
    Patch(facecolor=ENGRAM, edgecolor="black", lw=0.4, label="engram_lean, bi-temporal category"),
    Patch(facecolor=ENGRAM_L, edgecolor="black", lw=0.4, label="engram_lean, other"),
    Patch(facecolor=BASE_L, edgecolor="black", lw=0.4, label="full-context baseline"),
]
ax.legend(handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=3, fontsize=7.0, frameon=False, columnspacing=1.0, handlelength=1.2)
fig.tight_layout()
out2 = os.path.join(HERE, "fig_percat.pdf")
fig.savefig(out2)
print("wrote", out2)
