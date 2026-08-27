# Supplementary experiments — main-track upgrade checklist

> **Judge 端点变更（2026-08-27）**：`volcano:deepseek-v3-2-251201` 已被 Volcano Ark 标记为 `status: Shutdown`（该平台整个 `deepseek-*` 族均已下线），本文档中的 judge 已换为仍在服务的 `deepseek`（DeepSeek 官方 API）。`doubao-seed-1-6-flash-250615` 当前为 `status: Retiring`，需留意后续替换。换 judge 会改变绝对分数，与已公布的 83.6 / 73.2 不可直接比较；同一次 run 内的系统间对照仍然有效。详见 `RESULTS.md`。

Goal: turn the paper from "workshop/Findings-ready" into "main-track competitive" by closing the four
reviewer-killers (single benchmark, single backbone, no competitor numbers, single run / no error bars).
Infrastructure for all of these is **already in the repo** (LOCOMO loader, competitor adapters, the unified
rig, `--answerer` swap). Each item below lists: the command, the paper artifact it produces, what it needs,
and rough cost. Ordered by reviewer-impact ÷ effort.

Conventions used in every command (the locked headline config):
```
--extractor volcano:doubao-seed-1-6-flash-250615 --judge deepseek \
--embedder bge-small --reasoning --persona \
--chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 \
--workers 4 --answerer-timeout 150 --resume
```
Run everything with `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=1`. Each run writes a
committed `results/*.jsonl`; `eval/report.py <log>` prints the table; stats via `paper/compute_stats.py`.

---

## P0 — Integrity fix (DONE, committed evidence)
Canonical single-run headline so lean+full are within-run and trace to the reproduce command, and
`engram_full` reported as run-to-run variance (83.4 ↔ 86.0), not a cherry-picked single value.
```
python eval/bench.py --data s --limit 500 --systems engram_lean,full_context \
  --answerer volcano:doubao-seed-2-0-pro-260215  <common flags>  --out results/headline_500.jsonl
```
→ **Result:** `results/headline_500.jsonl` (500/500 scored, 0 errors): `engram_lean` 79.0% vs
`full_context` 76.0% at 7.3k vs 79.2k context tokens. This fixes Table 1 provenance and supports
the "within-run" wording for this answerer/config. It does **not** replace the public README headline
unless README/RESULTS are updated in the same change.

---

## P1 — Multi-backbone  ★ highest impact (kills "single answerer")
Re-run the headline (lean vs full_context) with 2–3 answerer backbones; **only `--answerer` changes.**
Shows lean>full holds regardless of the reader model — and quantifies cross-backbone spread.
```
# frontier (have): volcano:doubao-seed-2-0-pro-260215   -> results/headline_500.jsonl   [DONE]
python eval/bench.py --data s --limit 500 --systems engram_lean,full_context \
  --answerer univibe:gpt-5.5            <common flags> --out results/bb_gpt55.jsonl
python eval/bench.py --data s --limit 500 --systems engram_lean,full_context \
  --answerer volcano:doubao-seed-1-6-flash-250615  <common flags> --out results/bb_flash.jsonl  # small/open-ish [DONE]
```
→ **Artifact:** Table "Headline across 3 backbones" (lean acc, full acc, Δ, per backbone) + a sentence:
"the lean>full gain holds across backbones (Δ = +X..+Y)."
→ **Needs:** only API keys you already have. **Cost:** ~3 × the headline run.
→ **Note:** report each as mean of the backbone; the Δ (lean−full) is the claim, robust to per-backbone level.
→ **Completed so far:** `results/headline_500.jsonl` gives Δ=+3.0; `results/bb_flash.jsonl` gives
Δ=+12.2 (500/500 scored, 0 errors). `results/bb_gpt55.jsonl` is not committed because the available
local log is partial and should not be cited.

