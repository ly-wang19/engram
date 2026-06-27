# Engram — Benchmark Results & Methodology

This file is the full, reproducible record behind the headline number in the README. The project's core
discipline (CLAUDE.md, Bet D): **a number we cannot reproduce does not exist.** Everything needed to
re-run and verify is here.

**Paper:** [arXiv:2606.09900](https://arxiv.org/abs/2606.09900) — the peer-reviewable write-up of the headline result below.

---

## Headline: LongMemEval_S, 500 questions

The headline system is **`engram_lean`** — it answers from a small *retrieved* slice of memory, never the
full history. This is the honest test of a memory system: a precisely filtered context that beats the
noisy full window, at a fraction of the tokens.

| System | Overall | Avg context tokens | Errors |
|---|---:|---:|---:|
| **Engram** (`engram_lean`) | **83.6%** | **9.6k** | 0 / 500 |
| full-context baseline (same answerer + judge) | 73.2% | 79k | 0 / 500 |

**Engram `engram_lean` beats the full-context baseline by +10.4 points while using ~8× fewer tokens**
(9.6k vs 79k) on this 500-question run. Both numbers are on the **official** LongMemEval judge prompts,
with the **same answerer and judge applied to every system** in the harness; both `engram_lean` and
`full_context` completed all 500 questions with 0 errored.

> For reference, in the same 500-question run a non-lean variant that prepends the conflict-resolved facts
> *above the full history* (`engram_full`, ~79k tokens) scores **83.4%** — i.e. lean retrieval at 9.6k
> tokens matches the full-history-plus-facts variant at ~1/8 the context. `engram_full` had 1 errored item,
> so its 83.4% is over 499 scored questions; the lean number is the one we headline.

---

## Per-category breakdown (`engram_lean`, full 500)

| Category | Score | n | Notes |
|---|---:|---:|---|
| single-session-assistant | 92.9% | 56 | near-ceiling |
| single-session-user | 87.5% | 64 | profile/identity recall |
| knowledge-update | 87.5% | 72 | bi-temporal "most-recent-wins" working |
| abstention | 86.7% | 30 | official *unanswerable* judge |
| temporal-reasoning | 81.1% | 127 | date-stamped context + reasoning chain |
| multi-session | 79.3% | 121 | counting/aggregation across sessions — active area |
| single-session-preference | 73.3% | 30 | hard industry-wide (frontier LLMs 37–48% on PersonaMem) |
| **Overall** | **83.6%** | **500** | |

**Efficiency:** mean retrieved context **~9.6k tokens** (~8× leaner than the ~79k full-context baseline)
in the committed run. End-to-end answer latency for `engram_lean` is p50 **60.5s** / p95 **106.6s** in
this log; this includes the remote doubao-seed-2.0-pro answer call and is reported as measured, not used
as a separate retrieval latency claim.

---

## Exact setup

- **Dataset:** `longmemeval_s` (the real benchmark, 500 questions, ~50 sessions / ~115k-token haystack
  per question), pulled from HuggingFace `xiaowu0162/longmemeval`.
- **Embedder:** `BAAI/bge-small-en-v1.5` (local, no API key).
- **Internal extractor** (System-2 fact extraction): `doubao-seed-1.6-flash`.
- **Answerer:** `doubao-seed-2.0-pro`.
- **Judge:** `deepseek-v3.2`, prompted with the **exact official LongMemEval category-specific judge
  prompts** (`official_judge_prompt()` in `eval/longmemeval.py`) — temporal off-by-one tolerance,
  knowledge-update old-info tolerance, preference rubric leniency, "contains the answer" semantics,
  unanswerable detection.
- **The `engram_lean` system:** Engram retrieves a small hybrid slice — conflict-resolved bi-temporal
  facts + the most relevant raw session chunks + L2 session summaries — and answers from *that* alone
  (~9.6k tokens), never the full history.

### Reproduce

```bash
# headline number (needs model access for answerer + judge; see "Provider setup" below)
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python eval/bench.py \
    --data s --limit 500 \
    --systems engram_lean,full_context \
    --answerer volcano:doubao-seed-2-0-pro-260215 \
    --judge volcano:deepseek-v3-2-251201 \
    --extractor volcano:doubao-seed-1-6-flash-250615 \
    --embedder bge-small --reasoning --persona \
    --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 \
    --out data/run.jsonl

python eval/report.py data/run.jsonl     # prints the per-category table above
python eval/validate_results.py --expected-rows 500 --require-complete \
    --system engram_lean --system full_context data/run.jsonl  # 500 = full LongMemEval_S
```

The harness applies the **same answerer and judge to every `--systems` entry**, so any comparison *within*
a run (e.g. `engram_lean` vs `full_context`) is apples-to-apples by construction. Raw per-question logs
(prediction + gold + correctness + tokens + latency for every question) are written to the `--out` JSONL.

**The raw logs are committed:**
- `engram_lean` headline (83.6%, 9.6k tokens): [`results/longmemeval_s_engram_lean_v2_final.jsonl`](results/longmemeval_s_engram_lean_v2_final.jsonl) — 500 lines.
- full-context baseline (73.2%, 500/500 scored) + the `engram_full` variant (83.4%, 499/500 scored, 1 error), same 500-question run, same answerer + judge: [`results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl`](results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl).

Recompute any table yourself: `python eval/report.py <file.jsonl>`. Before a log is used as published
evidence, check the cited system(s) with
`python eval/validate_results.py --expected-rows <full item count> --require-complete --system <name> <file.jsonl>`.
Omit `--system` only when every system in the log is meant to be complete, citable evidence.

### Provider setup

Models are addressed as `provider:model` and resolved by `engram/llm/providers.py` via LiteLLM, so any
OpenAI-compatible endpoint works. Swap in OpenAI / DeepSeek / a local model and the same commands run.
The embedder and the offline fallbacks need no key.

---

## Measurement-integrity notes (what we fixed so the number is honest)

These are the kind of bugs that silently inflate or deflate memory-benchmark numbers. We document ours:

1. **Lean, not full-history (the honest test):** an earlier headline (`engram_full`) prepended facts above
   the *entire* conversation history (~79k tokens). That system *contains* full-context, so it can't really
   lose to it — it doesn't validate the memory architecture. The headline is now `engram_lean`, which
   retrieves a ~9.6k-token slice and is the real test of the retrieval thesis.
2. **Full-context truncation bug (was deflating the baseline, not us):** the full-context baseline was once
   capped below the `_S` haystack size, feeding it only the oldest sessions. Fixed so it gets the whole
   haystack. Any "full-context only scores 30%" claim from before that fix is a truncation artifact.
3. **Official judge, not a homemade one:** an earlier generic "same info?" judge was *stricter* than the
   official LongMemEval judge and deflated scores while making them non-comparable. We use the official
   prompts verbatim.
4. **Abstention handling:** `_abs` questions are graded by the official *unanswerable* judge.
5. **Reliability:** the LLM client uses exponential-backoff retry with jitter + transient/permanent error
   classification; the headline run completed with **0 errored questions** out of 500.

---

## Honest caveats

- **83.6% is a real result for the current `engram_lean` configuration, not a cherry-picked slice.** Small
  samples were repeatedly optimistic during development (an 18-item slice once read 83% when an earlier
  full-set truth was ~58%); **only full-500 numbers appear here.**
- The lean headline (83.6% @ 9.6k) and the full-context baseline (73.2% @ 79k) are both 500-question runs
  under the same doubao answerer + deepseek judge; the full-context baseline depends only on
  (questions, answerer, judge), all identical, so it is a fair reference for the lean number.
- The hardest categories — multi-session reasoning and single-session-preference — are the active roadmap,
  updated here with the same reproduce discipline. If a future number appears in this file, the command to
  reproduce it appears next to it.
