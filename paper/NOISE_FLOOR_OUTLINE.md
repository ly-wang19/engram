# Outline — the noise-floor / measurement-integrity paper (top main-track framing)

> Why this framing, not the system framing: the system ("bi-temporal memory beats full-context") is
> incremental at main track (bi-temporal ≈ Zep; full-context is a weak baseline). The **measurement**
> finding is contrarian, evidence-backed, and a recognized main-track genre (eval-pitfalls / "benchmarks
> are broken"). It turns this whole investigation's *negative* results into the contribution.
> Honest odds: a **real** main-track shot (esp. COLM / ACL-EMNLP analysis & resource tracks), still
> competitive — measurement papers are reviewer-taste-dependent. Far better than the system framing's
> near-reject.

## Working title
"The Answerer Is the Confound: Run-to-Run Variance Exceeds Memory-Retrieval Gains on LongMemEval"
(alt: "Measuring Noise: LLM Memory Leaderboards Report Differences Smaller Than Their Own Variance")

## Thesis (one sentence)
On a fixed, neutral harness, the LLM answerer's run-to-run variance (±6–10% of items flip between
identical-config runs) is **larger than the net effect of nearly every memory-retrieval intervention** we
tested — so most reported leaderboard deltas are statistically indistinguishable from noise.

## Abstract (draft)
Memory systems for LLM agents are compared on benchmark accuracy, yet the same system is reported at wildly
different scores across sources. We ask a prior question: *how large is the gain you'd need to claim, before
it exceeds measurement noise?* On LongMemEval_S, under one neutral harness with the official judge and the
full-context baseline in every table, we measure that (i) an identical memory configuration swings by **σ ≈
N points** across repeated runs (M% of items flip), driven by the answerer's stochasticity, not the memory
layer; (ii) a battery of **K standard interventions** — cross-encoder reranking, agentic/iterative
retrieval, query decomposition, self-consistency voting, cross-model ensembling, preference-schema layers,
answer re-prompting, detail re-injection — all produce net changes **within that variance band** on the full
500 questions; and (iii) cross-backbone and cross-harness differences are larger still. We argue many
published memory-system gains are not distinguishable from noise, and we contribute a protocol + open harness
(official judge, full-context baseline, committed per-question logs, **mandatory multi-run variance
reporting**) to fix it. As a positive control, we show the one effect that **does** survive the noise: a
precisely retrieved lean context beats the full-context baseline by **+10.4 points at ~8× fewer tokens**
(McNemar p<10⁻⁶, CI well outside σ) — an existence proof of what an above-noise memory result looks like.

## Contributions
1. **A variance measurement.** Quantify answerer run-to-run variance via a config × backbone × repeat matrix;
   show σ exceeds the typical reported memory gain.
2. **A controlled negative-results battery.** K interventions, each at full-500, each net ≤ σ — with logs.
   (Most papers hide these; here they are the result.)
3. **A measurement protocol + neutral harness** (official judge, full-context baseline in every table,
   committed logs, *mandatory* N≥3-run variance) — the reusable resource.
4. **A positive control above the noise**: lean > full-context (+10.4, ~8× fewer tokens), showing the bar an
   honest claim must clear; the bi-temporal hybrid system is the vehicle, not the headline.

## Section structure
1. **Introduction** — contested numbers; the prior question ("how big before it's real?"); thesis + 4 contribs.
2. **Related work** — memory systems (MemGPT, Mem0, Zep, HippoRAG, A-MEM…); eval pitfalls / leaderboard
   integrity / contamination literature; reproducibility.
3. **A neutral harness** — same answerer+judge to every system; official judge verbatim; full-context baseline
   always; committed logs; the measurement-integrity bugs we found & fixed (truncation, home-grown judge,
   full-history leak).
4. **★ How big is the noise?** — the variance matrix: headline config × {doubao, gpt-5.5, qwen} × N≥3 runs.
   Report mean ± σ, per-item flip rate, and where σ sits vs. typical reported gains. **Core section.**
5. **★ Interventions vs. the noise floor** — the battery table: each intervention, net Δ on full-500, and
   "within σ?". The point: clever memory tricks net ~0 because the floor is the answerer.
6. **The effect that survives** — lean > full-context (+10.4, 8×): the existence proof; mechanism
   (lost-in-the-middle); CI outside σ; holds across backbones. System described here as the case study.
7. **A protocol for honest memory evaluation** — concrete checklist (N≥3 runs + σ, official judge,
   full-context baseline, committed logs, no sub-σ claims, no cross-harness number lifting).
8. **Discussion / limitations / ethics** — scope; what σ does *not* excuse; recommendations.

## Experiments this framing needs (status)
| # | Experiment | For section | Status |
|---|---|---|---|
| V1 | headline config × **doubao × N≥3 runs** → σ, flip rate | §4 (core) | P0 = run 1; var2/var3 queued (P4) |
| V2 | headline × **gpt-5.5** × N≥3, **qwen** × N≥3 → cross-backbone σ | §4 | P1 gives 1 run each; need ≥3 each |
| B1 | intervention battery at full-500 (rerank, agentic, consensus, ensemble, preference, reprompt, raw_top…) | §5 | **most were slice/partial — must re-run full-500 for the table** |
| P  | lean vs full-context (+10.4) across backbones, multi-run | §6 | P0/P1 in progress |
| C  | competitors (Mem0 ✓; Zep/HippoRAG need setup) on same harness | §3/§6 (supporting) | Mem0 queued; rest need setup |

**Key shift from the running pipeline:** the noise paper needs **N≥3 repeats per (config,backbone)** for the
σ matrix, and the intervention battery **re-run at full-500** (the session's slice numbers aren't
publication-grade). Cheaper than it sounds — all API/flag-only, no new backends except the optional competitors.

## Honest risks (write these into the paper, don't hide)
- Reviewers may say "variance is known / obvious." Rebuttal: we *quantify* it on a standard memory benchmark
  and show it **exceeds the field's reported gains** — that specific, measured claim is not in the literature.
- σ itself is an estimate; report it with its own CI (enough repeats).
- The positive control (lean>full) must be shown **robustly above σ** or it undercuts the thesis — it is
  (+10.4 ≫ σ≈few points), but report the multi-run version.

## Next concrete steps (do not touch main.tex until you decide)
1. Decide: pivot to this framing? (keep the system paper as a fallback / workshop version.)
2. If yes: I draft `main_v2.tex` from this outline (reuse the system §, harness §, prompts appendix you
   already have) — your existing assets transfer ~70%.
3. Run the σ matrix (V1+V2) + the full-500 intervention battery (B1) — the two sections that make or break it.
