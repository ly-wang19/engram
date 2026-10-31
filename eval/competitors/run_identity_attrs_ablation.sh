#!/usr/bin/env bash
# A/B the identity-attribute extraction channel on the questions that motivated it.
#
# The defect (COMPARISON.md §5): the extraction prompt names "identities, attributes" but only
# ENUMERATES preferences and procedures, and enumeration is what the extractor acts on. A durable
# identity attribute stated in passing inside an off-topic session is dropped — traced on qid e47becba,
# where the extractor pulled an *intent* out of a session and left "I graduated with a degree in
# Business Administration" in the same session unextracted, and mem0 answered the question we abstained
# on.
#
# Test set: results/abstain_on_answerable_qids.json — the 33 questions where the committed 500-run
# abstains on an answerable question. The OFF arm here is the same file the verify-retry ablation
# produced (results/vr_ablation_off.jsonl), so run that first, or let this script re-create it.
#
#   bash eval/competitors/run_identity_attrs_ablation.sh
#
# Interpretation guard: these 33 were selected *because* they failed, so regression to the mean alone
# moves the number. The comparison that means something is ON vs the OFF arm measured in the same
# window with the same judge — not ON vs the committed June log.

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
        --workers 2)

if [ ! -s results/vr_ablation_off.jsonl ]; then
  echo "[ia] no OFF arm yet — producing one"
  # shellcheck disable=SC2086
  python3 eval/bench.py "${COMMON[@]}" $QIDS --systems engram_lean \
      --out results/vr_ablation_off.jsonl > results/vr_ablation_off.log 2>&1
fi

# shellcheck disable=SC2086
python3 eval/bench.py "${COMMON[@]}" $QIDS --systems engram_lean_identity_attrs \
    --out results/identity_attrs_on.jsonl > results/identity_attrs_on.log 2>&1
echo "[ia] ON arm done"

echo "--- OFF (engram_lean, shipped) ---"; python3 eval/report.py results/vr_ablation_off.jsonl
echo "--- ON  (identity attrs) ---";       python3 eval/report.py results/identity_attrs_on.jsonl
echo "--- per-question diff ---"
python3 eval/compare.py results/vr_ablation_off.jsonl results/identity_attrs_on.jsonl

echo "--- direct evidence check on the traced case ---"
python3 eval/competitors/why_missed.py e47becba --system engram_lean_identity_attrs 2>/dev/null \
  | grep -E "GOLD|gold string|gold content|that sentence"
