# M2 runbook — competitors, second benchmark, multi-backbone

The three experiments that take the paper from "workshop-grade" to "top-venue competitive"
(see the paper's Limitations). **Every number here must come from a real run — never fabricate.**
All three reduce to running `eval/bench.py`, which applies the *same* answerer + judge to every
system, so any within-run comparison is apples-to-apples by construction.

> ⚠️ **The gate.** These runs make real LLM calls (each memory system's own extraction **plus** the
> shared answerer + judge, ×500 questions × #systems × #backbones). That is real API spend and hours
> of wall-clock, and it needs API keys + datasets that are **not** in the offline dev env. Set the keys,
> then run. Raw per-question logs land in `--out`; fold them into the paper with `eval/report.py` and
> `paper/compute_stats.py`.

## Keys (set what each run needs)

| Env var | Used by |
|---|---|
| `ARK_API_KEY` (+ `ARK_BASE_URL`) | answerer/extractor when `--answerer volcano:…` / `doubao:…` |
| `DEEPSEEK_API_KEY` | the DeepSeek judge **and** Mem0's internal LLM |
| `OPENAI_API_KEY` | HippoRAG (OpenIE) and Graphiti's extraction LLM |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Zep/Graphiti's graph backend (a running Neo4j or FalkorDB) |
| `HIPPORAG_LLM` | HippoRAG LLM name (default `gpt-4o-mini`) |

`engram_lean` / `full_context` themselves need only the shared answerer+judge+extractor keys
(+ the local `bge-small` embedder, no key).

---

## 1. Competitors on the *same* harness  ✅ code ready

Adapters live in `eval/bench.py` as black boxes (`context(item)->str`), registered in `SYSTEMS`:

| System | Status | Extra setup to run |
|---|---|---|
| `mem0` | **wired + package installed** | `DEEPSEEK_API_KEY` (Mem0's internal LLM) |
| `zep` | adapter added — **validate on first keyed run** | `pip install graphiti-core`; running Neo4j/FalkorDB; `OPENAI_API_KEY` |
| `hipporag` | adapter added — **validate on first keyed run** | `pip install hipporag`; `OPENAI_API_KEY` (+ `HIPPORAG_LLM`) |

`zep`/`hipporag` are written against the libraries' public APIs but could not be executed in the
offline env (no keys / no graph DB), so treat the first keyed run as a validation pass — signatures
across library versions may need a one-line tweak.

```bash
# headline competitor table (start with the ready one: Engram vs Mem0 vs full-context)
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python eval/bench.py \
    --data s --limit 500 \
    --systems engram_lean,mem0,full_context \
    --answerer volcano:doubao-seed-2-0-pro-260215 \
    --judge volcano:deepseek-v3-2-251201 \
    --extractor volcano:doubao-seed-1-6-flash-250615 \
    --embedder bge-small --reasoning --persona \
    --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 \
    --out results/lme_s_competitors_mem0.jsonl

# add zep + hipporag once their packages/backends are up:
#   --systems engram_lean,mem0,zep,hipporag,full_context  --out results/lme_s_competitors_all.jsonl

python eval/report.py results/lme_s_competitors_mem0.jsonl   # per-system, per-category table
```

## 2. Second benchmark — LOCOMO  ✅ loader ready (validated offline)

`eval/locomo.py` converts the public LOCOMO release into the harness's item shape — one item per QA pair,
the haystack being that sample's full multi-session conversation; LOCOMO category 5 (adversarial) maps onto
LongMemEval abstention. The conversion is unit-tested offline (`python -m eval.locomo`): item shape, date
normalisation (LOCOMO "1:56 pm on 8 May, 2023" → `parse_date`-friendly `2023/05/08 13:56`), and abstention
are all verified. So only the dataset file + the usual answerer/judge keys are needed to run.

Get the data: download `locomo10.json` from https://github.com/snap-research/locomo (`data/locomo10.json`)
into `eval/locomo10.json`, then run exactly as for LongMemEval:

```bash
python eval/bench.py --data locomo --limit 500 --systems engram_lean,mem0,full_context \
    --answerer volcano:doubao-seed-2-0-pro-260215 --judge volcano:deepseek-v3-2-251201 \
    --extractor volcano:doubao-seed-1-6-flash-250615 --embedder bge-small --reasoning --persona \
    --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 \
    --out results/locomo_engram_vs_mem0.jsonl
python eval/report.py results/locomo_engram_vs_mem0.jsonl
```

## 3. Multiple backbones  ✅ already supported

`make_llm` (`engram/llm/providers.py`) already routes many providers, so multi-backbone == re-running
with a different `--answerer` (+ that provider's key). No code change needed.

```bash
for A in "deepseek" "qwen-max" "openai:gpt-4o" "volcano:doubao-seed-2-0-pro-260215"; do
  python eval/bench.py --data s --limit 500 --systems engram_lean,full_context \
      --answerer "$A" --judge volcano:deepseek-v3-2-251201 \
      --extractor volcano:doubao-seed-1-6-flash-250615 \
      --embedder bge-small --reasoning --persona \
      --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 \
      --out "results/lme_s_backbone_$(echo $A | tr ':/' '__').jsonl"
done
```

The thesis (a lean retrieved context beats full-context on accuracy) is only convincing if it holds
across backbones — that's exactly why this matrix matters.
