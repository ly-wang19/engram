# Engram — Benchmark Results & Methodology

This file is the full, reproducible record behind the headline number in the README. The project's core
discipline (AGENTS.md, Bet D): **a number we cannot reproduce does not exist.** Everything needed to
re-run and verify is here.

**Paper:** [arXiv:2606.09900](https://arxiv.org/abs/2606.09900) — the peer-reviewable write-up of the headline result below.

---

## Headline: LongMemEval_S, 500 questions

The headline system is **`engram_lean`** — it answers from a small *retrieved* slice of memory, never the
full history. This is the honest test of a memory system: compare a precisely filtered context with the
noisy full window in one paired run, reporting accuracy, tokens, and latency together.

| System | Overall | Avg context tokens | End-to-end latency (p50 / p95) | Errors |
|---|---:|---:|---:|---:|
| **Engram** (`engram_lean`) | **79.0%** | **7,283** | **93.6s / 173.7s** | 0 / 500 |
| full-context baseline (same run, answerer, and judge) | 76.0% | 79,241 | 14.5s / 60.1s | 0 / 500 |

In this canonical joint run, **Engram `engram_lean` is +3.0 points on the accuracy point estimate while
using 10.9× fewer context tokens** (7,283 vs 79,241). Both systems use the **official** LongMemEval judge
prompts and completed all 500 questions with 0 errors. The paired accuracy difference is not statistically
decisive (McNemar exact `p=0.195`; paired-bootstrap 95% CI `[-1.2, +7.2]` points), and Engram did not win on
end-to-end latency in this run. Latency includes the remote answer call and is reported as measured.

---

## Per-category breakdown (`engram_lean`, full 500)

| Category | Score | n | Notes |
|---|---:|---:|---|
| single-session-assistant | 100.0% | 56 | ceiling on this run |
| knowledge-update | 91.7% | 72 | bi-temporal "most-recent-wins" working |
| abstention | 90.0% | 30 | official *unanswerable* judge |
| single-session-user | 84.4% | 64 | profile/identity recall |
| temporal-reasoning | 70.9% | 127 | date-stamped context + reasoning chain |
| multi-session | 70.2% | 121 | counting/aggregation across sessions — active area |
| single-session-preference | 56.7% | 30 | active area |
| **Overall** | **79.0%** | **500** | |

**Efficiency and latency:** mean retrieved context is **7,283 tokens** (10.9× leaner than the 79,241-token
full-context baseline). End-to-end latency is p50 **93.6s** / p95 **173.7s** for `engram_lean` versus
p50 **14.5s** / p95 **60.1s** for `full_context`. These measurements include the remote
doubao-seed-2.0-pro answer call and are not presented as retrieval-only latency.

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
  (7,283 mean context tokens in the canonical run), never the full history.

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

- Canonical paired headline (`engram_lean` 79.0%, `full_context` 76.0%, 500/500 scored and 0 errors for
  both): [`results/headline_500.jsonl`](results/headline_500.jsonl).
- Historical independent lean run (`engram_lean` 83.6%, 9,568 mean tokens, 500/500 scored):
  [`results/longmemeval_s_engram_lean_v2_final.jsonl`](results/longmemeval_s_engram_lean_v2_final.jsonl).
- Historical separate run (`full_context` 73.2%, 500/500 scored; `engram_full` 83.4%, 499/500 scored and
  1 error):
  [`results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl`](results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl).

The historical 83.6% lean score and 73.2% full-context score are retained for auditability but came from
**different logs**. They must not be combined into a paired `+10.4`-point claim. Only
`results/headline_500.jsonl` is the canonical paired headline.

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
   retrieves a 7,283-token mean slice in the canonical run and is the real test of the retrieval thesis.
2. **Full-context truncation bug (was deflating the baseline, not us):** the full-context baseline was once
   capped below the `_S` haystack size, feeding it only the oldest sessions. Fixed so it gets the whole
   haystack. Any "full-context only scores 30%" claim from before that fix is a truncation artifact.
3. **Official judge, not a homemade one:** an earlier generic "same info?" judge was *stricter* than the
   official LongMemEval judge and deflated scores while making them non-comparable. We use the official
   prompts verbatim.
4. **Abstention handling:** `_abs` questions are graded by the official *unanswerable* judge.
5. **Reliability:** the LLM client uses exponential-backoff retry with jitter + transient/permanent error
   classification; the headline run completed with **0 errored questions** out of 500.
6. **Paired evidence, not mixed runs:** the earlier public `83.6% vs 73.2% (+10.4)` statement combined a
   lean result and baseline result from separate logs. Both runs remain published as historical evidence,
   but the headline now comes only from the joint `results/headline_500.jsonl` run.

---

## Honest caveats

- **83.6% remains a real historical full-500 `engram_lean` result**, not a cherry-picked slice. It is not
  the paired headline because its comparison baseline was recorded in a different log. Small samples were
  repeatedly optimistic during development (an 18-item slice once read 83% when an earlier full-set truth
  was ~58%); only full-500 numbers appear here.
- The canonical paired headline is **79.0% vs 76.0%** from one joint run. Its +3.0-point estimate is positive
  but its confidence interval crosses zero, so we do not call it statistically decisive.
- The hardest categories — multi-session reasoning and single-session-preference — are the active roadmap,
  updated here with the same reproduce discipline. If a future number appears in this file, the command to
  reproduce it appears next to it.
