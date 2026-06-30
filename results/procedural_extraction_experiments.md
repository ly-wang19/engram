# Procedural Extraction Experiments

**Date**: 2026-06-30

**Change**: add default-on offline extraction for explicit runbook/how-to procedure text, gated by
`Config.procedural_extraction`, and keep procedural facts out of the entity graph so long step text does
not become graph nodes.

This is not a public benchmark claim. It is a PR gate for a Stage 2 derived-memory/consolidation change.

## Commands

```bash
python3 eval/ablate_features.py --jsonl results/procedural_extraction_ablation.jsonl
pytest -q tests/test_smoke.py -k 'runbook_source or to_colon_howto'
pytest -q tests/test_product_fixes.py -k 'procedural_extraction or procedural_memory'
pytest -q tests/test_eval_ablation.py
```

Real-data context acceptance, LLM-free:

```bash
python3 - <<'PY'
# See results/procedural_extraction_context25.jsonl for per-item rows and summary.
PY
```

## Saved Results

| Artifact | Scope | Result |
| --- | --- | --- |
| `results/procedural_extraction_ablation.jsonl` | Zero-key feature ablation | `16/16` features improved; `procedural_extraction` enabled HIT / disabled `--` |
| `results/procedural_extraction_context25.jsonl` | LongMemEval_S stride-25, context-only | `engram_lean` 17/25 answer-session hits, 7605.92 avg tokens, 397.675 ms p50, 667.465 ms p95, 0 errors |
| `results/procedural_extraction_context25.jsonl` | Same slice, procedural extraction disabled | 15/25 answer-session hits, 7472.08 avg tokens, 407.586 ms p50, 653.369 ms p95, 0 errors |

## Interpretation

- The targeted ablation proves explicit runbook text is promoted into typed, source-backed procedural
  memory and can be disabled independently.
- An initial broad rule treated ordinary imported role-labeled conversations as procedures. That was
  rejected during real-data context acceptance; the committed extractor is anchored to explicit
  `runbook/procedure/workflow/checklist:` headings or `To/How to <action>:` forms.
- Procedural facts are intentionally not projected into the entity graph because their objects are
  long step text, not graph entities. This preserves graph proximity quality and avoids regex work over
  long pseudo-entity names.
- The LongMemEval_S stride-25 run is a safety/neutrality gate, not a QA accuracy claim.
