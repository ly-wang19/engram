# Summary Fallback Experiments

Date: 2026-06-30

Status: PR-gate evidence for `summary_fallback`. These artifacts are exploratory or gate checks, not
public benchmark claims.

## Commands And Artifacts

### Offline feature ablation

```bash
python3 eval/ablate_features.py --jsonl results/summary_fallback_ablation.jsonl
```

Artifact: `results/summary_fallback_ablation.jsonl`

Result: `improved 14/14 features`; the new `summary_fallback` row is enabled `HIT`, disabled `--`,
target `derived session summary answers fact-miss how-to query`.

### Real-data LLM-free recall smoke

Saved command equivalent:

```bash
python3 eval/longmemeval.py --mode recall --data eval/longmemeval_sample.json --limit 4 \
  --embedder hashing --topk 3
```

Artifact: `results/summary_fallback_recall_sample.jsonl`

Result: LongMemEval sample, 3 answerable items, `hit@3 = 1.0`, `recall@3 = 1.0`.

### Real-data context acceptance

Saved command equivalent:

```bash
python3 - <<'PY'
# Uses eval.longmemeval.load_data('s') with stride-50 sampling and compares
# engram_lean vs engram_lean_no_summary_fallback without external LLM calls.
PY
```

Artifact: `results/summary_fallback_context50.jsonl`

Result on LongMemEval_S stride-50 sample:

| System | Answer-session hits | Avg context tokens | p50 context ms | p95 context ms | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| `engram_lean` | 32/50 | 7575.6 | 441.321 | 772.143 | 0 |
| `engram_lean_no_summary_fallback` | 33/50 | 7559.24 | 450.027 | 774.314 | 0 |

Interpretation: this real-data gate shows no runtime errors and neutral context-size/latency behavior
on the sample. It is not a public QA accuracy claim; the target improvement is the fact-miss
derived-summary search fallback proven by the offline ablation.

## Conclusion

This change is acceptable as an algorithmic PR-gate improvement because:

- The synthetic ablation proves the intended enabled-vs-disabled behavior.
- The fallback now returns source-backed summary answers, including the source session id.
- Real LongMemEval_S context assembly runs on 50 fixed-stride items with 0 errors.
- Existing full-set real benchmark evidence remains valid:

```bash
python3 eval/validate_results.py --expected-rows 500 --require-complete --system engram_lean results/headline_500.jsonl
```

Output: `OK results/headline_500.jsonl`.
