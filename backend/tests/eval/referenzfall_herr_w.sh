#!/usr/bin/env bash
#
# referenzfall_herr_w.sh — End-to-End-Test "Herr W." VOM AUDIO (v2, echter Fluss).
# Nutzt den Recording-basierten Pfad (es gibt keinen /api/transcribe-Endpunkt):
#   1. POST /api/recordings      (Audio hoch, async Transkription via P0-Queue)
#   2. GET  /api/recordings/{id} (pollen bis status=ready)
#   3. POST /api/jobs/generate   (dokumentation, p0_recording_id, je Modell)
#   4. GET  /api/jobs/{job_id}   (pollen bis status=done)
#
# Nutzung: bash referenzfall_herr_w.sh ./aufnahme-51.webm
# Backend muss mit AUTH_ENABLED=false laufen.

set -uo pipefail

AUDIO="${1:?Bitte Audio angeben: bash referenzfall_herr_w.sh ./audio.webm}"
BACKEND="${EVAL_BACKEND_URL:-http://localhost:8000}"
OUT="${OUT_DIR:-/workspace/eval_results/referenzfall_herr_w}"
MODELS=("gemma3:27b" "mistral-small3.2" "gemma4:31b" "qwen3:32b")

[[ -f "$AUDIO" ]] || { echo "FEHLER: Audio nicht gefunden: $AUDIO" >&2; exit 1; }
mkdir -p "$OUT"

echo "=================================================================="
echo "  Referenzfall Herr W. — End-to-End vom Audio (v2)"
echo "  Audio:   $AUDIO   ($(du -h "$AUDIO" | cut -f1))"
echo "  Backend: $BACKEND    Output: $OUT"
echo "=================================================================="

if ! curl -sf "$BACKEND/api/health" >/dev/null 2>&1; then
  echo "FEHLER: Backend nicht erreichbar. Server starten (venv!):" >&2
  echo "  AUTH_ENABLED=false OLLAMA_MODELS=/workspace/ollama \\" >&2
  echo "    /workspace/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000" >&2
  exit 1
fi
echo "Backend erreichbar."

# 1. Upload
echo ""; echo "── Schritt 1: Audio hochladen ──"
UP="$OUT/recording_upload.json"
HTTP=$(curl -s -o "$UP" -w "%{http_code}" -X POST "$BACKEND/api/recordings" \
  -F "audio=@${AUDIO}" -F "label=Referenzfall Herr W.")
if [[ "$HTTP" != "201" && "$HTTP" != "200" ]]; then
  echo "FEHLER Upload (HTTP $HTTP):"; cat "$UP"; exit 1
fi
REC_ID=$(python3 -c "import json;print(json.load(open('$UP'))['id'])")
echo "  Recording id=$REC_ID (async Transkription gestartet)"

# 2. Auf Transkript warten
echo ""; echo "── Schritt 2: Auf Transkription warten ──"
TF="$OUT/transkript.txt"
for i in $(seq 1 120); do
  sleep 5
  ST="$OUT/recording_status.json"
  curl -sf "$BACKEND/api/recordings/${REC_ID}" -o "$ST" 2>/dev/null || continue
  STATUS=$(python3 -c "import json;print(json.load(open('$ST')).get('status','?'))" 2>/dev/null)
  printf "\r  Status: %-12s (%3ds)" "$STATUS" "$((i*5))"
  if [[ "$STATUS" == "ready" ]]; then
    python3 -c "
import json
d=json.load(open('$ST')); t=d.get('transcript') or ''
open('$TF','w').write(t)
print(); print(f'  Transkript fertig: {len(t.split())} Woerter, {d.get(\"duration_s\",0):.0f}s')
"
    break
  elif [[ "$STATUS" == "error" ]]; then
    echo; echo "  FEHLER Transkription:"; python3 -c "import json;print('   ',json.load(open('$ST')).get('error_msg','?'))"; exit 1
  fi
done
[[ -f "$TF" ]] || { echo "FEHLER: Transkript-Timeout."; exit 1; }

# Issue-5-Check
echo ""; echo "  ── Issue-5-Check (ASR-Begriffe) ──"
python3 -c "
t=open('$TF',encoding='utf-8').read().lower()
for l,ok in [('Familiengespraech','familiengespräch' in t or 'familiengespraech' in t),
             ('Transfergespraech','transfergespräch' in t or 'transfergespraech' in t),
             ('KEIN Armee','armee' not in t),
             ('KEIN Bruder treiben','bruder treiben' not in t)]:
    print(f'    {\"OK\" if ok else \"XX\"} {l}')
"

# 3. Generierung je Modell
echo ""; echo "── Schritt 3: Generierung je Modell ──"
for MODEL in "${MODELS[@]}"; do
  SLUG=$(echo "$MODEL" | tr ':/.' '___')
  echo ""; echo "  > $MODEL"
  JJ="$OUT/job_${SLUG}.json"
  HTTP=$(curl -s -o "$JJ" -w "%{http_code}" -X POST "$BACKEND/api/jobs/generate" \
    -F "workflow=dokumentation" -F "model=${MODEL}" \
    -F "patientenname=Herr W." -F "p0_recording_id=${REC_ID}")
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

# 4. Bundle
BUNDLE="$OUT/referenzfall_bundle.txt"
{
  echo "### TRANSKRIPT (Recording $REC_ID)"; echo; cat "$TF"; echo
  for f in "$OUT"/output_*.txt; do
    [[ -f "$f" ]] || continue
    echo; echo "### $(basename "$f")"; echo; cat "$f"; echo
  done
} > "$BUNDLE"

echo ""; echo "=================================================================="
echo "  Fertig. Bundle: $BUNDLE"
echo "=================================================================="
