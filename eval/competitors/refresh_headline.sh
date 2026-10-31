#!/usr/bin/env bash
# Re-measure the headline on the CURRENT code, full 500 questions, under a judge that still exists.
#
# Why: the log behind RESULTS.md's 83.6% (results/longmemeval_s_engram_lean_v2_final.jsonl) is dated
# 2026-06-04. Roughly twenty retrieval and extraction commits landed between 2026-06-29 and 2026-07-09
# on top of it — graph anchoring, evidence budgeting, planner chains, the three aggregation changes,
# preference and procedural extraction, provenance chunk promotion — and none of them are reflected in
# the published number. Meanwhile the documented reproduce command fails outright, because ARK retired
# the judge it names (RESULTS.md → "Endpoint drift").
#
# So the flagship figure is two months and twenty improvements stale, and nobody can currently check it.
# Measured hint that it is an *under*statement: on the 33 questions the June log records as abstained,
# the current code answers 12 correctly with no configuration change (COMPARISON.md §3).
#
#   bash eval/competitors/refresh_headline.sh              # engram_lean only, 500q
#   bash eval/competitors/refresh_headline.sh with-baseline # + full_context, for the +10.4pt claim
#
# Cost: this is real API spend on ARK (answerer + extractor) and DeepSeek (judge) — roughly 500 answer
# calls, ~25k extraction calls, 500 judge calls. Run it when the machine is otherwise idle: mem0's torch
# embedder or a concurrent competitor run will starve it (see README "Validity checks").
#
# Discipline this run must keep, or the number is not a headline number (RESULTS.md's own rule):
#   - full 500, not a slice;
#   - identical flags to the published configuration, listed below and unchanged;
#   - no --verify-retry and no --strategies (neither was in the published run);
#   - validate the log before citing it.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

set -a; . ./.env; set +a
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

SYSTEMS="engram_lean"
OUT="results/headline_refresh_$(python3 -c 'import time;print(time.strftime("%Y%m%d"))').jsonl"
if [ "${1:-}" = "with-baseline" ]; then
  SYSTEMS="engram_lean,full_context"
fi

echo "[refresh] systems=$SYSTEMS -> $OUT"
echo "[refresh] judge is DeepSeek v4-flash, NOT the retired ARK deepseek-v3-2 behind 83.6% —"
echo "[refresh] the new number replaces the old one, it does not get compared to it."

python3 eval/bench.py \
  --data s --limit 500 --systems "$SYSTEMS" \
  --answerer volcano:doubao-seed-2-0-pro-260215 \
  --judge deepseek \
  --extractor volcano:doubao-seed-1-6-flash-250615 \
  --embedder bge-small --reasoning --persona \
  --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 \
  --workers 6 --resume --out "$OUT" \
  > "${OUT%.jsonl}.log" 2>&1

echo "[refresh] done. report:"
python3 eval/report.py "$OUT"
echo "[refresh] validate before citing:"
python3 eval/validate_results.py --expected-rows 500 --require-complete \
  $(for s in ${SYSTEMS//,/ }; do printf -- "--system %s " "$s"; done) "$OUT"
