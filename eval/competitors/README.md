# The neutral scoreboard — Engram vs shipped competitor memory engines

CLAUDE.md Bet D says the moat is *being the trustworthy scoreboard*. Until now the scoreboard had one
row: Engram vs a full-context baseline. Every competitor number in circulation — Mem0's marketing
figures, Tencent Hunyuan's 85.2% on LongMemEval, OpenViking's 91% on HotpotQA — comes from that
vendor's own harness, with its own answerer, its own judge, and in some cases no released code at all.
Nobody has re-measured them side by side. That is the gap this directory fills.

`results/cmp_mem0.jsonl` was the previous attempt. All 105 mem0 rows in it are
`{"ok": null, "err": "APIStatusError: 402 Insufficient Balance"}` — the run died on a billing error and
was never repeated, so the file records zero competitor evidence. It is kept for provenance only; do not
cite it.

## What's here

| file | role |
|---|---|
| `run_hy_memory.sh` | brings Hy-Memory up in the exact configuration it is measured in |
| `stratified_slice.py` | deterministic, category-proportional question slice, so every system sees the same questions |
| `run_scoreboard.sh` | launches the head-to-head runs |
| `openviking_worker.py` | out-of-process OpenViking driver (its Rust/C++ install cannot share the harness interpreter) |
| `run_verify_retry_ablation.sh` | A/B of `--verify-retry` on the questions Engram abstains on |
| `why_missed.py` | post-hoc: rebuild the lean context for one question and check whether the gold evidence ever entered it |

### OpenViking status: adapter validated, run not yet executed

The `openviking` system is wired and proven end-to-end on one question (54 sessions ingested,
committed, searched, real extracted content returned) but has not been run over a slice. Three things
had to be discovered the hard way, and are recorded here because each one silently produces a *fake*
competitor defeat:

1. **`commit_session` is mandatory.** `add_message` only buffers into the live session; nothing is
   extracted until the session is committed. Skip it and the engine stores nothing.
2. **`search` returns pointers, not content.** It is a planner over the URI-Trie and answers with
   `.abstract.md` / `.overview.md` nodes describing directories. Scoring those means scoring OpenViking's
   folder descriptions. The worker descends from each pointer into the directory and reads the leaves,
   which is what an agent driving OpenViking would do.
3. **Tenant isolation does not work through the facade.** `AsyncOpenViking.__init__` hard-codes
   `UserIdentifier.the_default_user()` and forwards only `path`, so a `user=` argument is silently
   dropped and every question would pile into `viking://user/default`. Isolation is one workspace
   directory per question instead.

Also note their config's `embedding.dimension: 1024` is stale — the ARK model now returns 2048, and
`doubao-embedding-vision` is only served on `/embeddings/multimodal`, not `/embeddings`.

Incidentally, on the one question tried (`e47becba`, "What degree did I graduate with?"), a `grep` over
the whole OpenViking workspace finds the gold sentence **only in the raw session archive** — their
extraction pipeline did not lift it into the memory tree either. That is the same miss Engram makes on
this question; see the identity-attribute finding in COMPARISON.md.

## The fairness ledger

A competitor number is worthless as evidence if the competitor can credibly say it was sandbagged. Every
knob that could tilt the result, and which way it was set:

| knob | setting | who it favours |
|---|---|---|
| questions | identical qid list, category-stratified, seeded | neutral |
| answerer | `doubao-seed-2-0-pro` for every system | neutral |
| judge | official LongMemEval category-specific prompts, one judge for all | neutral |
| embedder | `bge-small-en-v1.5` for Engram, mem0 **and** Hy-Memory | neutral — Hy-Memory ships bge-small-**zh**, which would have quietly cost it points on an English benchmark |
| competitor internal LLM | `doubao-seed-1-6-flash`, the same model as Engram's extractor | neutral |
| Hy-Memory mode | `ultra` — System-2 + Kuzu graph + L3 summaries | competitor; this is their strongest mode, not their default-lite |
| Hy-Memory System-2 flush | `/api/v1/digest` called after ingest | competitor; without it a pure-HTTP integration only ever sees System-1 layers |
| session dates | passed to Engram (`event_time`) and Hy-Memory (`memory_at`) | see caveat below |
| `--verify-retry` | **off** | would have been Engram-only (the retry path is hard-coded to `engram_lean`) |
| `--strategies` | **off** | Engram-side answer routing competitors cannot receive |
| retrieval budget | `--topk 15` for every system | neutral in item count; see token caveat |

### Known caveats, stated up front

- **mem0 gets no real timestamps.** `mem0.Memory.add()` in 0.1.93 exposes no time argument — only
  `metadata`. Engram and Hy-Memory both receive true LongMemEval session dates. This disadvantages mem0
  on `temporal-reasoning`, which is 133/500 of the benchmark. Treat mem0's temporal column as a floor,
  not a measurement, and discount any Engram win that rests on it.
- **Equal `topk` is not equal context.** 15 items means ~170–300 words from mem0 (atomic one-line facts)
  and ~8k words from Engram (facts + provenance chunks + session summaries). Accuracy must therefore be
  read next to the token column, never alone: a system that is 5 points behind at 1/30th the context is
  not obviously the worse engine.
