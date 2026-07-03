#!/usr/bin/env bash
#
# referenzfall_generate.sh — Generierung je Modell fuer ein BEREITS
# transkribiertes Recording (v3).
#
# Fixes ggü. v2:
#   - liest korrektes Ergebnisfeld: result_text (nicht result/output)
#   - zieht jedes Modell selbst (ollama pull) und entfernt es danach (ollama rm),
#     ausser der Baseline qwen3:32b -> nie mehr als 1 Testmodell zusaetzlich
#     auf der Platte (70-GB-Quota-sicher).
#   - holt workflow_instructions-Default aus dem Server-Code (Issue-4-Test)
#
# Nutzung:  bash referenzfall_generate.sh <REC_ID>
#           z.B.  bash referenzfall_generate.sh 53

set -uo pipefail

REC_ID="${1:?Bitte Recording-ID: bash referenzfall_generate.sh 53}"
BACKEND="${EVAL_BACKEND_URL:-http://localhost:8000}"
OUT="${OUT_DIR:-/workspace/eval_results/referenzfall_herr_w}"
VENV_PY="${VENV_PY:-/workspace/venv/bin/python}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/workspace/ollama}"
MODELS=("gemma3:27b" "mistral-small3.2" "gemma4:31b" "qwen3:32b")
KEEP="qwen3:32b"   # Baseline nicht entfernen

mkdir -p "$OUT"

# ── workflow_instructions-Default holen ──────────────────────────────────────
WF_INSTR="$OUT/wf_instructions_dokumentation.txt"
echo "Hole Default-Workflow-Anweisungen (dokumentation) ..."
"$VENV_PY" - << 'PY' > "$WF_INSTR"
from app.services.prompts import WORKFLOW_INSTRUCTIONS_DEFAULT
print(WORKFLOW_INSTRUCTIONS_DEFAULT["dokumentation"], end="")
PY
WORDS=$(wc -w < "$WF_INSTR")
[[ "$WORDS" -ge 20 ]] || { echo "FEHLER: Anweisungen nicht geladen ($WORDS w). VENV_PY=$VENV_PY?"; exit 1; }
echo "  ✓ $WORDS Wörter"
grep -q "Organisatorisches" "$WF_INSTR" && echo "  ✓ Issue-4-Orga-Sektion aktiv" || echo "  ⚠ 'Organisatorisches' fehlt im Default"

echo ""
echo "── Generierung je Modell (Recording $REC_ID) ──"
for MODEL in "${MODELS[@]}"; do
  SLUG=$(echo "$MODEL" | tr ':/.' '___')
  echo ""; echo "  ▸ $MODEL"

  # Modell vorhanden? sonst ziehen
  if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "    ↓ nicht vorhanden — ollama pull $MODEL ..."
    if ! ollama pull "$MODEL" 2>&1 | tail -1; then
      echo "    XX pull fehlgeschlagen — überspringe $MODEL"; continue
    fi
  fi

  # Job starten
  JJ="$OUT/job_${SLUG}.json"
  HTTP=$(curl -s -o "$JJ" -w "%{http_code}" -X POST "$BACKEND/api/jobs/generate" \
    -F "workflow=dokumentation" -F "model=${MODEL}" \
    -F "patientenname=Herr W." -F "p0_recording_id=${REC_ID}" \
    -F "workflow_instructions=<${WF_INSTR}")
  if [[ "$HTTP" != "200" && "$HTTP" != "201" ]]; then
    echo "    XX Job-Start (HTTP $HTTP):"; cat "$JJ"; echo
  else
    JOB_ID=$(python3 -c "import json;print(json.load(open('$JJ')).get('job_id',''))" 2>/dev/null)
    if [[ -z "$JOB_ID" ]]; then
      echo "    XX keine job_id:"; cat "$JJ"
    else
      echo "    Job $JOB_ID"
      for i in $(seq 1 90); do
        sleep 5
        JS="$OUT/jobstatus_${SLUG}.json"
        curl -sf "$BACKEND/api/jobs/${JOB_ID}" -o "$JS" 2>/dev/null || continue
        STATUS=$(python3 -c "import json;print(json.load(open('$JS')).get('status','?'))" 2>/dev/null)
        printf "\r    Status: %-10s (%3ds)" "$STATUS" "$((i*5))"
        if [[ "$STATUS" == "done" ]]; then
          python3 -c "
import json
d=json.load(open('$JS'))
out=d.get('result_text') or ''
open('$OUT/output_${SLUG}.txt','w').write(out)
print(); print(f'    ✓ {len(out.split())} Wörter -> output_${SLUG}.txt (model_used={d.get(\"model_used\")})')
"
          break
        elif [[ "$STATUS" == "error" ]]; then
          echo; echo "    XX Fehler:"; python3 -c "import json;print('     ',json.load(open('$JS')).get('error_msg','?'))"; break
        fi
      done
    fi
  fi

  # Testmodell wieder entfernen (Quota), Baseline behalten
  if [[ "$MODEL" != "$KEEP" ]]; then
    echo "    ✗ entferne $MODEL (Quota) ..."
    ollama rm "$MODEL" >/dev/null 2>&1 || true
  fi
done

# Bundle
BUNDLE="$OUT/referenzfall_bundle.txt"
{
  [[ -f "$OUT/transkript.txt" ]] && { echo "### TRANSKRIPT (Recording $REC_ID)"; echo; cat "$OUT/transkript.txt"; echo; }
  for f in "$OUT"/output_*.txt; do
    [[ -f "$f" ]] || continue
    echo; echo "### $(basename "$f")"; echo; cat "$f"; echo
  done
} > "$BUNDLE"

echo ""; echo "=================================================================="
echo "  Fertig. Bundle: $BUNDLE"
echo "=================================================================="
