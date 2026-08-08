#!/usr/bin/env python3
"""Generate the two data figures for the Engram preprint as vector PDFs.

Every number here is from the committed RESULTS.md / results/ logs -- nothing invented.
Run:  python3 paper/figs/make_figs.py   (writes fig_acc_tokens.pdf, fig_percat.pdf alongside)
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))

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

# ---------------------------------------------------------------- Fig 3: accuracy vs tokens
# LongMemEval_S, 500 Q, official judge (RESULTS.md headline table).
fig, ax = plt.subplots(figsize=(5.0, 3.4))

ax.scatter([7.283], [79.0], marker="*", s=480, color=ENGRAM, zorder=6,
           edgecolor="black", linewidth=0.5, label=r"Engram (engram_lean)")
ax.scatter([79.241], [76.0], marker="s", s=80, color=BASELINE, zorder=6,
           edgecolor="black", linewidth=0.5, label="full-context baseline")

# improvement arrow: baseline -> lean (up and to the left)
ax.annotate("", xy=(10.5, 78.8), xytext=(76, 76.2),
            arrowprops=dict(arrowstyle="-|>", color="0.45", lw=1.3,
                            connectionstyle="arc3,rad=0.18"), zorder=4)
ax.text(38, 78.2, "+3.0 pts\n10.9x fewer context tokens", color="0.30", fontsize=8.5,
        ha="center", va="center", style="italic")

ax.annotate("79.0% @ 7.3k", (7.283, 79.0), textcoords="offset points",
            xytext=(10, -13), fontsize=8.5, color=ENGRAM, fontweight="bold")
ax.annotate("76.0% @ 79.2k", (79.241, 76.0), textcoords="offset points",
            xytext=(-6, -14), fontsize=8.0, color=BASELINE, ha="right")

ax.text(2, 81.4, r"$\Leftarrow$ higher accuracy estimate, fewer tokens",
        fontsize=8.0, color="0.4")

ax.set_xlabel("Avg. context tokens (thousands)")
ax.set_ylabel("LongMemEval$_S$ accuracy (%)")
ax.set_xlim(0, 90)
ax.set_ylim(72, 82)
ax.grid(True, ls=":", lw=0.6, alpha=0.6)
ax.legend(loc="lower left", fontsize=7.6, frameon=True, framealpha=0.95)
fig.tight_layout()
out1 = os.path.join(HERE, "fig_acc_tokens.pdf")
fig.savefig(out1)
print("wrote", out1)

# ---------------------------------------------------------------- Fig 4: per-category bars
# engram_lean, full 500 (RESULTS.md per-category table).
cats = ["single-session-assistant", "knowledge-update", "abstention",
        "single-session-user", "temporal-reasoning", "multi-session",
        "single-session-preference"]
vals = [100.0, 91.7, 90.0, 84.4, 70.9, 70.2, 56.7]
ns   = [56,    72,   30,   64,   127,  121,  30]
# Highlight the categories most directly related to the bi-temporal design; this is not a causal ablation.
highlight = {"knowledge-update", "temporal-reasoning"}
colors = [ENGRAM if c in highlight else ENGRAM_L for c in cats]

fig, ax = plt.subplots(figsize=(5.6, 3.2))
ypos = list(range(len(cats)))
ax.barh(ypos, vals, color=colors, edgecolor="black", linewidth=0.4, height=0.66)
ax.invert_yaxis()  # highest category on top

ax.axvline(79.0, ls="--", color=BASELINE, lw=1.2)
ax.text(79.0, -0.7, "overall 79.0%", color=BASELINE, fontsize=8, ha="center")

for y, v, n in zip(ypos, vals, ns):
    ax.text(v + 0.8, y, f"{v:.1f}%  (n={n})", va="center", fontsize=8)

ax.set_yticks(ypos)
ax.set_yticklabels(cats, fontsize=8.5)
ax.set_xlabel("Accuracy (%)")
ax.set_xlim(0, 116)
ax.set_xticks([0, 20, 40, 60, 80, 100])
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)

legend_handles = [
    Patch(facecolor=ENGRAM, edgecolor="black", lw=0.4,
          label="bi-temporal–related category"),
    Patch(facecolor=ENGRAM_L, edgecolor="black", lw=0.4, label="other category"),
]
ax.legend(handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 1.01),
          ncol=2, fontsize=7.8, frameon=False)
fig.tight_layout()
out2 = os.path.join(HERE, "fig_percat.pdf")
fig.savefig(out2)
print("wrote", out2)
