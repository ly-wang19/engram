# Engram — paper source (ICLR 2027 submission; arXiv v1 preceded it)

Source for *"Less Context, More Accuracy: A Bi-Temporal Memory Engine for LLM Agents
Where a Lean Retrieved Context Beats the Full History"* — the arXiv v1 preprint, now revised and
typeset as an ICLR 2027 submission (double-blind by default).

Every number in the paper is grounded in the committed logs in [`../RESULTS.md`](../RESULTS.md) and
[`../results/`](../results/) — nothing is invented, and `python3 check_numbers.py` asserts it: every
percentage, gap, token figure, ratio and latency in the main text must equal a value recomputed from the
named log. Headline (ICLR 2027 revision): `engram_lean` **84.4%** vs full-context **78.8%** (+5.6) at
7.9k vs 79k tokens, `results/run500_v3_main_deepseekjudge.jsonl`, DeepSeek official-API judge. The arXiv v1
figures (83.6 / 73.2 / +10.4) were graded by a since-retired endpoint and survive only in the
judge-migration footnote. The four figures:

1. **Architecture** (TikZ, inline) — the dual-process write/consolidate/read pipeline.
2. **Bi-temporal timeline** (TikZ, inline) — non-destructive invalidation + `supersedes` + as-of queries.
3. **Accuracy vs. tokens** (`figs/fig_acc_tokens.pdf`) — the headline for two answerer backbones: +5.6 pts
   at 10× fewer tokens (doubao-seed-2.0-pro), +12.2 at 11× (doubao-seed-1.6-flash).
4. **Per-category bars** (`figs/fig_percat.pdf`) — engram_lean vs full-context by category, bi-temporal
   categories highlighted.

## Files

| File | What it is |
|---|---|
| `main.tex` | the paper source, ICLR 2027 layout (`\usepackage{iclr2027/iclr2027_conference,times}`); anonymous submission build by default, camera-ready via `\def\FINAL{}` |
| `iclr2027/` | vendored ICLR 2027 style files (`iclr2027_conference.sty`, `fancyhdr.sty`, `natbib.sty`, `math_commands.tex`), unmodified |
| `texfonts.map` | font-alias fallback for TeX installs without the `helvetic` package (see Build) |
| `references.bib` | bibliography |
| `main.bbl` | **generated** bibliography — arXiv needs this in the upload (arXiv does not run BibTeX) |
| `figs/make_figs.py` | regenerates the two **data** figures (matplotlib → vector PDF) by reading the logs through `compute_stats.py` |
| `check_numbers.py` | **the number gate**: parses `main.tex` and fails if any number in the main text does not trace to a committed log (run before every commit of the paper) |
| `figs/fig_*.pdf` | the two generated data figures (the TikZ diagrams are inline in `main.tex`) |
| `compute_stats.py` | recomputes every paper number (headline, Wilson/McNemar/bootstrap, per-category, error analysis, backbone table, LOCOMO slice) from result logs (no model calls) |
| `main.pdf` | the compiled anonymous PDF: **15 pages** — main text pp. 1–9 (ends with the Conclusion on p. 9, within the 9-page limit), AI-use / reproducibility / ethics statements pp. 10–11, references p. 11, appendix pp. 12–15 |
| `arxiv_abstract.txt` | the abstract as plain text, ready to paste into the arXiv metadata form |
| `arxiv-submission.tar.gz` | the arXiv **v1** bundle (article-class source of the June 2026 preprint). Not regenerated for the ICLR revision — see "arXiv v2" below |

## Build

```bash
export PATH="/Library/TeX/texbin:$PATH"   # macOS MacTeX; skip if pdflatex is already on PATH
cd paper
python3 figs/make_figs.py                 # (re)generate fig_acc_tokens.pdf + fig_percat.pdf
python3 check_numbers.py                  # must PASS before committing main.tex
pdflatex -interaction=nonstopmode main.tex
bibtex   main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex   # -> main.pdf (anonymous submission build)
```

The two TikZ diagrams compile inline; only the two data charts come from `make_figs.py`.

