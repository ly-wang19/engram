# Engram — arXiv preprint

Source for the preprint *"Less Context, More Accuracy: A Bi-Temporal Memory Engine for LLM Agents
Where a Lean Retrieved Context Beats the Full History."*

Every number in the paper is grounded in the committed logs in [`../RESULTS.md`](../RESULTS.md) and
[`../results/`](../results/) — nothing is invented. The four figures:

1. **Architecture** (TikZ, inline) — the dual-process write/consolidate/read pipeline.
2. **Bi-temporal timeline** (TikZ, inline) — non-destructive invalidation + `supersedes` + as-of queries.
3. **Accuracy vs. tokens** (`figs/fig_acc_tokens.pdf`) — the headline: +10.4 pts at ~8× fewer tokens.
4. **Per-category bars** (`figs/fig_percat.pdf`) — engram_lean by category, bi-temporal categories highlighted.

## Files

| File | What it is |
|---|---|
| `main.tex` | the paper source (self-contained; no conference `.sty` needed) |
| `references.bib` | bibliography |
| `main.bbl` | **generated** bibliography — arXiv needs this in the upload (arXiv does not run BibTeX) |
| `figs/make_figs.py` | regenerates the two **data** figures (matplotlib → vector PDF) |
| `figs/fig_*.pdf` | the two generated data figures (the TikZ diagrams are inline in `main.tex`) |
| `compute_stats.py` | recomputes the significance tests + confidence intervals from the committed logs (no model calls) |
| `main.pdf` | the compiled **14-page** PDF (incl. appendix: prompts + qualitative examples) |
| `arxiv_abstract.txt` | the abstract as plain text, ready to paste into the arXiv metadata form |
| `arxiv-submission.tar.gz` | **the ready-to-upload bundle** (`main.tex` + `references.bib` + `main.bbl` + `figs/*.pdf`) |

## Build

```bash
export PATH="/Library/TeX/texbin:$PATH"   # macOS MacTeX; skip if pdflatex is already on PATH
cd paper
python3 figs/make_figs.py                 # (re)generate fig_acc_tokens.pdf + fig_percat.pdf
pdflatex -interaction=nonstopmode main.tex
bibtex   main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex   # -> main.pdf
```

The two TikZ diagrams compile inline; only the two data charts come from `make_figs.py`.

## Before you upload — two things only you can fill in

1. **Authors.** `main.tex` is set to `Liuyin Wang`, `Independent Researcher`, `liuyinwangthu@gmail.com`.
   If your affiliation should be **Tsinghua University** (or anything else), change that one line and rebuild.
2. **Verify the bib.** The entries in `references.bib` are real works, but double-check each venue/year
   against the canonical source before submitting.

## Submitting to arXiv (the part that must be done under your own account)

I can prepare everything above, but the final submit is yours — it creates an account-bound, legally
binding statement of authorship and originality. Step by step:

1. **Account + endorsement.** Sign in at <https://arxiv.org>. First-time CS submitters may need an
   **endorsement** (or auto-endorsement via an academic email / prior submissions). A `tsinghua.edu.cn`
   address usually auto-endorses; a gmail address usually does **not**. Sort this out early.
2. **Start a new submission**, primary category **cs.CL**; cross-list **cs.AI**, **cs.LG**, **cs.IR**.
3. **Upload `arxiv-submission.tar.gz`.** It already contains `main.tex`, `references.bib`, `main.bbl`,
   and `figs/fig_acc_tokens.pdf` + `figs/fig_percat.pdf` with the right paths. arXiv preserves the
   `figs/` folder and (because `\pdfoutput=1` is set) compiles with pdfLaTeX. ⚠️ Do **not** upload only
   `main.pdf` — arXiv wants the source.
4. **Metadata.** Paste the title and the contents of `arxiv_abstract.txt` (plain text — no LaTeX macros).
   Add a "Comments" line, e.g. `14 pages (incl. appendix), 4 figures, 3 tables`. Set authors to match `main.tex`.
5. **License.** For an open-source project, **CC BY 4.0** maximizes reuse; arXiv's default non-exclusive
   license is the more conservative choice.
6. **Preview** the arXiv-rendered PDF, confirm the figures and references look right, then **Submit**.

Tip: register/claim an **ORCID** and link it so the paper attaches to your author record.

## After arXiv → a venue

The chosen plan is arXiv first. Once it's up, the natural next targets (deadlines as of June 2026):
**AAAI 2027** (abstract Jul 21 / paper Jul 28, 2026) or an **ACL Rolling Review** cycle. Those want a
conference template and (for competitiveness) a second benchmark + a second answerer backbone — see the
"Limitations" section of the paper, which already names that as next work.
