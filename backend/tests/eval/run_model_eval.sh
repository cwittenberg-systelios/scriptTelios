#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Modellvergleich fuer scriptTelios
#
# Faehrt das Eval pro Modell mit IDENTISCHER, niedriger Temperatur (Determinismus)
# in GETRENNTE Output-Verzeichnisse. Danach `compare_models.py` fuer Side-by-Side.
#
# Modelle muessen vorab via `ollama pull <name>` vorhanden sein (werden NICHT
# automatisch gepullt - ein 16GB-Download soll nicht ueberraschend starten).
#
# Nutzung:
#   bash tests/eval/run_model_eval.sh
#   CMP_TEMP=0.2 bash tests/eval/run_model_eval.sh          # andere Temp
#   CMP_K="test_eval_workflow and dokumentation" bash ...   # nur eine Teilmenge
#
#   # 70GB-Platte zu klein fuer alle Modelle gleichzeitig -> sequentiell holen+entfernen:
#   CMP_AUTOPULL=1 CMP_RM_AFTER=1 bash tests/eval/run_model_eval.sh
#   # -> pullt jedes fehlende Modell vor dem Lauf, entfernt es danach wieder
#   #    (ausser CMP_KEEP, default qwen3:32b). Peak: ~1 neues Modell + Baseline.
#
# Wichtig (Pod-Eigenheiten, aus frueheren Laeufen):
#   - eigener uvicorn auf Port 8001 (NIE pkill -f uvicorn -> killt PID-1-Kind)
#   - AUTH_ENABLED=false (Eval sendet kein Confluence-HMAC)
#   - LLM_TEMPERATURE_OVERRIDE erzwingt gleiche Temp ueber alle Modelle/Pfade
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

# ===== HIER Modelle eintragen (muessen via `ollama pull` vorhanden sein, ODER
# ===== CMP_AUTOPULL=1 setzen) ================================================
MODELS=(
  "qwen3:32b"          # Baseline (aktuelles Produktionsmodell) - NICHT entfernen
  "gemma3:27b"         # Google Gemma 3 - deutsche Prosa (~17GB)
  "qwen3.6:27b"        # Alibaba Qwen 3.6 - neueste Qwen-Gen, aber coding-orientiert (~17GB)
  "mistral-small"      # Mistral Small 3 - europaeisch, ~24B (~14GB)
)

TEMP="${CMP_TEMP:-0.0}"                              # 0.0 = greedy/deterministisch
OUT_ROOT="${CMP_OUT:-/workspace/eval_results/model_cmp}"
KSEL="${CMP_K:-test_eval_workflow}"                  # welche Eval-Cases
# Disk-Management (70GB-Platte reicht NICHT fuer alle Modelle gleichzeitig):
AUTOPULL="${CMP_AUTOPULL:-0}"                         # 1 = fehlende Modelle vor dem Lauf pullen
RM_AFTER="${CMP_RM_AFTER:-0}"                         # 1 = Modell NACH dem Lauf wieder entfernen
KEEP="${CMP_KEEP:-qwen3:32b}"                         # dieses Modell NIE entfernen (Produktion)
BE="/workspace/scriptTelios/backend"
PY="/workspace/venv/bin/python"
PORT=8001

cd "$BE" || { echo "Backend-Verzeichnis fehlt: $BE"; exit 1; }
set -a; . /workspace/.env 2>/dev/null; set +a
export AUTH_ENABLED=false
export LLM_TEMPERATURE_OVERRIDE="$TEMP"
export EVAL_BACKEND_URL="http://127.0.0.1:${PORT}"
export LLM_NUM_CTX_CAP="${LLM_NUM_CTX_CAP:-16384}"
[ -n "${LLM_SEED:-}" ] || export LLM_SEED=42        # bei TEMP>0 relevant
mkdir -p "$OUT_ROOT" /workspace/logs

MYPID=""
start_be () {  # $1 = modellname
  export OLLAMA_MODEL="$1"
  ( exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 ) \
      >> "/workspace/logs/cmp_backend_${PORT}.log" 2>&1 &
  MYPID=$!
  for _ in $(seq 1 90); do
    curl -sf "127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "  !! Backend kam nicht hoch fuer $1 (siehe cmp_backend_${PORT}.log)"
  return 1
}
stop_be () {
  [ -n "$MYPID" ] && kill "$MYPID" 2>/dev/null
  pkill -f "uvicorn.*${PORT}" 2>/dev/null
  MYPID=""
  sleep 2
}
trap stop_be EXIT

echo "Temperatur (alle Modelle): $TEMP   |   Cases: $KSEL   |   Out: $OUT_ROOT"
echo ""

for M in "${MODELS[@]}"; do
  SLUG=$(printf '%s' "$M" | tr '/:. ' '____')
  echo "=================================================================="
  echo "  MODELL: $M    ->    $OUT_ROOT/$SLUG"
  echo "=================================================================="
  if ! ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$M"; then
    if [ "$AUTOPULL" = "1" ]; then
      echo "  nicht vorhanden -> ollama pull $M ..."
      if ! ollama pull "$M"; then
        echo "  !! pull fehlgeschlagen fuer $M (Tag pruefen: ollama.com/library) - uebersprungen"
        echo ""
        continue
      fi
    else
      echo "  FEHLT in Ollama. Erst holen:  ollama pull $M   (oder CMP_AUTOPULL=1) - uebersprungen"
      echo ""
      continue
    fi
  fi
  ollama stop "$M" 2>/dev/null; sleep 1
  if ! start_be "$M"; then stop_be; continue; fi

  "$PY" -m pytest tests/eval/test_eval.py --tb=short --qa \
      -k "$KSEL" \
      --eval-output "$OUT_ROOT/$SLUG" \
      2>&1 | tee "/workspace/logs/cmp_${SLUG}.log"

  stop_be
  ollama stop "$M" 2>/dev/null; sleep 2
  if [ "$RM_AFTER" = "1" ] && [ "$M" != "$KEEP" ]; then
    echo "  ollama rm $M  (Platte freigeben; KEEP=$KEEP bleibt)"
    ollama rm "$M" 2>/dev/null || true
  fi
  echo ""
done

echo "=================================================================="
echo "  Fertig. Side-by-Side-Report erzeugen:"
echo "    $PY -m tests.eval.compare_models --root $OUT_ROOT"
echo "=================================================================="
