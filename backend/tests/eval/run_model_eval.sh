#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_model_eval.sh  (v2 — RunPod-sicher)
#
# Modell-A/B: qwen3:32b vs qwen3.6:27b vs qwen3.6:35b-a3b. Gleicher Seed, gleiche
# (gecachte) Transkripte, gleicher num_ctx-Cap -> EINZIGE Variable = Modell.
#
# WICHTIG / Unterschied zu v1:
#   Startet einen EIGENEN Backend auf Port 8001 und killt am Ende NUR dessen PID.
#   KEIN 'pkill -f uvicorn' (das wuerde den Pod-Hauptprozess auf Port 8000
#   killen und den Container neustarten). Port 8000 bleibt unangetastet.
#
# Hinweis: alle Backends reden mit DEMSELBEN Ollama -> immer nur EIN Modell im
# VRAM. 'ollama stop' vor jedem Modell macht den VRAM frei.
#
# Optional gleicher Context-Bump wie im Context-Experiment: LLM_NUM_CTX_CAP unten
# setzen (Default = Pod-.env). Fuer den reinen Modellvergleich konstant lassen.
#
# Nutzung:  ./run_model_eval.sh   (oder setsid ... < /dev/null > driver.log 2>&1 &)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

BE=/workspace/scriptTelios/backend
PY=/workspace/venv/bin/python
PORT=8001
RESULTS=/workspace/eval_results
LOGS=/workspace/logs
MODELS=("qwen3:32b" "qwen3.6:27b" "qwen3.6:35b-a3b")

mkdir -p "$RESULTS" "$LOGS"

export LLM_SEED=42                       # reproduzierbarer A/B
# Pod-Umgebung uebernehmen (DB-URL, OLLAMA_HOST, HMAC-Secret, WHISPER_MODEL,
# AUTH_ENABLED, Pfade ...), damit der eigene Backend exakt wie der Pod-Backend
# laeuft. Ohne das defaultet pydantic u.a. AUTH_ENABLED=True -> 401 auf /generate.
set -a; [ -f /workspace/.env ] && . /workspace/.env; set +a
export AUTH_ENABLED=false               # Eval ist server-to-server (kein Confluence-HMAC-Header)
export EVAL_BACKEND_URL="http://127.0.0.1:$PORT"
# export LLM_NUM_CTX_CAP=32768           # nur wenn du mit Context-Bump vergleichen willst

MYPID=""
start_my_backend() {
  ( cd "$BE" && exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 ) \
    >> "$LOGS/eval_backend_$PORT.log" 2>&1 &
  MYPID=$!
}
stop_my_backend() {
  [ -n "$MYPID" ] && kill "$MYPID" 2>/dev/null
  [ -n "$MYPID" ] && wait "$MYPID" 2>/dev/null
  MYPID=""
}
wait_healthy() {
  for _ in $(seq 1 60); do
    curl -sf "127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}
trap 'stop_my_backend' EXIT

first=1
for M in "${MODELS[@]}"; do
  echo "=== Modell: $M ==="
  ollama list | grep -q "$M" || ollama pull "$M" || { echo "pull $M fehlgeschlagen -> skip"; first=0; continue; }

  export OLLAMA_MODEL="$M"
  ollama stop "$M" 2>/dev/null || true   # VRAM frei vor dem naechsten Modell
  sleep 2
  start_my_backend
  if ! wait_healthy; then
    echo "Eigener Backend fuer $M auf $PORT nicht healthy -> skip"
    tail -20 "$LOGS/eval_backend_$PORT.log"; stop_my_backend; first=0; continue
  fi

  SAFE=$(echo "$M" | tr ':/.' '___')
  ollama ps > "$RESULTS/ollama_ps_$SAFE.txt" 2>&1

  TR=""; [ "$first" = "1" ] && TR="--transcribe"
  ( cd "$BE" && "$PY" -m pytest tests/eval/test_eval.py -v --tb=short --qa --eval-report $TR \
       --eval-output "$RESULTS/$SAFE" ) > "$RESULTS/run_$SAFE.log" 2>&1
  echo "  pytest exit=$?  -> $RESULTS/run_$SAFE.log"
  grep -iqE "out of memory|CUDA error|cannot allocate" "$RESULTS/run_$SAFE.log" \
    && echo "  !! OOM/CUDA-Fehler -> $M-Lauf unbrauchbar (32GB zu eng?)"

  stop_my_backend
  first=0
done

echo "=== fertig: $RESULTS ==="
