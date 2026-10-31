# Competitor scoreboard — findings

Companion to `eval/competitors/README.md` (methodology and the fairness ledger) and `RESULTS.md` (the
headline Engram number). This file records what building the first neutral head-to-head turned up.

Nothing here is a headline number. RESULTS.md's standing rule — only full-500 runs are headline
numbers, because small slices were repeatedly optimistic during development — applies to this file too.
Every figure below is printed with its denominator.

---

## 1. There were no competitor numbers on the scoreboard at all

CLAUDE.md Bet D claims the moat is *being the trustworthy scoreboard*. The scoreboard had one row:
Engram vs a full-context baseline. Every competitor figure in circulation — Mem0's marketing numbers,
Tencent Hunyuan's 85.2% on LongMemEval, OpenViking's 91% on HotpotQA — is self-reported on the vendor's
own harness, and none had been re-measured here.

`results/cmp_mem0.jsonl` looks like a prior attempt, but all 105 mem0 rows in it read
`{"ok": null, "err": "APIStatusError: 402 Insufficient Balance"}`. The run died on a billing error and
was never repeated. It contains zero competitor evidence; do not cite it.

The billing error is long resolved — the same adapter runs today. The comparison was blocked for two
months by an unrepeated failure, not by anything technical.

## 2. The reproduce command in RESULTS.md does not run

Verified 2026-08-17. Volcano/ARK has withdrawn `deepseek-v3-2-251201`, the judge named in the documented
command. The id returns `InvalidEndpointOrModel.NotFound`, and because the judge is shared, **every
system fails before a single question is scored** — a `litellm.NotFoundError` on all of them. No DeepSeek
model is served on ARK any more. The answerer and extractor are unaffected.

Fixed in RESULTS.md ("Endpoint drift") with a working substitute (`--judge deepseek`, DeepSeek's own
API). That substitute is *not* the judge behind the committed logs — `deepseek-chat` now serves v4-flash
— so fresh runs are internally consistent but not comparable to 83.6%.

This is the failure mode a reproducibility moat has to survive: the code stayed correct, the raw logs
stayed committed, and a third-party model id was retired underneath the documented command with nothing
in the repo noticing. Pinning judges to endpoints outside our control is a standing liability for Bet D.

## 3. The published 83.6% is stale, and its direction of drift is unknown

`results/longmemeval_s_engram_lean_v2_final.jsonl` — the log behind the headline — was produced
**2026-06-04**. Between 2026-06-29 and 2026-07-09, roughly twenty retrieval and extraction commits
landed on top of it: graph anchoring (self / entity-alias / negative constraints / cross-path
reinforcement), evidence budgeting by query intent, planner location and project chains, temporal
history answering, the three aggregation changes (numeric candidates, recall expansion, constraint
filter), preference extraction and normalization, procedural extraction and fallback, and provenance
chunk promotion. RESULTS.md still presents the June log as the current result.

On the 33 questions the June log records as *abstained-on-but-answerable*, the current code answers
**12/33 correctly** with no configuration change (`results/vr_ablation_off.jsonl`), and the flips are
substantive rather than judge leniency — `"I don't know"` became `"10%"`, `"7 pm"`, `"500"`, each
matching gold.

**That number does not show the commits helped, and an earlier draft of this file wrongly implied it
did.** §6 measures the per-question flip rate between two independent runs of the same code at **20%**.
Applied to 33 questions, pure variance predicts ~6.6 flips before any code change is considered; 12 were
observed, leaving ~5 unexplained. And the 33 are not a random sample — they were selected *because* they
failed, so they sit closer to the decision boundary and should flip *more* than the 20% baseline. 6.6 is
therefore a lower bound on the noise share, and the residual attributable to twenty commits is small and
possibly zero. **No improvement can be established from this data.**