- **The judge is not the judge behind the published 83.6%.** ARK retired `deepseek-v3-2-251201`; the runs
  here use DeepSeek's own API, which now serves v4-flash. Comparisons *within* a run are exact;
  comparisons to RESULTS.md's headline are not. See RESULTS.md → "Endpoint drift".
- **Hy-Memory reader.** Their engine ships several read pipelines (`legacy` / `hybrid` / `hybrid_v2` /
  `exhaustive`); these runs use the shipped default (`legacy`). If Hy-Memory loses, a reader sweep is the
  first thing a sceptic should ask for, and the honest answer is that it has not been run yet.
- **Slice size.** RESULTS.md's standing rule is that only full-500 numbers are headline numbers, because
  small slices were repeatedly optimistic during development. Nothing produced here is a headline number.
  It is a first neutral measurement with its denominators printed next to it.

## Validity checks actually run

Claims a sceptic would make first, and what was done about them:

- **"You leaked one question's memories into another."** Hy-Memory keeps all questions in one Chroma
  collection (`agent_memories_384`) and separates them by `user_id` at query time, so this had to be
  checked rather than assumed. Searching a populated `user_id` returns 10 normal + 3 profile memories;
  searching a `user_id` that was never written returns **0 and 0**. No leakage.
- **"You starved mem0."** mem0's contexts are ~170–300 words against Engram's ~8,000, so the possibility
  is real. It isn't starvation: on the very first probe question mem0 answered correctly from 172 words
  while `engram_lean` abstained on 7,177. Both get the same `--topk 15`; the size gap is architectural
  (atomic one-line facts vs facts + chunks + summaries), and both numbers are reported side by side.
- **"How much of the margin is noise?"** `engram_lean` runs in both scoreboard processes over the same
  qids. Already visible on the overlap: qid `19b5f2b3` scored correct in one run and wrong in the other
  — same system, same config, same question. Any head-to-head margin has to clear that floor, and the
  floor is measured rather than assumed.
- **"You measured a crippled competitor."** Three integration bugs were found and fixed *before* scoring,
  each of which would have produced a fake competitor defeat: Hy-Memory's System-2 never flushing
  (no HTTP route for `digest`), OpenViking never extracting anything (no `commit_session`), and
  OpenViking's search returning folder descriptions instead of memories (no descent from the pointers).
- **"Your latency numbers are your own machine's fault."** They were, at first. Run the two scoreboard
  processes at once and mem0's in-process torch embedder saturates all ten cores; the local bge service
  that *every Hy-Memory write* goes through then answers in **5.9s instead of 0.33s** — an 18× penalty
  charged entirely to the competitor's column. Reported ingestion latency is only meaningful when the
  shared local services are uncontended, so the runs are now **sequential, not parallel**
  (`run_scoreboard.sh` launches both; stop one if the box has fewer cores than the two need). Parallelism
  here has to be sized by the local bottleneck, not by what the remote API can absorb.

## What a run this size can and cannot answer

The noise floor is measured, not assumed, and it is wide. Duplicating `engram_lean` across both
processes gave **62.5% vs 75.0% on the same 16 questions** — a 12.5-point spread from run-to-run
variance alone, with `temperature=0.0` throughout. The verify-retry ablation reproduced the same
magnitude independently (~3 questions flipping the wrong way at n=33).

Practical consequence — match the question count to the size of the effect you are chasing:

| question | effect size | what it needs |
|---|---|---|
| does Engram beat mem0 / Hy-Memory? | 45+ points | n=60 is far more than enough |
| is Engram's absolute score X%? | — | full 500, or the number carries ±6pt at n=60 |
| did a retrieval tweak add 1–3 points? | 1–3 points | full 500, or repeated runs averaged — **n=60 cannot see this** |
| did a per-category change help? | varies | per-category cells at n=60 hold 2–14 questions; most are unusable alone |

The last row is the trap. A 60-question run reports `single-session-preference` from three questions;
one flip moves that cell 33 points. Cells that small are for spotting directions worth investigating, not
for claiming results — and they should never be quoted without their denominator, which is why
`report.py` always prints it.

## Reproduce

```bash
bash eval/competitors/run_hy_memory.sh          # engine up (Hy-Memory only)
bash eval/competitors/run_scoreboard.sh 40      # two runs, same 40 questions
python eval/report.py results/scoreboard_mem0_n40.jsonl
python eval/report.py results/scoreboard_hy_n40.jsonl
python eval/compare.py results/scoreboard_mem0_n40.jsonl results/scoreboard_hy_n40.jsonl
```

`engram_lean` runs in *both* processes on purpose. The two `engram_lean` columns are the same system on
the same questions, so the gap between them is answerer/judge nondeterminism — a free noise floor that
tells you how much of any head-to-head margin to believe.

## Upstream change required for Hy-Memory

Hy-Memory's `client.digest()` is documented as the eval path ("batch `add()`, then call `digest()` once"),
but `server.py` exposed no route for it, so an HTTP integration could never flush System-2 and would
silently measure a crippled engine. A `POST /api/v1/digest` route was added to the local checkout
(`记忆/hy-memory-src`, commit "feat: 暴露 /api/v1/digest"). Anyone reproducing this needs that route, or
must drive the Python client in-process instead.
