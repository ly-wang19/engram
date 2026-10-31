#!/usr/bin/env bash
# Bring up Tencent Hunyuan's Hy-Memory engine in the EXACT configuration the neutral scoreboard
# compares against Engram on LongMemEval (English).
#
# Why this script exists: a competitor number is only citable if the competitor's setup is written
# down and re-runnable. Everything that could tilt the comparison is pinned here.
#
# Parity with Engram's own run (see RESULTS.md "Exact setup"):
#   embedder  = BAAI/bge-small-en-v1.5  (384-dim, local, no key)  -- SAME model as Engram
#   extractor = doubao-seed-1-6-flash   (Hy-Memory's internal LLM) -- SAME model as Engram's extractor
# The stock Hy-Memory deployment ships bge-small-ZH; on an English benchmark that would hand us an
# unearned win, so the English embedder is forced here and the dims changed 512 -> 384 to match.
#
#   bash eval/competitors/run_hy_memory.sh          # start (idempotent)
#   bash eval/competitors/run_hy_memory.sh stop     # stop
#
# Requires the local Hy-Memory checkout + venv (external to this repo):
#   HY_ROOT=/path/to/hy-memory-cc   (venv/ with `hy-memory` installed, embed_server.py)

set -euo pipefail

HY_ROOT="${HY_ROOT:-/Users/ywwl/Documents/Claude/Projects/记忆/hy-memory-cc}"
ENGRAM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# System python has sentence-transformers/torch; the venv python has hy-memory. They are different.
SYS_PY="${SYS_PY:-/Library/Frameworks/Python.framework/Versions/3.12/bin/python3}"
VENV_PY="$HY_ROOT/venv/bin/python"

EMBED_PORT="${EMBED_PORT:-8901}"          # 8901, not the stock 8900: the zh server may own that one
ENGINE_PORT="${ENGINE_PORT:-19527}"
RUN_DIR="$ENGRAM_ROOT/data/hy_memory_lme"
mkdir -p "$RUN_DIR"

if [ "${1:-start}" = "stop" ]; then
  pkill -f "hy_memory.server --port $ENGINE_PORT" 2>/dev/null || true
  pkill -f "embed_server.py" 2>/dev/null || true
  echo "[hy] stopped"
  exit 0
fi

[ -x "$VENV_PY" ] || { echo "[hy] FATAL: no hy-memory venv at $VENV_PY (set HY_ROOT)"; exit 1; }
set -a; . "$HY_ROOT/.env"; set +a          # ARK_API_KEY / ARK_BASE_URL

# 1) English embedding service (bge-small-en-v1.5, 384-dim) ---------------------------------------
if ! curl -sf --max-time 2 "http://127.0.0.1:$EMBED_PORT/health" >/dev/null 2>&1; then
  echo "[hy] starting EN embedding service :$EMBED_PORT ..."
  EMBED_MODEL="BAAI/bge-small-en-v1.5" EMBED_PORT="$EMBED_PORT" \
    nohup "$SYS_PY" "$HY_ROOT/embed_server.py" > "$RUN_DIR/embed_en.log" 2>&1 &
  curl -s --retry 60 --retry-delay 1 --retry-connrefused --max-time 120 \
    "http://127.0.0.1:$EMBED_PORT/health" >/dev/null
fi
echo "[hy] embedder: $(curl -s "http://127.0.0.1:$EMBED_PORT/health")"

# 2) Hy-Memory engine ------------------------------------------------------------------------------
export MEMORY_MODE="${MEMORY_MODE:-ultra}"        # ultra = System2 + Kuzu graph = their strongest mode
export MEMORY_ENABLE_SEARCH_QUERY=false
export MEMORY_CACHE_BACKEND=sqlite
export MEMORY_ENABLE_SUMMARY=true                 # L3 summaries on: give them their full stack
export MEMORY_GRAPH_PROVIDER=kuzu
export MEMORY_GRAPH_DB_PATH="$RUN_DIR/kuzu_db"
export MEMORY_LLM_PROVIDER=openai
export MEMORY_LLM_MODEL="${MEMORY_LLM_MODEL:-doubao-seed-1-6-flash-250615}"   # == Engram's extractor
export MEMORY_LLM_API_KEY="$ARK_API_KEY"
export MEMORY_LLM_BASE_URL="$ARK_BASE_URL"
export MEMORY_EMBEDDER_PROVIDER=openai
export MEMORY_EMBEDDER_MODEL=bge-small-en-v1.5
export MEMORY_EMBEDDER_API_KEY=local-no-key
export MEMORY_EMBEDDER_BASE_URL="http://127.0.0.1:$EMBED_PORT/v1"
export MEMORY_EMBEDDING_DIMS=384                  # bge-small-EN is 384 (the zh model is 512)
export MEMORY_VECTOR_STORE=chroma
export MEMORY_PERSIST_DIR="$RUN_DIR/chroma"

if ! curl -sf --max-time 2 "http://127.0.0.1:$ENGINE_PORT/healthz" >/dev/null 2>&1; then
  echo "[hy] starting engine :$ENGINE_PORT (mode=$MEMORY_MODE) ..."
  cd "$HY_ROOT"
  nohup "$VENV_PY" -m hy_memory.server --port "$ENGINE_PORT" > "$RUN_DIR/engine.log" 2>&1 &
  curl -s --retry 90 --retry-delay 1 --retry-connrefused --max-time 180 \
    "http://127.0.0.1:$ENGINE_PORT/healthz" >/dev/null
fi
echo "[hy] engine:   $(curl -s "http://127.0.0.1:$ENGINE_PORT/healthz")"
echo "[hy] ready. logs in $RUN_DIR/"
