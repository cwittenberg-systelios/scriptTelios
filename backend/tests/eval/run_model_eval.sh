#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_model_eval.sh — Modell-A/B-Eval für scriptTelios
#
# Vergleicht mehrere LLMs unter EINER Variable (dem Modell): identische Inputs
# (Transkripte einmal gecacht), reproduzierbar (fester Seed), gleicher num_ctx.
#
# Voraussetzungen:
#   - Ollama-Dienst läuft mit den Blackwell-Server-Envs (OLLAMA_LLM_LIBRARY=cuda_v13,
#     OLLAMA_NUM_PARALLEL=1, OLLAMA_FLASH_ATTENTION=true, OLLAMA_KV_CACHE_TYPE=q8_0)
#   - venv unter $VENV, Backend unter $BE, eval_data befüllbar (Audio/Styles)
#
# Nutzung:
#   chmod +x run_model_eval.sh
#   ./run_model_eval.sh
#
# Ergebnisse: $RESULTS/<modell>/  + run_<modell>.log + ollama_ps_<modell>.txt
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail                       # KEIN -e: pkill/grep liefern erwartbar non-zero

RESULTS=/workspace/eval_results
BE=/workspace/scriptTelios/backend
VENV=/workspace/venv
MODELS=("qwen3:32b" "qwen3.6:27b" "qwen3.6:35b-a3b")

mkdir -p "$RESULTS" /workspace/logs
export LLM_SEED=42                      # reproduzierbarer A/B (deterministischer Zug pro Modell)

start_backend() {
  ( cd "$BE" && source "$VENV/bin/activate" \
    && nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 \
       >> /workspace/logs/backend.log 2>&1 & )
}

wait_port_free() {                     # nach pkill: warten bis Port 8000 wirklich frei
  for _ in $(seq 1 30); do
    curl -sf localhost:8000/api/health >/dev/null 2>&1 || return 0
    sleep 1
  done
  echo "WARN: Port 8000 noch belegt"
}

wait_healthy() {                       # MIT Timeout (120s) statt Endlos-until
  for _ in $(seq 1 60); do
    curl -sf localhost:8000/api/health >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

first=1
for M in "${MODELS[@]}"; do
  echo "=== Modell: $M ==="
  ollama list | grep -q "$M" || ollama pull "$M" || { echo "pull $M fehlgeschlagen -> skip"; continue; }

  export OLLAMA_MODEL="$M"
  pkill -f "uvicorn app.main:app"; wait_port_free
  start_backend
  if ! wait_healthy; then
    echo "Backend fuer $M nicht healthy -> skip"
    ollama stop "$M" 2>/dev/null || true
    continue
  fi

  SAFE=$(echo "$M" | tr ':/.' '___')
  ollama ps > "$RESULTS/ollama_ps_$SAFE.txt" 2>&1     # geladenes Modell + Kontext bestaetigen

  # Transkripte nur EINMAL (erstes Modell) erzeugen -> identische Inputs ueber alle
  # Modelle; danach Cache (in eval_data) wiederverwenden, Whisper laeuft nicht neben
  # den naechsten LLMs (VRAM-Schonung auf 32GB).
  TR=""; [ "$first" = "1" ] && TR="--transcribe"
  ( cd "$BE" && source "$VENV/bin/activate" \
    && pytest tests/eval/test_eval.py -v --tb=short --qa --eval-report $TR \
       --eval-output "$RESULTS/$SAFE" > "$RESULTS/run_$SAFE.log" 2>&1 )
  echo "  pytest exit=$?  -> $RESULTS/run_$SAFE.log"
  grep -iqE "out of memory|CUDA error|cannot allocate" "$RESULTS/run_$SAFE.log" \
    && echo "  !! OOM/CUDA-Fehler im Log — Score ist dann unbrauchbar"

  ollama stop "$M" 2>/dev/null || true               # VRAM frei (Pflicht auf 32GB)
  first=0
done

pkill -f "uvicorn app.main:app" 2>/dev/null || true  # Final-Cleanup
echo "=== fertig: $RESULTS ==="
