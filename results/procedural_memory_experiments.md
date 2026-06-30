# Procedural Memory Experiments

**Date**: 2026-06-30

**Change**: add a default-on, source-backed procedural memory read layer for standing rules, runbooks,
instructions, and how-to facts. The layer is configurable through `Config.procedural_memory` and can be
disabled in benchmark ablations with `procedural_memory`.

This is not a public benchmark claim. It is a PR gate for a Stage 2 derived-memory algorithm change.

## Commands

```bash
python3 eval/ablate_features.py --jsonl results/procedural_memory_ablation.jsonl
pytest -q tests/test_product_fixes.py -k 'procedural or summary_fallback'
pytest -q tests/test_lean.py -k 'evidence_planner or procedural_memory'
pytest -q tests/test_eval_ablation.py
```

Real-data context acceptance, LLM-free:

```bash
python3 - <<'PY'
# See the committed JSONL for per-item rows and summary.
PY
```

Output: `results/procedural_memory_context50.jsonl`

## Saved Results

| Artifact | Scope | Result |
| --- | --- | --- |
| `results/procedural_memory_ablation.jsonl` | Zero-key feature ablation | `15/15` features improved; `procedural_memory` enabled HIT / disabled `--` |
| `results/procedural_memory_context50.jsonl` | LongMemEval_S stride-50, context-only | `engram_lean` 34/50 answer-session hits, 7580.36 avg tokens, 444.45 ms p50, 785.224 ms p95, 0 errors |
| `results/procedural_memory_context50.jsonl` | Same slice, procedural disabled | 34/50 answer-session hits, 7585.56 avg tokens, 434.919 ms p50, 779.647 ms p95, 0 errors |

## Interpretation

- The targeted ablation proves the new layer adds source-backed procedural evidence for a runbook/rule
  query that the disabled path cannot surface as a procedural block.
- The LongMemEval_S stride-50 acceptance run is a safety/neutrality gate, not a QA accuracy claim. It
  shows no errors and no broad context regression on a real benchmark slice.
- The stride-50 sample contained 2 procedural-shaped questions, but the offline extractor did not produce
  procedural facts for them, so no procedural blocks appeared in that slice. This means the next useful
  improvement is extraction coverage for procedure/rule facts, not more read-path machinery.
