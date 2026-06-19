#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_context_eval.sh  (v2 — RunPod-sicher)
#
# Context-Bump-Experiment: qwen3:32b ZWEIMAL (num_ctx-Cap 16384 vs 32768),
# gleicher Seed, gleiche (gecachte) Transkripte -> EINZIGE Variable = num_ctx.
#
# WICHTIG / Unterschied zu v1:
#   Startet einen EIGENEN Backend auf Port 8001 und killt am Ende NUR dessen PID.
#   KEIN 'pkill -f uvicorn' — das wuerde den Pod-Hauptprozess (Port 8000, von
#   runpod-start.sh) treffen und den Container neustarten. Port 8000 bleibt
#   komplett unangetastet.
#
# VORAUSSETZUNG:
#   - Pod-Code unter $BE enthaelt die LLM_NUM_CTX_CAP-Aenderung (aus
#     scriptTelios_changes.zip). Sonst ist LLM_NUM_CTX_CAP wirkungslos -> der
#     Sanity-Check unten bricht dann ab.
#   - Waehrend des Laufs NICHT die App auf Port 8000 benutzen und keine andere
#     Eval/Probe laufen lassen (gemeinsamer Ollama -> Modell wuerde thrashen).
#
# Nutzung (Vordergrund):  ./run_context_eval.sh
# Hintergrund/abgekoppelt: setsid bash run_context_eval.sh < /dev/null \
#                            > /workspace/logs/ctx_driver.log 2>&1 &
#                          tail -f /workspace/logs/ctx_driver.log
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

BE=/workspace/scriptTelios/backend
PY=/workspace/venv/bin/python
PORT=8001
MODEL="qwen3:32b"
RESULTS=/workspace/eval_results/context
LOGS=/workspace/logs

mkdir -p "$RESULTS" "$LOGS"

# --- Sanity: ist die Cap-Aenderung ueberhaupt im Pod-Code? -------------------
if ! grep -q "LLM_NUM_CTX_CAP" "$BE/app/services/llm.py"; then
  echo "FEHLER: $BE/app/services/llm.py kennt LLM_NUM_CTX_CAP nicht."
  echo "        -> scriptTelios_changes.zip auf den Pod deployen, dann erneut starten."
  exit 1
fi

export LLM_SEED=42
export OLLAMA_MODEL="$MODEL"
# Pod-Umgebung uebernehmen (DB-URL, OLLAMA_HOST, HMAC-Secret, WHISPER_MODEL,
# AUTH_ENABLED, Pfade ...), damit der eigene Backend exakt wie der Pod-Backend
# laeuft. Ohne das defaultet pydantic u.a. AUTH_ENABLED=True -> 401 auf /generate.
set -a; [ -f /workspace/.env ] && . /workspace/.env; set +a
export AUTH_ENABLED=false               # Eval ist server-to-server (kein Confluence-HMAC-Header)
export EVAL_BACKEND_URL="http://127.0.0.1:$PORT"

MYPID=""
start_my_backend() {                    # uvicorn auf $PORT, PID -> $MYPID (exec => $! ist uvicorn)
  ( cd "$BE" && exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 ) \
    >> "$LOGS/eval_backend_$PORT.log" 2>&1 &
  MYPID=$!
}
stop_my_backend() {                     # NUR die eigene PID (KEIN pkill)
  [ -n "$MYPID" ] && kill "$MYPID" 2>/dev/null
  [ -n "$MYPID" ] && wait "$MYPID" 2>/dev/null
  MYPID=""
}
wait_healthy() {                        # MIT Timeout (120s)
  for _ in $(seq 1 60); do
    curl -sf "127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}
trap 'stop_my_backend' EXIT            # falls das Skript abbricht: eigenen Backend mitnehmen

ollama list | grep -q "$MODEL" || ollama pull "$MODEL"

first=1
for CAP in 16384 32768; do
  echo "=== num_ctx-Cap: $CAP ==="
  export LLM_NUM_CTX_CAP="$CAP"         # friert beim Backend-Start ein -> eigener Backend pro Cap

  ollama stop "$MODEL" 2>/dev/null || true   # erzwingt sauberen Reload mit neuem Kontext
  sleep 2
  start_my_backend
  if ! wait_healthy; then
    echo "Eigener Backend (cap=$CAP) auf $PORT nicht healthy -> skip"
    tail -20 "$LOGS/eval_backend_$PORT.log"
    stop_my_backend; first=0; continue
  fi

  TR=""; [ "$first" = "1" ] && TR="--transcribe"     # Transkripte nur einmal cachen
  ( cd "$BE" && "$PY" -m pytest tests/eval/test_eval.py -v --tb=short --qa --eval-report $TR \
       --eval-output "$RESULTS/ctx_$CAP" ) > "$RESULTS/run_ctx_$CAP.log" 2>&1
  echo "  pytest exit=$?  -> $RESULTS/run_ctx_$CAP.log"
  grep -iqE "out of memory|CUDA error|cannot allocate|num_ctx=8192" "$RESULTS/run_ctx_$CAP.log" \
    && echo "  !! OOM/Fallback im Log -> der $CAP-Lauf ist NICHT aussagekraeftig"

  stop_my_backend
  first=0
done

ollama stop "$MODEL" 2>/dev/null || true
echo "=== fertig. Vergleich: $RESULTS/ctx_16384  vs  $RESULTS/ctx_32768 ==="
