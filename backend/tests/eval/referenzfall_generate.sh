#!/usr/bin/env bash
#
# referenzfall_generate.sh — Schritt 3+4 des Herr-W.-Tests: Generierung je Modell
# fuer ein BEREITS transkribiertes Recording. Spart das erneute 65-min-Transkribieren.
#
# Nutzung:  bash referenzfall_generate.sh <REC_ID>
#   z.B.    bash referenzfall_generate.sh 52
#
# Holt WORKFLOW_INSTRUCTIONS_DEFAULT["dokumentation"] selbst vom Server-Code
# (via venv-Python) und sendet es als Pflichtfeld workflow_instructions mit -
# damit wird exakt der Issue-4-Default (Orga-Trennung) mitgetestet.

set -uo pipefail

REC_ID="${1:?Bitte Recording-ID angeben: bash referenzfall_generate.sh 52}"
BACKEND="${EVAL_BACKEND_URL:-http://localhost:8000}"
OUT="${OUT_DIR:-/workspace/eval_results/referenzfall_herr_w}"
VENV_PY="${VENV_PY:-/workspace/venv/bin/python}"
MODELS=("gemma3:27b" "mistral-small3.2" "gemma4:31b" "qwen3:32b")

mkdir -p "$OUT"

# ── workflow_instructions-Default aus dem Server-Code holen ──────────────────
WF_INSTR="$OUT/wf_instructions_dokumentation.txt"
echo "Hole Default-Workflow-Anweisungen (dokumentation) aus dem Code ..."
"$VENV_PY" - << 'PY' > "$WF_INSTR"
from app.services.prompts import WORKFLOW_INSTRUCTIONS_DEFAULT
print(WORKFLOW_INSTRUCTIONS_DEFAULT["dokumentation"], end="")
PY
WORDS=$(wc -w < "$WF_INSTR")
if [[ "$WORDS" -lt 20 ]]; then
  echo "FEHLER: Default-Anweisungen konnten nicht geladen werden (nur $WORDS Wörter)." >&2
  echo "  Prüfe VENV_PY=$VENV_PY und dass du im backend-Verzeichnis bist." >&2
  exit 1
fi
echo "  ✓ $WORDS Wörter geladen (enthält Issue-4-Orga-Trennung)"
grep -q "Organisatorisches" "$WF_INSTR" && echo "  ✓ 'Organisatorisches'-Sektion im Default vorhanden" \
  || echo "  ⚠ 'Organisatorisches' NICHT im Default — Issue-4-Patch evtl. nicht aktiv?"

echo ""
echo "── Generierung je Modell (Recording $REC_ID) ──"
for MODEL in "${MODELS[@]}"; do
  SLUG=$(echo "$MODEL" | tr ':/.' '___')
  echo ""; echo "  > $MODEL"
  JJ="$OUT/job_${SLUG}.json"
  HTTP=$(curl -s -o "$JJ" -w "%{http_code}" -X POST "$BACKEND/api/jobs/generate" \
    -F "workflow=dokumentation" \
    -F "model=${MODEL}" \
    -F "patientenname=Herr W." \
    -F "p0_recording_id=${REC_ID}" \
    -F "workflow_instructions=<${WF_INSTR}")
  if [[ "$HTTP" != "200" && "$HTTP" != "201" ]]; then
    echo "    XX Job-Start (HTTP $HTTP):"; cat "$JJ"; echo; continue
  fi
  JOB_ID=$(python3 -c "import json;print(json.load(open('$JJ')).get('job_id',''))" 2>/dev/null)
  [[ -n "$JOB_ID" ]] || { echo "    XX keine job_id:"; cat "$JJ"; continue; }
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
d=json.load(open('$JS')); out=d.get('result') or d.get('output') or d.get('text') or ''
open('$OUT/output_${SLUG}.txt','w').write(out)
print(); print(f'    OK {len(out.split())} Woerter -> output_${SLUG}.txt')
"
      break
    elif [[ "$STATUS" == "error" ]]; then
      echo; echo "    XX Fehler:"; python3 -c "import json;print('     ',json.load(open('$JS')).get('error_msg','?'))"; break
    fi
  done
done

# Bundle bauen (Transkript aus Schritt 2 sollte schon in $OUT/transkript.txt liegen)
BUNDLE="$OUT/referenzfall_bundle.txt"
{
  if [[ -f "$OUT/transkript.txt" ]]; then
    echo "### TRANSKRIPT (Recording $REC_ID)"; echo; cat "$OUT/transkript.txt"; echo
  fi
  for f in "$OUT"/output_*.txt; do
    [[ -f "$f" ]] || continue
    echo; echo "### $(basename "$f")"; echo; cat "$f"; echo
  done
} > "$BUNDLE"

echo ""; echo "=================================================================="
echo "  Fertig. Bundle zum Hochladen: $BUNDLE"
echo "=================================================================="