What survives, and is not in doubt: the published number was measured on **June-4 code**, twenty
retrieval and extraction commits have landed since, and the command someone would use to check it is
broken. Whether current Engram is better, worse, or identical is simply **unknown**, and unknowable
until it is re-measured. For an asset whose pitch is a benchmark position, an unverifiable two-month-old
headline is the exposure — not the direction of the drift. Re-running the full 500 under a pinned,
currently-live judge (`eval/competitors/refresh_headline.sh`) is the highest-value single action
available, and it is cheap next to what it defends.

## 4. The dominant residual failure is missing evidence, not wrong reasoning

Across the committed 500-question log, `engram_lean` gets 82 wrong. **33 of those 82 (40.2%, 6.6 points
of the total score) are the same failure: the question is answerable and the system says "I don't
know".** Not a wrong answer — no answer.

| category | wrong | of which abstain-on-answerable |
|---|---:|---:|
| temporal-reasoning | 24 | 15 |
| multi-session | 28 | 5 |
| single-session-preference | 8 | 5 |
| single-session-user | 8 | **5 — every error in the category** |
| knowledge-update | 10 | 2 |
| single-session-assistant | 4 | 1 |
| **total** | **82** | **33** |

The qid list is committed at `results/abstain_on_answerable_qids.json` with its selection criterion, so
the set is re-derivable.

`--verify-retry` ("on an abstention, widen the slice and retry once") is implemented and aimed exactly
at this, and appears **nowhere** in RESULTS.md, CHANGELOG.md, or any experiments file — it had never been
measured. It has now been, over exactly these 33 questions
(`eval/competitors/run_verify_retry_ablation.sh`, logs `results/vr_ablation_{off,on}.jsonl`):

| | OFF (shipped `engram_lean`) | ON (`--verify-retry`) |
|---|---:|---:|
| score on the 33-question abstention set | 36.4% (12/33) | **48.5% (16/33)** |
| p50 latency | 55.9s | 109.9s |
| p95 latency | 94.2s | 228.1s |
| avg context | 8,090 words | 8,184 words |
| errors | 0 | 0 |

Per question: **7 wins, 3 losses, net +4.**

The three losses are the interesting part, because `--verify-retry` cannot have caused them. The retry
fires only when the base answer is an abstention, and it substitutes only when the *retried* answer is
not an abstention — a question the OFF arm answered correctly never enters that path. Yet all three
losses are questions where OFF answered correctly and ON returned "I don't know". They are run-to-run
variance, and they put a number on it: **on this exact comparison, noise alone flips about three
questions in the wrong direction at n=33.**

So the honest reading is *suggestive, not established*. A net +4 barely clears a ±3 noise band, and some
of the seven wins are presumably noise in the favourable direction for the same reason. Scaled to the
whole benchmark the ceiling is modest — 4 more questions out of 500 is **+0.8 points** — bought with
doubled latency on the questions that trigger it (only ~33/500 do, so the aggregate latency cost is
small). Establishing it needs the full 500, or repeated runs on this subset.

This is also the cleanest demonstration in the repo of why single-run per-question outcomes should not
be trusted, and why `engram_lean` is deliberately duplicated across both scoreboard processes.

### The other half of the errors is the same problem wearing a different hat

Classifying all 82 errors, not just the abstentions:

| shape | n | note |
|---|---:|---|
| abstain on an answerable question | 33 | evidence never reached the context |
| answered, short numeric, off by ≤1 or ≤10% | 14 | **11 of the 14 are off by exactly ±1**, 10 are multi-session |
| answered, wrong in some other way | 31 | includes 3 `_abs` questions answered instead of abstained |
| abstained on an `_abs` question but still scored wrong | 1 | |
| **total** | **82** | 16.4% of 500 |

The off-by-one column is the tell. `3` vs `4`, `2` vs `3`, `2` vs `3`, `3` vs `4`… and the sign is
mostly negative — undercounting. A counting question answered `3` when the truth is `4` is not a
reasoning failure; it is one evidence session that never made it into the slice. Same root cause as the
abstentions, one notch less severe: there the slice was empty enough to force an "I don't know", here it
was just short by one item.

**Together that is 47 of 82 errors — 57% of everything still wrong, 9.4 points of the total score — and
all of it is evidence coverage, not reasoning and not the answerer.** For a system already at 83.6%,
that is where the next several points live.

