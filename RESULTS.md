# Engram — Benchmark Results & Methodology

This file is the full, reproducible record behind the headline number in the README. The project's core
discipline (CLAUDE.md, Bet D): **a number we cannot reproduce does not exist.** Everything needed to
re-run and verify is here.

---

## Headline: LongMemEval_S, 500 questions

| System | Answerer | Judge | Overall | Errors |
|---|---|---|---:|---:|
| **Engram** (`engram_full`) | gemini-2.5-pro | official LongMemEval prompts (gpt-5.5) | **86.0%** | 0 / 500 |

Reference points (all self-reported by their authors, on their own pipelines):

| System | LongMemEval_S | Open source? |
|---|---:|:---:|
| OMEGA | 95.4 | no |
| Mem0-2026 | 94.4 | partial |
| **Engram (this repo)** | **86.0** | **yes (Apache-2.0)** |
| Hunyuan Hy-Memory | 85.2 | no |

**Where we honestly stand:** Engram clears the closest open baseline (Hunyuan, 85.2). It is *not* at the
top of the leaderboard — OMEGA and Mem0-2026 are 8–9 points ahead. We publish this gap rather than hide it.
What Engram uniquely offers today is that **its number is fully reproducible from this repo with one
command**, on the *official* judge, with the full-context baseline measured under the identical pipeline.

---

## Per-category breakdown (Engram `engram_full`, full 500)

| Category | Score | n | Notes |
|---|---:|---:|---|
| single-session-assistant | 96.4% | 56 | near-ceiling |
| single-session-user | 95.3% | 64 | near-ceiling |
| knowledge-update | 93.1% | 72 | bi-temporal "most-recent-wins" working |
| temporal-reasoning | 87.4% | 127 | date-stamped context + reasoning chain |
| multi-session | 83.5% | 121 | counting/aggregation across sessions — partial |
| abstention | 70.0% | 30 | known weak spot: model over-answers (fabricates) — active fix |
| single-session-preference | 50.0% | 30 | hard industry-wide (frontier LLMs 37–48% on PersonaMem) |
| **Overall** | **86.0%** | **500** | |

**Efficiency:** mean context **~79.9k tokens** (~69% of the full-context haystack), p50 latency 48.8s,
p95 107.2s (the latency is the gemini-2.5-pro answer call on a long context, not Engram's retrieval).

---

## Exact setup

- **Dataset:** `longmemeval_s` (the real benchmark, 500 questions, ~50 sessions / ~115k-token haystack
  per question), pulled from HuggingFace `xiaowu0162/longmemeval`.
- **Embedder:** `BAAI/bge-small-en-v1.5` (local, no API key).
- **Internal extractor** (System-2 fact extraction): DeepSeek (`deepseek-chat`).
- **Answerer:** `gemini-2.5-pro`.
- **Judge:** `gpt-5.5`, prompted with the **exact official LongMemEval category-specific judge prompts**
  (`official_judge_prompt()` in `eval/longmemeval.py`) — temporal off-by-one tolerance, knowledge-update
  old-info tolerance, preference rubric leniency, "contains the answer" semantics, unanswerable detection.
- **The `engram_full` system:** Engram extracts conflict-resolved, bi-temporal facts from the top-k
  retrieved sessions and prepends them (most-recent-first) as a MEMORY INDEX above the full conversation
  history. This combines 100% session recall with Engram's structured/temporal layer.

### Reproduce

```bash
# headline number (needs model access for answerer + judge; see "Provider setup" below)
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python eval/bench.py \
    --data s --limit 500 \
    --systems engram_full,full_context \
    --answerer univibe:gemini-2.5-pro \
    --judge univibe:gpt-5.5 \
    --extractor deepseek --embedder bge-small \
    --reasoning --chunks 10 --topk 20 --extract-k 10 \
    --workers 3 --shuffle --seed 42 \
    --out data/run.jsonl

python eval/report.py data/run.jsonl     # prints the per-category table above
```

The harness applies the **same answerer and judge to every `--systems` entry**, so any comparison *within*
a run (e.g. `engram_full` vs `full_context`) is apples-to-apples by construction. Raw per-question logs
(prediction + gold + correctness + tokens + latency for every question) are written to the `--out` JSONL.

**The raw log for the headline 86.0% run is committed** at
[`results/longmemeval_s_engram_full_pro.jsonl`](results/longmemeval_s_engram_full_pro.jsonl) — 500 lines,
one per question, each with the model's prediction, the gold answer, the judge verdict, tokens and latency.
Recompute the table yourself: `python eval/report.py results/longmemeval_s_engram_full_pro.jsonl`.

### Provider setup

Models are addressed as `provider:model` and resolved by `engram/llm/providers.py` via LiteLLM, so any
OpenAI-compatible endpoint works. The runs above used a relay exposing `gemini-2.5-pro` and `gpt-5.5`;
swap in OpenAI / DeepSeek / a local model and the same commands run. The embedder and the offline
fallbacks need no key.

---

## Measurement-integrity notes (what we fixed so the number is honest)

These are the kind of bugs that silently inflate or deflate memory-benchmark numbers. We document ours:

1. **Full-context truncation bug (was deflating the baseline, not us):** the full-context baseline was
   capped at 200k chars while the `_S` haystack averages ~497k chars — it was being fed only the *first
   40%* (oldest) of sessions. Fixed the cap to fit the whole haystack. Any "full-context only scores 30%"
   claim from before this fix is a truncation artifact, not full-context's real ceiling.
2. **Official judge, not a homemade one:** an earlier generic "same info?" judge was *stricter* than the
   official LongMemEval judge and deflated our scores while making them non-comparable. We now use the
   official prompts verbatim.
3. **Abstention handling:** `_abs` questions are graded by the official *unanswerable* judge. Our current
   weak spot here (70%) is the model fabricating a plausible answer from related-but-not-matching context;
   this is an open item, tracked honestly, not hidden.
4. **Reliability:** the LLM client uses exponential-backoff retry with jitter + transient/permanent error
   classification; the headline run completed with **0 errored questions** out of 500.

---

## Honest caveats

- The competitor numbers (OMEGA 95.4, Mem0 94.4, Hunyuan 85.2) are **self-reported on their own answer +
  judge pipelines.** Our 86.0 uses a gemini-2.5-pro answerer and the official judge prompts. Cross-system
  comparison therefore carries the usual "different backbone" caveat; the honest, controlled comparison is
  the one *inside* this harness (same answerer/judge across systems).
- 86.0% is a real ceiling for the current `engram_full` + gemini-2.5-pro configuration, not a cherry-picked
  slice. Small samples were repeatedly optimistic during development (an 18-item slice once read 83% when
  the full-set truth was 58%); **only full-500 numbers appear here.**
- The roadmap to close the gap (preference + multi-session + abstention) is public and will be updated here
  with the same reproduce discipline. If a future number appears in this file, the command to reproduce it
  appears next to it.
