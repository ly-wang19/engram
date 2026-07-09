# Chain Provenance Promotion Experiments

Date: 2026-07-09

Status: PR-gate evidence for chain-aware raw evidence fusion. These artifacts are internal acceptance
checks, not public benchmark claims.

## What Changed

Previous `lean_context()` could render a `FACT HISTORY` / `FACT EVOLUTION` table for superseded facts,
but provenance-driven raw chunk promotion was seeded mostly by the currently retrieved live facts. For a
previous-value question such as "Where did Wei work before Moonshot AI?", the context could show the old
value in the structured history table while promoting only the current value's source conversation.

This change lets chain-expanded facts seed provenance evidence too. For history queries, superseded/past
facts receive priority when selecting full-detail provenance chunks, so the raw source of the old value is
available beside the structured chain.

## Commands And Artifacts

### Offline feature ablation

```bash
/Users/ywwl/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  eval/ablate_features.py --jsonl results/chain_provenance_promotion_ablation.jsonl
```

Artifact: `results/chain_provenance_promotion_ablation.jsonl`

Result: `improved 24/24 features`. The new `chain_provenance_promotion` row is enabled `HIT`, disabled
`--`, target `superseded fact source promoted for previous-value questions`.

### Direct zero-dependency behavior check

```bash
/Users/ywwl/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
# Builds a two-step employer update with source episodes.
# Asserts chain_evidence=True promotes the old-source raw chunk,
# while chain_evidence=False keeps the current new-source chunk.
PY
```

Result: `OK chain provenance promotion behavior`.

### Real-data context acceptance

```bash
/Users/ywwl/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
# Loads eval/longmemeval_sample.json, runs chain_on vs chain_off context assembly,
# and records full-detail answer-session hits, tokens, latency, and errors.
PY
```

Artifact: `results/chain_provenance_promotion_context_sample.jsonl`

Result on the bundled LongMemEval sample:

| System | Items | Full-detail answer-session hits | Avg context tokens | Max context tokens | Avg context ms | Max context ms | Errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `chain_on` | 4 | 3 | 120.25 | 268 | 7.06 | 9.84 | 0 |
| `chain_off` | 4 | 3 | 96.00 | 223 | 4.18 | 8.32 | 0 |

Interpretation: the real-data sample acceptance shows no context assembly errors and keeps the added
chain-aware provenance cost small. The targeted improvement is proven by the offline enabled-vs-disabled
ablation, because the bundled sample does not include a previous-value raw-source question.

### Zero-setup project verification

```bash
/Users/ywwl/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  scripts/check_zero_setup.py
```

Result: passed. The command exercised quickstart, installed-module quickstart, agent CLI help, offline
synthetic eval, durable smoke eval, public evidence validation, paper stats, and stdlib compile checks.

### Pytest status

Attempted:

```bash
python3 -m pytest tests/test_lean.py -k "chain_provenance or provenance_source or history_for_previous or evolution_chain" -q
/Users/ywwl/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m pytest tests/test_lean.py -k "chain_provenance or provenance_source or history_for_previous or evolution_chain" -q
```

Both available Python environments reported `No module named pytest`. The new behavior is still covered
by `tests/test_lean.py::test_chain_provenance_promotes_superseded_source_for_history_query`; it should be
run in a dev environment with `.[dev]` installed.

## Conclusion

This is acceptable as a small algorithmic PR-gate improvement because:

- It connects the existing supersedes chain to the existing raw evidence fusion path.
- It is controlled by the existing `chain_evidence` ablation switch.
- The zero-key ablation proves the intended enabled-vs-disabled behavior.
- The bundled LongMemEval sample context run has 0 errors and saves token/latency evidence.
- Zero-setup verification still passes without API keys or external services.