### And the coverage is lost *after* retrieval, not by it

"Evidence coverage" has two very different causes with two very different price tags: the retriever
never surfaced the evidence, or it surfaced it and something downstream threw it away. `recall_audit.py`
separates them — embed every haystack session with the same bge-small the harness uses, rank against the
question, and ask where the gold session lands (`results/recall_audit.json`):

| | failing set (the 33 abstentions) | random control (33 others) |
|---|---|---|
| median rank of the gold session | **1** | 0 |
| recall@8 | 72.7% | **100%** |
| recall@15 | 78.8% | **100%** |
| recall@28 | **90.9%** | **100%** |

The pipeline extracts facts from the top `--extract-k 8` sessions and summarizes the top `--summ-k 28`.
So on the questions where Engram answers *"I don't know"*:

- the gold session sits at **median rank 1** — this is not a ranking problem;
- it is inside the summarized set **30 times out of 33**, and the answer still did not survive;
- only **3 of 33 (9%)** are genuine retrieval misses, with the evidence outside the top 28.

**Roughly thirty of the thirty-three abstentions are content that retrieval found and the pipeline then
discarded.** That is the `e47becba` identity-attribute failure generalized: extraction and summarization
compress a session toward its topic, and a fact that is not what the session is *about* does not make it
out the other side.

The spending implication is the opposite of the intuitive one. Better retrieval, better ranking, a bigger
`topk` — none of those buy much here; the retriever is already at median rank 1 and 100% on ordinary
questions. The points are in **what happens to a session after it is retrieved**: extraction coverage,
summary faithfulness, context packing. `identity_attribute_extraction` (§5) is the first patch on that
line, not the whole of it.

*Caveat:* the audit ranks whole-session embeddings by plain cosine, which is a proxy for Engram's actual
read path (same embedder, but the real path adds hybrid scoring, the evidence planner, and expansion).
The proxy can only understate what the real retriever surfaces, so the conclusion — the evidence was
available and got dropped — holds a fortiori.

## 5. A competitor-visible recall defect: identity attributes get summarized away

The sharpest signal a scoreboard produces is a question a *competitor* answers and we abstain on. The
evidence is provably reachable, so the miss is ours.

`e47becba` — *"What degree did I graduate with?"*, gold **Business Administration**. mem0 answered it
correctly from a 172-word context. `engram_lean` answered `"I don't know"` from 7,177 words.

`eval/competitors/why_missed.py` rebuilds the lean context and checks evidence containment. The gold
string is **not in the context at all** — so the abstention was the correct behaviour given what
retrieval supplied. The defect is upstream, and it is not the obvious one:

- The evidence session **was retrieved.** Its summary is in the context.
- It **was fact-extracted.** `user intends to try mint_and_personal_capital`, in the FACTS block, comes
  from this very session.
- What survived is what the *assistant* said — `"Paperwork/expense tips: create filing system, digitize
  docs, use Expensify…"`. The user's own first-person sentence, *"I graduated with a degree in Business
  Administration"*, was dropped.
- `IDENTITY & ANCHORS` in the built profile lists binoculars, a metal desk and chair, running shoes. No
  education. No occupation.

Root cause: extraction and session summarization are organized around the session's *topic and intent*.
A durable identity attribute mentioned in passing, inside a session about something else, is compressed
out. mem0 wins *this* question structurally — it extracts atomic facts per message, with no topical
summarizer in between to discard them.

Worth holding both facts at once: mem0 scores **22.5% to Engram's 72.5%** on the neutral scoreboard (§6).
Its per-message atomic extraction preserves stray facts that Engram's topic-shaped pipeline throws away,
and that same design gives it a ~230-word context that cannot answer most questions. The lesson is not
"be more like mem0" — it is that Engram is paying a recall tax for a compression step mem0 never does,
and that tax is worth attacking without giving up the context that wins everywhere else.

