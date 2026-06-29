# Engram Evaluation

This directory is the reproducibility surface for Engram's benchmark claims. The core rule is simple:
a number is publishable only when the raw JSONL log is committed, reportable, and validated.

## Offline Smoke Harness

Run the zero-key project smoke first:

```bash
python scripts/check_zero_setup.py
```

That command exercises the quickstart, synthetic harness, committed public-log validation, paper stats,
and stdlib compilation without optional dependencies. To run only the synthetic harness:

```bash
python eval/harness.py
python eval/harness.py --storage durable --out results/synthetic_durable.jsonl
python eval/report.py results/synthetic_durable.jsonl
```

Synthetic logs are for regression and harness-shape checks. They are not public benchmark evidence.

## Algorithm Ablation Smoke

When read-path features are added, first prove they add the intended evidence in a zero-key synthetic
ablation before spending on LongMemEval/LOCOMO:

```bash
python eval/ablate_features.py
```

This command toggles the newest evidence features one at a time and checks whether the enabled path
surfaces evidence that the disabled path cannot: supersedes-chain context, provenance-backed raw snippets,
and n-hop graph proximity. It is a local improvement proof for the target behavior, not a public
accuracy claim. Publishable claims still require `eval/bench.py` plus committed raw logs.

## Real Benchmarks

`eval/bench.py` is the unified rig for LongMemEval/LOCOMO/PersonaMem-style runs. Every system in one
run shares the same answerer, judge, extractor, and data slice, so within-run comparisons are the only
apples-to-apples claims.

After a real run writes `results/<name>.jsonl`, use both tools:

```bash
python eval/report.py results/<name>.jsonl
python eval/validate_results.py --expected-rows <full item count> --require-complete results/<name>.jsonl
```

- `report.py` summarizes whatever was written: accuracy, context tokens, p50/p95 latency, errors, and
  scored denominators.
- `validate_results.py` fails if a log is not complete enough to cite: wrong row count, duplicate qids,
  missing fields, unscored rows, or error rows.
  For LongMemEval_S full-set runs, `<full item count>` is `500`; use the benchmark's real full item count
  for other datasets.
  If a multi-system log includes an exploratory or explicitly errored system that is not part of the
  published claim, validate the cited systems explicitly:

```bash
python eval/validate_results.py --expected-rows 500 --require-complete \
    --system engram_lean --system full_context results/<name>.jsonl
```

- `audit_results.py` scans a messy local `results/` directory and labels each log `complete`,
  `incomplete`, or `invalid` so exploratory logs do not get mistaken for evidence.
- `compare.py` compares two or more validated logs on their shared scored qids for one system, reporting
  per-log accuracy, disagreements, and the oracle upper bound for multi-backbone complementarity:

```bash
python eval/compare.py results/backbone_a.jsonl results/backbone_b.jsonl --system engram_lean --per-category
```

Only logs that pass validation should be marked `DONE` in paper notes or used in README/RESULTS copy.
Partial logs, smoke slices, failed competitor runs, and exploratory ablations can stay local until they
are completed or explicitly documented as exploratory. See [`../results/README.md`](../results/README.md)
for the commit policy for raw logs.

Before deciding what to commit, audit the directory:

```bash
python eval/audit_results.py
python eval/audit_results.py --fail-invalid results/headline_500.jsonl results/bb_flash.jsonl
```

PersonaMem-v2 is a multiple-choice runner with a different JSONL shape (`pref_type` + picked option,
not LongMemEval's `cat` + judged `pred/gold`). Validate it with the explicit schema:

```bash
python eval/personamem.py --n-personas 20 --per-persona 5 --out results/personamem_v2.jsonl
python eval/report.py results/personamem_v2.jsonl
python eval/validate_results.py --schema personamem --expected-rows 100 --require-complete results/personamem_v2.jsonl
```

## Next Experiments

See [`M2_RUNBOOK.md`](M2_RUNBOOK.md) and [`../paper/EXPERIMENTS_CHECKLIST.md`](../paper/EXPERIMENTS_CHECKLIST.md)
for the current multi-backbone, competitor, LOCOMO, and variance runs.
