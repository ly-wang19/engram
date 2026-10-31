#!/usr/bin/env bash
# A/B the `--verify-retry` path on the questions it was built for.
#
# Why this experiment: in the committed headline log
# (results/longmemeval_s_engram_lean_v2_final.jsonl) 33 of engram_lean's 82 errors — 40.2% of everything
# still wrong, 6.6 points of the total score — are the SAME failure: the question is answerable, the
# system says "I don't know". That is a recall miss, not a reasoning miss.
#
# `--verify-retry` ("on an abstention, widen the slice and retry once") targets exactly that, is
# implemented, and appears nowhere in RESULTS.md, CHANGELOG.md, or any experiments file — it has never
# been measured. This runs both arms over those 33 qids under one judge so the lift is attributable.
#
#   bash eval/competitors/run_verify_retry_ablation.sh
#
# Both arms use the same answerer/judge/extractor as the scoreboard, so the OFF arm also re-establishes
# the baseline under the *current* judge (the committed log was scored by the now-retired ARK deepseek).

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

set -a; . ./.env; set +a
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

QIDFILE="results/abstain_on_answerable_qids.json"
[ -f "$QIDFILE" ] || { echo "FATAL: $QIDFILE missing"; exit 1; }
QIDS=$(python3 -c "import json;print(' '.join('--qid '+q for q in json.load(open('$QIDFILE'))['qids']))")

COMMON=(--data s --answerer volcano:doubao-seed-2-0-pro-260215 --judge deepseek
        --extractor volcano:doubao-seed-1-6-flash-250615 --embedder bge-small
        --reasoning --persona --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28
        --systems engram_lean --workers 2)

# shellcheck disable=SC2086
python3 eval/bench.py "${COMMON[@]}" $QIDS \
    --out results/vr_ablation_off.jsonl > results/vr_ablation_off.log 2>&1
echo "[vr] OFF arm done"

# shellcheck disable=SC2086
python3 eval/bench.py "${COMMON[@]}" $QIDS --verify-retry \
    --out results/vr_ablation_on.jsonl > results/vr_ablation_on.log 2>&1
echo "[vr] ON arm done"

echo "--- OFF ---"; python3 eval/report.py results/vr_ablation_off.jsonl
echo "--- ON  ---"; python3 eval/report.py results/vr_ablation_on.jsonl
echo "--- per-question diff ---"
python3 eval/compare.py results/vr_ablation_off.jsonl results/vr_ablation_on.jsonl
