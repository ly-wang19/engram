# Engram Evaluation

This directory is the reproducibility surface for Engram's benchmark claims. The core rule is simple:
a number is publishable only when the raw JSONL log is committed, reportable, and validated.

## Offline Smoke Harness

Run the zero-key synthetic harness first:

```bash
python eval/harness.py
python eval/harness.py --storage durable --out results/synthetic_durable.jsonl
python eval/report.py results/synthetic_durable.jsonl
```

Synthetic logs are for regression and harness-shape checks. They are not public benchmark evidence.

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

Only logs that pass validation should be marked `DONE` in paper notes or used in README/RESULTS copy.
Partial logs, smoke slices, failed competitor runs, and exploratory ablations can stay local until they
are completed or explicitly documented as exploratory. See [`../results/README.md`](../results/README.md)
for the commit policy for raw logs.

## Next Experiments

See [`M2_RUNBOOK.md`](M2_RUNBOOK.md) and [`../paper/EXPERIMENTS_CHECKLIST.md`](../paper/EXPERIMENTS_CHECKLIST.md)
for the current multi-backbone, competitor, LOCOMO, and variance runs.
