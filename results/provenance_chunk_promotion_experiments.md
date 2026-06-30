# Provenance Chunk Promotion Experiments

Date: 2026-06-30

Status: PR-gate evidence for `provenance_chunk_promotion`. These artifacts are exploratory or gate
checks, not public benchmark claims.

## Commands And Artifacts

### Offline feature ablation

```bash
python3 eval/ablate_features.py --jsonl results/provenance_chunk_promotion_ablation.jsonl
```

Artifact: `results/provenance_chunk_promotion_ablation.jsonl`

Result: `improved 13/13 features`; the new `provenance_chunk_promotion` row is enabled `HIT`,
disabled `--`, target `source episode promoted into full-detail raw chunk`.

### Real-data LLM-free recall smoke

```bash
python3 eval/longmemeval.py --mode recall --data eval/longmemeval_sample.json --limit 4 --embedder hashing --topk 3
```

Saved artifact: `results/provenance_chunk_promotion_recall_sample.jsonl`

Result: LongMemEval sample, 3 answerable items, `hit@3 = 1.0`, `recall@3 = 1.0`.

### Real-data context acceptance

Saved command equivalent:

```bash
python3 - <<'PY'
# Uses eval.longmemeval.load_data('s') with stride-50 sampling and compares
# engram_lean vs engram_lean_no_provenance_chunks without external LLM calls.
PY
```

Artifact: `results/provenance_chunk_promotion_context50.jsonl`

Result on LongMemEval_S stride-50 sample:

| System | Answer-session hits | Avg context tokens | p50 context ms | p95 context ms | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| `engram_lean` | 33/50 | 7557.44 | 451.22 | 789.028 | 0 |
| `engram_lean_no_provenance_chunks` | 35/50 | 7677.28 | 440.317 | 789.326 | 0 |

Interpretation: this real-data gate shows no runtime errors and no context-size regression on the
sample, but it is neutral on answer-session coverage. The targeted improvement is proven by the
offline ablation, not by a public benchmark claim.

### External LLM QA attempt

```bash
python3 eval/bench.py --data eval/longmemeval_sample.json --limit 4 \
  --systems engram_lean,engram_lean_no_provenance_chunks \
  --answerer deepseek --judge qwen-plus --extractor deepseek --embedder hashing \
  --workers 1 --topk 10 --chunks 3 --extract-k 4 \
  --out results/provenance_chunk_promotion_sample.jsonl
```

Artifact: `results/provenance_chunk_promotion_qa_attempt_failed.jsonl`

Result: failed provider attempt. LiteLLM repeatedly reported provider access-denied/retry messages
during judge calls; the incomplete two-row JSONL output was removed and replaced with the failed
attempt record above.

## Conclusion

This change is acceptable as an algorithmic PR-gate improvement because:

- The synthetic ablation proves the intended behavior with an enabled-vs-disabled comparison.
- Real LongMemEval_S context assembly runs on 50 fixed-stride items with 0 errors.
- Existing full-set real benchmark evidence remains valid:

```bash
python3 eval/validate_results.py --expected-rows 500 --require-complete --system engram_lean results/headline_500.jsonl
```

Output: `OK results/headline_500.jsonl`.
