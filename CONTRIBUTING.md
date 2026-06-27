# Contributing to Engram

Engram aims to be a best-in-class, **fully reproducible** open-source memory engine. Two things make that
real: the code, and the discipline of how we talk about it. The short rules below apply to human
contributors **and to AI coding agents** working on this repo alike — please follow them.

## Dev basics

- **Zero-setup invariant.** `python examples/quickstart.py` and `pytest` MUST pass with **no API keys and
  no external services** (hashing embedder + rule extractor + in-memory stores). Never break this.
- **Zero-dep smoke check.** Run `python scripts/check_zero_setup.py` before publishing a benchmark or
  claiming the repo works from a clean checkout. It exercises the quickstart, offline harness, committed
  evidence-log validation, paper stats, and stdlib compilation without optional packages.
- **Tests.** Run `pytest` before pushing when test dependencies are installed; add tests for new behavior.
  Keep the offline fallbacks deterministic.
- **Interfaces first.** Every external dependency (LLM, embedder, store) sits behind an interface with a
  zero-dep offline fallback. Don't import heavy deps at module top level in core.
- See [`CLAUDE.md`](CLAUDE.md) for the architecture and conventions.

## Numbers & benchmark claims (non-negotiable)

**A number we cannot reproduce does not exist.**

- Every published number must trace to a **committed raw log** (`results/*.jsonl`), re-derivable with
  `python eval/report.py <file>`. If you can't point to the log, don't publish the number.
- Before marking a run as DONE or using it as paper/README evidence, validate it with
  `python eval/validate_results.py --expected-rows <full item count> --require-complete <file>`
  (for LongMemEval_S full set, `<full item count>` is `500`). If a multi-system log contains an
  exploratory or explicitly errored system that is not being cited, validate the cited systems with
  repeatable `--system <name>` flags instead of pretending the whole log is clean.
- Report the **triple**: accuracy **+** tokens **+** latency — never accuracy alone. Always include the
  **full-context baseline** from the *same run* (same answerer + judge), and say so plainly if we don't beat it.
- **No cherry-picking.** Full-set numbers only — small samples were repeatedly optimistic here. Use the
  official judge prompts; document any deviation.
- If you change a headline number, update **every** place it appears **in the same change** so they stay
  consistent: `README.md`, `README.zh-CN.md`, `RESULTS.md`, and the landing page (`demo/index.html` +
  `docs/index.html`).

## Messaging & positioning

Engram stands on its own. Be confident, but honest.

- **Don't name-drop or benchmark-compare competitors** in public-facing copy, and don't frame features as
  "borrowed from <big company>". Describe what *Engram* does, on its own merits.
- **No unbenchmarked claims.** Don't write "scales to 1M tokens", "SOTA", "#1", "cost stays flat as
  history grows", or similar unless a committed log backs it. Prefer the proven property (e.g. "answers
  from a retrieved slice rather than replaying the full history") over an aspirational scaling claim.
- **No stale status.** Don't leave "in progress / 验证中 / coming soon" on something that's already done
  (or vice versa). Keep the **English and 中文** copy in sync (README ×2, landing page).

## Commits

- **No AI attribution in commit messages.** Do not add `Co-Authored-By: Claude …` or `Generated with …`
  trailers. Write a plain, descriptive message. (If a branch arrives with such trailers, strip them before
  it lands on `main`.)

## Privacy

- **Never commit real personal data, names, or secrets.** Use synthetic placeholders in examples
  (e.g. `李雷`, `user123`, `my-app`). Keep `.env` gitignored. If you spot leaked PII or a credential in the
  history, flag it immediately — a real fix usually means rewriting history, not just a new commit.
