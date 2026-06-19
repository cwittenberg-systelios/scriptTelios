#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_context_eval.sh — Context-Bump-Experiment (EINE Variable: num_ctx)
#
# Faehrt qwen3:32b ZWEIMAL: einmal mit num_ctx-Cap 16384 (Baseline), einmal mit
# 32768 — gleiches Modell, gleicher Seed, gleiche (gecachte) Transkripte. Der
# EINZIGE Unterschied ist das Kontextfenster. Zeigt isoliert, ob die laengeren
# Inputs (die bei 16384 von Ollama still gekuerzt werden) die Output-Qualitaet
# heben (Absatzdichte / Stub-Outputs).
#
# WICHTIG: 32768 + 32B-Gewichte + KV-Cache ist auf 32GB machbar (q8_0-KV, in
# 'ollama ps' bereits gesehen), aber enger bei langem Input. Bei OOM faellt der
# Backend per Retry auf num_ctx=8192 zurueck -> dann ist der 32768-Lauf wertlos.
# Darum unten der OOM-Check im run-Log.
#
# Nutzung:  chmod +x run_context_eval.sh && ./run_context_eval.sh   (in tmux!)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

RESULTS=/workspace/eval_results/context
BE=/workspace/scriptTelios/backend
VENV=/workspace/venv
MODEL="qwen3:32b"

mkdir -p "$RESULTS" /workspace/logs
export OLLAMA_MODEL="$MODEL"
export LLM_SEED=42                      # fester Seed -> einzige Variable ist num_ctx

start_backend() {
  ( cd "$BE" && source "$VENV/bin/activate" \
    && nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 \
       >> /workspace/logs/backend.log 2>&1 & )
}
wait_port_free() {
  for _ in $(seq 1 30); do curl -sf localhost:8000/api/health >/dev/null 2>&1 || return 0; sleep 1; done
  echo "WARN: Port 8000 noch belegt"
}
wait_healthy() {
  for _ in $(seq 1 60); do curl -sf localhost:8000/api/health >/dev/null 2>&1 && return 0; sleep 2; done
  return 1
}

ollama list | grep -q "$MODEL" || ollama pull "$MODEL"

# CAP=16384 zuerst (Baseline) + Transkripte cachen; dann CAP=32768 (reuse Cache).
first=1
for CAP in 16384 32768; do
  echo "=== num_ctx-Cap: $CAP ==="
  export LLM_NUM_CTX_CAP="$CAP"         # Setting -> friert beim Prozessstart ein -> Neustart noetig

  pkill -f "uvicorn app.main:app"; wait_port_free
  start_backend
  if ! wait_healthy; then
    echo "Backend (cap=$CAP) nicht healthy -> skip"; continue
  fi

  TR=""; [ "$first" = "1" ] && TR="--transcribe"
  ( cd "$BE" && source "$VENV/bin/activate" \
    && pytest tests/eval/test_eval.py -v --tb=short --qa --eval-report $TR \
       --eval-output "$RESULTS/ctx_$CAP" > "$RESULTS/run_ctx_$CAP.log" 2>&1 )
  echo "  pytest exit=$?  -> $RESULTS/run_ctx_$CAP.log"

  # Bei 32768: pruefen, dass NICHT auf 8192 zurueckgefallen wurde (sonst wertlos).
  grep -iqE "out of memory|CUDA error|cannot allocate|num_ctx=8192" "$RESULTS/run_ctx_$CAP.log" \
    && echo "  !! OOM/Fallback im Log — der $CAP-Lauf ist nicht aussagekraeftig"

  first=0
done

ollama stop "$MODEL" 2>/dev/null || true
pkill -f "uvicorn app.main:app" 2>/dev/null || true
echo "=== fertig. Vergleich: $RESULTS/ctx_16384 vs $RESULTS/ctx_32768 ==="
echo "    Eval-Report-PDFs + .qa.json je Lauf nebeneinander legen."
