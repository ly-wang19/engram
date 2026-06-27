# Benchmark Result Logs

This directory stores raw JSONL evidence for benchmark claims. Treat it as an audit log, not a scratch
dump.

## Commit Rule

A result log can be committed as evidence only when it has a clear consumer:

- `README.md` / `README.zh-CN.md` / `RESULTS.md` public numbers
- paper tables, checklists, or runbooks marked `DONE`
- regression fixtures used by tests

Before committing a new cited full-set run, validate it:

```bash
python eval/report.py results/<name>.jsonl
python eval/validate_results.py --expected-rows 500 --require-complete results/<name>.jsonl
```

The validator must pass before the log is marked `DONE` or used in public copy.

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