**Camera-ready / named build** (prints the author block and the "Published as..." header; `\anontrue`
turns off with it, which restores the repository URL in the Conclusion and Reproducibility Statement):

```bash
pdflatex -interaction=nonstopmode -jobname=main_final "\def\FINAL{}\input{main}"
bibtex main_final
pdflatex -interaction=nonstopmode -jobname=main_final "\def\FINAL{}\input{main}"
pdflatex -interaction=nonstopmode -jobname=main_final "\def\FINAL{}\input{main}"
```

**Page budget.** ICLR 2027 allows 9 pages of main text at submission (10 at camera-ready); references,
the three statements and the appendix do not count. Check where the Conclusion ends after any edit:
`grep 'newlabel{sec:aiuse}' main.aux` prints the page the AI Use Statement starts on — it must be ≤ 10
with the Conclusion finishing on page 9. Material that was moved out of the main text to stay under the
limit lives in the appendix (measurement-integrity notes, pluggable backends, the second backbone's
per-category gaps, prompts, qualitative examples) and in the Reproducibility Statement (the exact
reproduce command).

**Two build notes for minimal TeX installs.**

- The ICLR style loads Helvetica-Bold (`phvb`) only for the grey reviewer line-number ruler. On a TeX
  Live without the `helvetic` package (e.g. the BasicTeX on the owner's machine) that font is missing;
  `texfonts.map` aliases `phvb` to `ptmb` (Times Bold metrics) and `main.tex` adds the matching pdfTeX
  map line, so the build is error-free with the ruler digits set in Times. On a complete TeX Live the
  real `phvb.tfm` is found first and the alias is never consulted.
- The style sets the running header with `fancyhdr`'s `\lhead` inside `\maketitle`'s group; the
  `fancyhdr` it bundles (v3.2) makes that global, current TeX Live's (v4) does not, so `main.tex`
  re-issues `\lhead` after `\maketitle` with the style's own wording for both builds.

For a fast statistics smoke test without paying the full bootstrap cost:

```bash
python3 compute_stats.py --bootstrap-samples 100
```

## ICLR 2027 checklist (what the submission build already satisfies)

- `\documentclass{article}` + `iclr2027_conference.sty`, `times`; `\iclrfinalcopy` off → "Under review"
  header, "Anonymous authors" block, line-number ruler.
- No author names, affiliations, acknowledgements or self-identifying URLs in the anonymous build; the
  repository is referred to as "a public repository (link withheld for double-blind review)".
- AI Use Statement (required), Reproducibility Statement and Ethics Statement after the Conclusion and
  before the references.
- Main text ≤ 9 pages; every number traced by `check_numbers.py`; the judge migration is footnoted at
  the first body mention of the headline (Introduction, contribution 2).

## Before you upload — two things only you can fill in

1. **Authors.** The named (`\def\FINAL{}`) build uses `Liuyin Wang`, `Independent Researcher`,
   `liuyinwangthu@gmail.com` from `main.tex`. If your affiliation should be **Tsinghua University** (or
   anything else), change that one line and rebuild.
2. **Verify the bib.** The entries in `references.bib` are real works, but double-check each venue/year
   against the canonical source before submitting.

## arXiv v2 (not done in this revision)

`arxiv-submission.tar.gz` is still the **v1** bundle (article class, retired-judge numbers). Re-issuing the
preprint as v2 with the ICLR-revision numbers needs a decision the layout cannot make on its own: arXiv
compiles with no command-line `\def`, so a v2 bundle would have to either ship a copy of `main.tex` with
`\iclrfinalcopy` uncommented and the `iclr2027/` style files at the top level, or go back to an article
layout. Until then, the numbers of record are the ones in `main.pdf` here and in `../RESULTS.md`.

## Submitting to arXiv (the part that must be done under your own account)

The steps below describe the v1 upload and still apply to a v2 bundle once one is produced.

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

The chosen venue is **ICLR 2027** (abstract deadline Sep 18, full paper Sep 25, 2026). Open items for the
submission are tracked in `../HANDOFF.md` (二期 list): the full 1,986-item LOCOMO run replacing the 200-item
slice (`% TODO(locomo-full)` in `main.tex`), and a re-run of the small answerer under the current judge.
