# Benchmark Result Logs

This directory stores raw JSONL evidence for benchmark claims. Treat it as an audit log, not a scratch
dump.

## Experiment Persistence Rule

Every experiment run for an algorithmic change must leave a saved artifact under `results/` or a
documented path named in the PR. This includes smoke ablations, fixed-slice real-data checks, failed
provider attempts, and complete benchmark runs.

- Prefer machine-readable JSONL via `--out`, `--jsonl`, or a small saved runner output.
- Record the exact command, dataset or slice, system(s), date, metrics, and status.
- Mark failed, partial, or exploratory artifacts explicitly; never present them as publishable benchmark
  evidence.

## Commit Rule

A result log can be committed as evidence only when it has a clear consumer:

- `README.md` / `README.zh-CN.md` / `RESULTS.md` public numbers
- paper tables, checklists, or runbooks marked `DONE`
- regression fixtures used by tests

Before committing a new cited full-set run, validate it:

```bash
python eval/report.py results/<name>.jsonl
python eval/validate_results.py --expected-rows <full item count> --require-complete results/<name>.jsonl
```

The validator must pass before the log is marked `DONE` or used in public copy.
For LongMemEval_S full-set runs, `<full item count>` is `500`; use the benchmark's real full item count
for other datasets.
When public copy cites only selected systems from a multi-system log, validate those systems explicitly
with repeatable `--system <name>` flags. This keeps disclosed auxiliary-system failures visible without
blocking a clean cited baseline.
PersonaMem-v2 logs come from `eval/personamem.py` and use a distinct multiple-choice schema; validate
them with `--schema personamem` and the sampled item count (for example, `--expected-rows 100` for
`--n-personas 20 --per-persona 5`).

## What Not To Commit As Evidence

Do not cite or commit local exploratory files as benchmark proof when they are:

- partial runs
- smoke slices
- failed competitor runs
- prompt/rerank reconstruction slices
- unvalidated cross-benchmark experiments

Those files are useful while developing, but they should either be completed and validated, or documented
explicitly as exploratory artifacts before entering the repository.

Plain `.log` files are ignored by `.gitignore`; keep terminal transcripts local unless a paper artifact
explicitly needs them.

## Release Validation Logs

Engineering release evidence may also be stored here when a specification or release report cites it.
For example, `commercial_release_0_1_0_validation.jsonl` records security, packaging, SDK, frontend,
container, and regression gates for the 0.1.0 delivery. These logs prove release readiness only: they
are not algorithm experiments and must never be cited as benchmark-quality or accuracy evidence.