The extraction prompt (`engram/consolidate/llm_extractor.py`, `EXTRACT_SYSTEM`) does say "identities,
attributes", but it *enumerates* only preferences and procedures — "Capture PREFERENCES explicitly and
completely… For each preference or dislike stated, output a SEPARATE fact", plus a runbook clause.
Education, credentials, and occupation are named nowhere. The prompt's own preference clause is evidence
that enumeration is what works, and `explicit_preference_extraction` is the existing precedent in
`engram/config.py` for adding a gated extraction channel for an under-captured attribute class.

### Fix: `identity_attribute_extraction`, implemented and off by default

`Config.identity_attribute_extraction` (default **False**) adds an enumerated identity clause to the
extraction prompt — `degree_in`, `studied_at`, `graduated_in`, `major_in`, `certified_in`, `occupation`,
`job_title`, `employer`, `works_at`, `based_in`, `born_in`, `speaks` — inserted *before* the prompt's
"Do NOT infer or invent facts" guard so that guard keeps the last word. Wired through
`engram/consolidate/engine.py`; exposed for A/B as the `engram_lean_identity_attrs` bench system.

Default-off is deliberate: with the flag off, `extract_system()` returns the existing prompt byte for
byte, so every comparison already in this repo — including the scoreboard runs in flight — is unchanged.
418/418 tests pass.

Mechanism check on the traced question (`why_missed.py e47becba`):

| | shipped `engram_lean` | `engram_lean_identity_attrs` |
|---|---|---|
| gold string verbatim in context | ✗ | **✓** |
| gold content words in context | 1/2 | **2/2** |
| evidence sentence in context | ✗ | **✓** |
| context size | 6,397 words | 7,831 words (+22%) |

The evidence now reaches the context, and the +22% context cost is the thing to watch. One question is
a mechanism check, not a result — per-question outcomes on this harness move on their own (§3). The
real test is the A/B over the 33-question set: `eval/competitors/run_identity_attrs_ablation.sh`, which
compares against an OFF arm measured in the same window under the same judge, not against the June log.

Note the same miss appears in OpenViking: a `grep` of its whole workspace after ingesting this question
finds the gold sentence **only in the raw session archive**, never lifted into its memory tree. This is
an industry-wide blind spot in topic-organized extraction, not an Engram quirk.

## 6. Head-to-head results

In flight. Two runs over the same seeded, category-stratified 60-question slice of LongMemEval_S
(`results/scoreboard_slice_n60.json`): `engram_lean + mem0` and `engram_lean + hy_memory`, one answerer
and one judge across all of them. Raw logs accumulate in `results/scoreboard_{mem0,hy}_n60.jsonl` and can
be reported at any point with `eval/report.py`, which prints denominators.

They run **sequentially**. Started in parallel, mem0's in-process torch embedder saturated all ten cores
and the local bge service on Hy-Memory's write path degraded from 0.33s to 5.9s per embedding — an 18×
penalty landing in the competitor's column, and slower in wall-clock overall. Hy-Memory runs first; the
mem0 run resumes from its existing 16 rows afterwards.

### Engram vs Hy-Memory — full 60-question slice, complete and validated

`results/scoreboard_hy_n60.jsonl`, 60/60 scored, `validate_results.py` OK.

| system | overall | avg context | p50 latency | errors |
|---|---:|---:|---:|---:|
| `engram_lean` | **78.3%** (60) | 7,913 w | 41.0s | 0 |
| `hy_memory` (ultra) | **23.3%** (60) | 719 w | 1,249s | 0 |

| category | Engram | Hy-Memory | n |
|---|---:|---:|---:|
| temporal-reasoning | **93.3%** | 6.7% | 15 |
| multi-session | 71.4% | 7.1% | 14 |
| single-session-assistant | **100%** | 14.3% | 7 |
| knowledge-update | 77.8% | 44.4% | 9 |
| single-session-user | 50.0% | 12.5% | 8 |
| abstention | 100% | 100% | 4 |
| single-session-preference | **33.3%** | **66.7%** | 3 |

Two cells run against Engram and both are worth chasing rather than burying:

