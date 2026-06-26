# Results-section revisions (verified findings — integrate into main.tex)

Status: these are the corrections + new evidence I verified this session. Numbers marked **[clean]** are
publication-grade; numbers marked **[prelim]** were produced with current HEAD code, which has a
context-assembly regression (see §Reproducibility) and need a clean re-run at commit `b276866` before they
go in final. Nothing here requires touching main.tex until you approve.

---

## 1. Reproducibility note — MUST add (and pin the headline)
The committed headline log `results/longmemeval_s_engram_lean_v2_final.jsonl` (lean avg **9569 tokens**,
**83.6%**) was produced at commit **`b276866`**. Commits after it (access-layer + product fixes, ~1065 lines
across the consolidate/ path) changed what the consolidation pipeline *emits*, shrinking the assembled lean
context to ~7.3k tokens (→ ~79% — a regression, not the headline). The `lean_context` signature is unchanged;
the drift is in extraction/summarization output.

**Action:** pin the paper's reproduce command to commit `b276866` (or restore the 9.6k consolidation behavior
on HEAD and re-verify). Every headline number must come from that commit's code. This is exactly the kind of
drift the paper warns about — own it in the Reproducibility Statement: "headline produced at `b276866`; logs
committed; we observed and document a later consolidation refactor that altered context size."

## 2. Headline — keep 83.6 vs 73.2 (+10.4) BUT fix the provenance + add the honest caveat
The +10.4 currently mixes **two runs**: lean 83.6 (`v2_final`) and full 73.2 (`…deepseekjudge`). For an
honest within-run number, re-run `--systems engram_lean,full_context` together at `b276866`. (My within-run
attempt at HEAD gave +3.0, but that used the **degraded 7.3k lean** — invalid; discard it.)

**Action:** report +10.4 from a single within-run log at `b276866`, not two logs. The gap is far above noise
(McNemar p<10⁻⁶) so it survives — just make it within-run.

## 3. ★ NEW evidence — multi-backbone (strengthens the thesis; add a table)
The thesis ("lean beats full") holds across answerer backbones — and wins *bigger* on a weak model, exactly
as lost-in-the-middle predicts:

| Answerer | lean | full_context | Δ (lean−full) | note |
|---|---|---|---|---|
| doubao-2.0-pro (frontier) | 83.6% | 73.2% | **+10.4** | [clean] v2_final @ b276866 |
| doubao-1.6-flash (small) | 65.2% | 53.0% | **+12.2** | [prelim] HEAD/7.3k lean — wins big even handicapped |
| deepseek-chat (2nd vendor) | — | — | — | run errored 63% (deepseek = answerer AND judge → rate-limit); redo with a non-deepseek judge |
| gpt-5.5 | — | — | — | account 402 (no balance) — blocked |
| qwen | — | — | — | ALI key "access denied" — blocked |

**Takeaway sentence (usable):** "On a small model (doubao-1.6-flash) the lean slice beats full-context by
**+12.2** points — a larger margin than on the frontier model — consistent with weak long-context readers
suffering most from distractors." **Action:** re-run flash (and a clean 2nd-vendor backbone) at `b276866` to
make the table all-[clean]; fix the blocked accounts for gpt-5.5/qwen if you want more vendor diversity.

## 4. engram_full — report as run-to-run variance, NOT a single cherry-picked value
Two committed runs of the SAME config: 83.4% (`…deepseekjudge`) and **86.0%** (`…engram_full_pro`). The paper
cites only 83.4 to argue "engram_full ≈ lean." A reviewer finds the 86.0 log → cherry-pick + it would flip the
argument. **Action:** report engram_full as **83.4–86.0 across runs (±~2.6, within run-to-run noise)** and
soften "no difference (p=0.91)" → "within run-to-run variance." This *strengthens* the reproducibility thesis.

## 5. Facts-only ablation — still future work (don't claim); P5 in the checklist makes it a result.

---

## What's needed to finalize (blocks, by owner)
- **Compute (needs your go-ahead):** clean re-runs at `b276866` — headline within-run + flash + a clean
  2nd-vendor backbone + the variance×3. ~hours; machine was unstable (load spiked to 187) — best on a freed box.
- **You:** top up gpt-5.5 (univibe), enable qwen (ALI) for fuller multi-backbone; **decide the framing**
  (system → NeurIPS D&B/TMLR; noise-floor → COLM/main); **submit** (your account).
- **Me, on your word:** run the clean set, recompute all tables from the clean logs, integrate §1–4 into
  main.tex (or draft main_v2.tex for the noise-floor framing).
