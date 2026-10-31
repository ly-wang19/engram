#!/usr/bin/env bash
# The neutral scoreboard: Engram vs shipped competitor memory engines, same questions, same answerer,
# same judge, logs committed.
#
# Why two processes instead of one --systems list: eval_item() runs a question through its systems
# SEQUENTIALLY, so one list makes every question wait for the slowest engine. Competitor ingestion
# dominates (mem0 ~400s/question, Hy-Memory ~1000s/question, Engram ~65s), so the two comparisons run
# side by side and are merged afterwards with eval/compare.py.
#
# engram_lean is in BOTH runs on purpose. Each run is then a strict single-process comparison, and the
# two engram_lean columns are a free consistency check: they are the same system on the same questions,
# so a gap between them is judge/answerer nondeterminism and bounds how much of any head-to-head margin
# is noise.
#
#   bash eval/competitors/run_scoreboard.sh 40          # 40-question stratified slice
#   bash eval/competitors/run_scoreboard.sh 40 resume   # continue an interrupted slice
#
# Prereqs: `bash eval/competitors/run_hy_memory.sh` (engine up), .env with ARK_API_KEY + DEEPSEEK_API_KEY.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

N="${1:-40}"
RESUME_FLAG=""
[ "${2:-}" = "resume" ] && RESUME_FLAG="--resume"

set -a; . ./.env; set +a
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

# Same judge/answerer/extractor for every system in every run — the harness's one hard guarantee.
# NOTE: the judge is DeepSeek's own API, not the retired ARK deepseek-v3-2 (see RESULTS.md "Endpoint
# drift"), so these numbers are internally comparable but NOT comparable to the committed 83.6%.
ANSWERER="volcano:doubao-seed-2-0-pro-260215"
JUDGE="deepseek"
EXTRACTOR="volcano:doubao-seed-1-6-flash-250615"
# --limit 0 disables the cap. bench.py defaults to --limit 40 and applies it AFTER --qid filtering, so
# without this a 60-qid slice is silently stride-sampled down to 40 — the log says "QID filter: 60 of 60
# requested items" on one line and "UNIFIED RIG | 40 items" on the next. Cost us a run; do not remove.
COMMON=(--data s --limit 0 --answerer "$ANSWERER" --judge "$JUDGE" --extractor "$EXTRACTOR"
        --embedder bge-small --reasoning --persona
        --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28)
# Deliberately NOT passed: --verify-retry (engram_lean only would get a second attempt) and
# --strategies (routes answering per question shape). Both are Engram-side advantages the competitors
# cannot receive, so they are off for every system here.

QIDS=$(python3 eval/competitors/stratified_slice.py --data s --n "$N" --args)
[ -z "$QIDS" ] && { echo "FATAL: stratified slice returned nothing"; exit 1; }
python3 eval/competitors/stratified_slice.py --data s --n "$N" > "results/scoreboard_slice_n${N}.json"
echo "[scoreboard] slice: results/scoreboard_slice_n${N}.json"

run() {  # run <tag> <systems> <workers>
  local tag="$1" systems="$2" workers="$3"
  echo "[scoreboard] launching $tag: $systems (workers=$workers)"
  # shellcheck disable=SC2086
  nohup python3 eval/bench.py "${COMMON[@]}" $QIDS \
      --systems "$systems" --workers "$workers" $RESUME_FLAG \
      --out "results/scoreboard_${tag}_n${N}.jsonl" \
      > "results/scoreboard_${tag}_n${N}.log" 2>&1 &
  echo "$!" > "results/scoreboard_${tag}_n${N}.pid"
}

# SEQUENTIAL, not parallel. mem0's in-process torch embedder saturates every core, and the local bge
# service that each Hy-Memory write depends on then answers in 5.9s instead of 0.33s — an 18x penalty
# that lands in the competitor's latency column and makes the whole run slower in wall-clock too. Size
# the parallelism by the local bottleneck, not by what the remote API can absorb.
run hy "engram_lean,hy_memory" 4
wait "$(cat "results/scoreboard_hy_n60.pid" 2>/dev/null)" 2>/dev/null || true
while [ -s "results/scoreboard_hy_n${N}.pid" ] && ps -p "$(cat "results/scoreboard_hy_n${N}.pid")" >/dev/null 2>&1; do
  sleep 60
done
RESUME_FLAG="--resume"   # mem0 may already have rows from an earlier attempt
run mem0 "engram_lean,mem0" 4

echo "[scoreboard] both runs detached. watch with:"
echo "  tail -f results/scoreboard_hy_n${N}.log"
echo "when both finish:"
echo "  python eval/report.py results/scoreboard_mem0_n${N}.jsonl"
echo "  python eval/report.py results/scoreboard_hy_n${N}.jsonl"
echo "  python eval/compare.py results/scoreboard_mem0_n${N}.jsonl results/scoreboard_hy_n${N}.jsonl"