- **Preference is lost, again.** 1/3 here, tied at 66.7% in the mem0 run, and the lowest category
  (73.3%) in the published 500. Three independent looks, same direction. n=3 proves nothing by itself;
  three concordant sources make it the first place to look.
- **single-session-user is 50.0% (4/8) against 87.5% in the published run.** That is a 37-point drop on a
  category that should be easy, far larger than slice noise comfortably explains. Either these eight
  questions are unusually hard or something regressed; it needs its own check, not a hand-wave.

knowledge-update at 44.4% is Hy-Memory's only respectable column, which is consistent with a dual-system
design that invests specifically in belief revision.

**A silent cap ate the first attempt, and it is worth recording.** `bench.py` defaults to `--limit 40`
and applies it *after* `--qid` filtering, which the runner did not override. The log printed
`QID filter: 60 of 60 requested items` on one line and `UNIFIED RIG | 40 items` on the next. The 40
scored questions were valid; the slice simply wasn't the one claimed. `--limit 0` is now pinned in
`run_scoreboard.sh` with a comment, and a resume pass completed both runs to 60. This is exactly the
silent-truncation failure the repo's own discipline warns about, and it survived because the log header
stopped being read once the rig was known to work.

### The noise floor, measured — and it splits in two

`engram_lean` ran in both processes over the same 40 questions on purpose. The result is the most useful
methodological number in this repo:

| n | run A (with hy_memory) | run B (with mem0) | aggregate gap | per-question agreement |
|---:|---:|---:|---:|---:|
| 16 | 62.5% | 75.0% | 12.5 pt | — |
| 40 | 72.5% | 72.5% | 0.0 pt | 32/40 = 80.0% |
| **60** | **78.3%** | **76.7%** | **1.7 pt** | **51/60 = 85.0%** |

Same code, same config, same questions, `temperature=0.0` throughout. Two conclusions that must not be
collapsed into one:

- **Aggregate scores converge.** 12.5 points apart at n=16, within 1.7 points at n=60. Flips are roughly
  symmetric, so they cancel in the mean as the sample grows.
- **Individual outcomes do not.** 9 of 60 questions (15%) landed differently between the two runs, spread
  across categories — 3 multi-session, 3 temporal-reasoning, and one each in preference,
  single-session-user and single-session-assistant.

The second bullet is the one with teeth, because per-question analysis is exactly how memory systems get
debugged. **Any "we fixed these N failing questions" claim taken from a single run is chasing noise in
roughly one case in six** — including the 33-question set this file uses in §3 and §4, which is why §3
declines to attribute anything to the twenty post-log commits. Error analysis on this harness needs
repeated runs before a per-question verdict means anything.

The practical rule this yields: **aggregate at n≥40, never per-question at any n.** Engram's ~55-point
lead over both competitors clears the 1.7-point aggregate band by more than thirty times and is not in
question. Its own absolute score is sound in aggregate and unusable question by question.

### Neither competitor was starved

- **Hy-Memory returned a real context on every question** (214–1,200 words, none empty) and answered
  *"I don't know"* on 11 of the first 16. Its memories genuinely do not contain the answer; this is a
  recall result, not a harness artifact.
- **Its latency is its own.** Under CPU contention the median was 1,771s; running alone, 1,169s (fastest
  654s). Even uncontended that is roughly **20 minutes per question** against Engram's ~46 seconds.

That cost buys something specific, and it does not pay off. Hy-Memory extracts **all ~50 sessions at
write time**; Engram fact-extracts only the top-8 retrieved and summarizes the top-28. Exhaustive
write-time extraction did not produce better recall — it produced 20.0% at roughly 20× the ingestion
cost. Which sharpens §5: Engram's extraction problem is not that it extracts from too few sessions, it
is *what it keeps* from the ones it does.

### Engram vs mem0 — full 60-question slice, complete

`results/scoreboard_mem0_n60.jsonl`.

| system | overall | avg context | p50 latency | errors |
|---|---:|---:|---:|---:|
| `engram_lean` | **76.7%** (60) | 7,892 w | 43.5s | 0 |
| `mem0` 0.1.93 | **20.3%** (59) | 234 w | 203s | 1 |

