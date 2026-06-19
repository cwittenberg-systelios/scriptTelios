#!/usr/bin/env bash
set -uo pipefail
BE=/workspace/scriptTelios/backend
PY=/workspace/venv/bin/python
PORT=8001
MODEL="qwen3:32b"
RESULTS=/workspace/eval_results/context_quick
LOGS=/workspace/logs
mkdir -p "$RESULTS" "$LOGS"

set -a; [ -f /workspace/.env ] && . /workspace/.env; set +a
export AUTH_ENABLED=false
export LLM_SEED=42 OLLAMA_MODEL="$MODEL" EVAL_BACKEND_URL="http://127.0.0.1:$PORT"

MYPID=""
stop_be(){ [ -n "$MYPID" ] && kill "$MYPID" 2>/dev/null; [ -n "$MYPID" ] && wait "$MYPID" 2>/dev/null; MYPID=""; }
trap stop_be EXIT

for CAP in 16384 32768; do
  echo "=== num_ctx-Cap: $CAP ==="
  export LLM_NUM_CTX_CAP="$CAP"
  ollama stop "$MODEL" 2>/dev/null || true; sleep 2
  ( cd "$BE" && exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 ) \
    >> "$LOGS/eval_backend_$PORT.log" 2>&1 &
  MYPID=$!
  for _ in $(seq 1 60); do curl -sf "127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 2; done

  ( cd "$BE" && "$PY" -m pytest tests/eval/test_eval.py -v --tb=short --qa \
      -k "test_eval_workflow and (dokumentation or entlassbericht or anamnese)" \
      --eval-output "$RESULTS/ctx_$CAP" ) > "$RESULTS/run_ctx_$CAP.log" 2>&1
  echo "  pytest exit=$?  -> $RESULTS/run_ctx_$CAP.log"
  grep -iqE "out of memory|CUDA error|num_ctx=8192" "$RESULTS/run_ctx_$CAP.log" \
    && echo "  !! OOM/Fallback -> $CAP-Lauf unbrauchbar"
  stop_be
done
echo "=== fertig: $RESULTS/ctx_16384 vs ctx_32768 ==="