## P2 — Competitor memory systems  ★ kills "you only beat full-context"
Run Mem0 / Zep / HippoRAG on the SAME rig (adapters already in `eval/bench.py`).
```
# Mem0 (easiest: reuses DeepSeek as its internal LLM + bge-small):
OPENAI_API_KEY=$DEEPSEEK_API_KEY python eval/bench.py --data s --limit 500 \
  --systems engram_lean,mem0  <common flags> --out results/cmp_mem0.jsonl
# Zep/Graphiti (needs a running Neo4j + an OpenAI-compatible key for Graphiti's extractor):
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=... OPENAI_API_KEY=... OPENAI_BASE_URL=... \
  python eval/bench.py --data s --limit 500 --systems engram_lean,zep  <common flags> --out results/cmp_zep.jsonl
# HippoRAG 2 (needs `pip install hipporag` + a key; set HIPPORAG_LLM):
HIPPORAG_LLM=gpt-4o-mini OPENAI_API_KEY=... python eval/bench.py --data s --limit 500 \
  --systems engram_lean,hipporag  <common flags> --out results/cmp_hipporag.jsonl
```
→ **Artifact:** Table "Engram vs memory systems on LongMemEval_S (one harness, same answerer+judge)".
This is the table reviewers want most.
→ **Needs:** Mem0 = trivial (a key). Zep = a Neo4j instance (Docker is fine). HippoRAG = `pip install` + key.
→ **Caveat:** the adapters are scaffolding "validated on first keyed run, not in CI" — **smoke-test each on
`--limit 5` first**, fix any version-API drift, THEN run 500. Report exactly the config you ran (their
internal LLM, etc.) so it's apples-to-apples.

## P3 — Second benchmark: LOCOMO  ★ kills "single benchmark"
Loader is wired (`--data locomo`). Re-run the headline (and ideally competitors) on it.
```
python eval/bench.py --data locomo --systems engram_lean,full_context \
  --answerer volcano:doubao-seed-2-0-pro-260215  <common flags> --out results/locomo_headline.jsonl
```
→ **Artifact:** Table "LongMemEval_S + LOCOMO" (or a row per benchmark) showing the result generalizes.
→ **Needs:** verify `eval/locomo.py` loads the LOCOMO release you have (smoke `--limit 5`); LOCOMO's judge
differs — confirm the harness grades it correctly before trusting numbers.

## P4 — Run-to-run variance / error bars  ★ kills "single run" AND becomes a contribution
Repeat the headline config N≥3× (doubao is stochastic at temp 0); report mean ± std and per-item flip rate.
```
for i in 1 2 3; do
  python eval/bench.py --data s --limit 500 --systems engram_lean,full_context \
    --answerer volcano:doubao-seed-2-0-pro-260215  <common flags> --out results/var_run$i.jsonl
done
```
→ **Artifact:** (a) error bars on Table 1; (b) a "run-to-run variance" figure/paragraph: same config swings
±X points, item flip rate Y% — **the empirical backbone of the measurement-integrity / reproducibility
contribution.** This is what turns engram_full's 83.4↔86.0 from an embarrassment into a cited finding.
→ **Needs:** only repeats. **Cost:** ~3 × headline. Highest credibility-per-dollar.

---

## P5 — Controlled facts-only ablation (optional; upgrades a claim from "observation" to "result")
The paper states facts-only loses recall as a *development observation*. Make it a controlled full-500 result.
```
# hybrid (headline): chunks=2  ->  results/headline_500.jsonl (P0)
# facts-only: drop the raw chunks + summaries so the context is facts (+persona) only:
python eval/bench.py --data s --limit 500 --systems engram_lean \
  --answerer volcano:doubao-seed-2-0-pro-260215  --extractor ... --judge ... --embedder bge-small \
  --reasoning --persona --chunks 0 --n-summaries 0 --topk 15 --extract-k 8 \
  --workers 4 --resume --out results/ablate_factsonly.jsonl
```
→ **Artifact:** ablation row "facts-only vs hybrid" (expect facts-only < hybrid).
→ **Verify** `--chunks 0 --n-summaries 0` actually yields a facts-only context (read one assembled context to
confirm) before reporting.

---

## Honest notes (keep the paper defensible)
- **Report what you ran, with its config** — especially competitor internal LLMs/backends; mismatched
  backbones make competitor numbers contestable.
- **Every new run = a committed `results/*.jsonl`**; rewire `paper/compute_stats.py` to read each, so every
  table recomputes from logs with no model calls.
- **More runs surface more variance.** Don't hide it — error bars + the variance section are the point.
- **Don't claim SOTA / "beat <competitor>" on a single number within the noise band.** The defensible claims
  are: (i) lean > full-context on accuracy at ~8× fewer tokens, across backbones; (ii) Engram vs competitors
  on one neutral harness; (iii) the reproducibility/variance findings.
- **Order to submit:** P0 (done) → P1 + P4 (cheap, flag-only, huge credibility) make it Findings/COLM-strong;
  add P2 + P3 for a main-track shot.