mem0's single error is a local `NotImplementedError: Cannot copy out of meta tensor` — a torch model-load
failure on this machine under memory pressure, not a mem0 defect. That question is unscored, hence the
59 denominator.

Same shape as the Hy-Memory run: multi-session 78.6% vs 14.3% (n=14), single-session-assistant 85.7% vs
0% (n=7), temporal-reasoning 73.3% vs 6.7% (n=15). They tie at 66.7% on the three preference questions.

**Discount mem0's temporal column.** `mem0.Memory.add()` in 0.1.93 takes no timestamp, so mem0 alone
ingested without real session dates (fairness ledger). 6.7% on temporal-reasoning is a floor, not a
measurement, and temporal-reasoning is 15 of the 60 questions here.

### The neutral scoreboard — final, 60-question stratified slice

| system | score | context | p50 latency |
|---|---:|---:|---:|
| **Engram** (`engram_lean`) | **78.3%** / **76.7%** (two independent runs) | 7.9k words | 41–44s |
| Tencent Hy-Memory 1.2.16 (`ultra`) | 23.3% (60) | 0.72k words | 1,249s |
| mem0 0.1.93 | 20.3% (59) | 0.23k words | 203s |

One answerer, one judge, one question set, competitors configured in their own favour wherever there was
a choice, raw logs committed and `validate_results.py`-checked. **Engram scores roughly 3.3× both
competitors.** It also spends 11× the context of Hy-Memory and 34× that of mem0 — the honest cost side,
and the reason the token column travels next to the accuracy column everywhere in this repo. On latency
Engram is not the expensive one: 41s against Hy-Memory's ~21 minutes per question.

The margin is ~55 points. The measured aggregate noise band at this n is 1.7 points (below). The
conclusion is not close to the noise.

For scale: Hy-Memory's published LongMemEval claim is **85.2%** on their own harness. Here — in their
strongest `ultra` mode, with System-2 flushed via a route added for them, and their embedder upgraded
from bge-small-zh to the same bge-small-en Engram uses — it scores **23.3%**. That is not evidence their
number was fabricated: subset, judge, and answerer choices move these figures enormously, which is
exactly the problem a neutral scoreboard exists to solve. It does mean 85.2% is not comparable to
anything measured here, and neither is any other vendor-reported figure.

Both competitors landing on exactly 18.8% is coincidence, not a shared cause — their failure shapes
differ, and neither is starved:

- **Hy-Memory returned a real context on all 16 questions** (214–1,200 words, none empty) and answered
  *"I don't know"* on 11 of them. Its memories genuinely do not contain the answer; this is a recall
  result, not a harness artifact.
- **Latency is theirs, not the machine's.** Under contention the median was 1,771s; running alone it is
  **1,169s** (fastest 654s). So even uncontended, Hy-Memory needs roughly **20 minutes per question**
  against Engram's ~58 seconds — about 20× the ingestion cost.

That cost buys something specific, and the interesting part is that it doesn't pay off. Hy-Memory
extracts **every one of the ~50 sessions at write time**; Engram fact-extracts only the top-8 retrieved
and summarizes the top-28. Exhaustive write-time extraction did not produce better recall — it produced
18.8% at 20× the cost. Which sharpens §5: Engram's extraction problem is not that it extracts from too
few sessions, it is *what it keeps* from the sessions it does extract.

Read accuracy next to the token column, never alone. Equal `--topk 15` buys ~250 words from mem0,
~850–1000 from Hy-Memory, and ~8,000 from Engram — these engines return different *kinds* of thing, and
a system a few points behind at a thirtieth of the context has not obviously lost.

Two competitor caveats are load-bearing and are stated in the fairness ledger, not buried: mem0 0.1.93
has no timestamp argument on `add()`, so it alone receives no real session dates and its
`temporal-reasoning` column is a floor rather than a measurement; and Hy-Memory was run on its shipped
default reader (`legacy`) with `hybrid_v2` / `exhaustive` untested.
