#!/usr/bin/env bash
#
# referenzfall_herr_w.sh — End-to-End-Test des Referenzfalls "Herr W." VOM AUDIO.
#
# Testet die komplette Kette wie im echten Betrieb:
#   Audio → Whisper (mit Issue-5 WHISPER_INITIAL_PROMPT) → Transkript
#         → Doku-Generierung (mit Issue-4 Orga-Trennung + Issue-2 Glossar)
#   für alle vier Modelle nebeneinander.
#
# Das prüft GENAU die Fixes, die die synthetischen Eval-Fälle NICHT abdecken:
#   - Issue 5: wird "Familiengespräch" korrekt transkribiert statt "Armee"?
#   - Issue 4: landet Organisatorisches sauber in der Schluss-Sektion?
#   - Rollen-/Verlaufstreue: Helen als Betreuerin (nicht Ressource)? Gruppendynamik?
#
# Nutzung (auf dem Pod, im backend-Verzeichnis):
#   bash referenzfall_herr_w.sh /pfad/zu/herr_w.m4a
#
# Voraussetzung: Backend läuft (uvicorn auf Port 8000) und AUTH_ENABLED=false
#   ODER der Runner-eigene Server. Prüfung erfolgt automatisch.

set -euo pipefail

AUDIO="${1:?Bitte Audio-Datei angeben: bash referenzfall_herr_w.sh /pfad/zu/audio.m4a}"
BACKEND="${EVAL_BACKEND_URL:-http://localhost:8000}"
OUT="${OUT_DIR:-/workspace/eval_results/referenzfall_herr_w}"
MODELS=("gemma3:27b" "mistral-small3.2" "gemma4:31b" "qwen3:32b")

if [[ ! -f "$AUDIO" ]]; then
  echo "FEHLER: Audio-Datei nicht gefunden: $AUDIO" >&2
  exit 1
fi

mkdir -p "$OUT"
echo "=================================================================="
echo "  Referenzfall Herr W. — End-to-End vom Audio"
echo "  Audio:   $AUDIO"
echo "  Backend: $BACKEND"
echo "  Output:  $OUT"
echo "=================================================================="

# ── 0. Backend erreichbar? ───────────────────────────────────────────────────
if ! curl -sf "$BACKEND/api/health" >/dev/null 2>&1 && ! curl -sf "$BACKEND/health" >/dev/null 2>&1; then
  echo "WARNUNG: Backend unter $BACKEND nicht erreichbar."
  echo "  Starte es in einem anderen Terminal, z.B.:"
  echo "    cd /workspace/scriptTelios/backend"
  echo "    AUTH_ENABLED=false OLLAMA_MODELS=/workspace/ollama \\"
  echo "      python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
  echo "  (AUTH_ENABLED=false erlaubt den Test ohne Confluence-Signatur)"
  exit 1
fi

# ── 1. TRANSKRIPTION (Issue-5-Test) ──────────────────────────────────────────
echo ""
echo "── Schritt 1: Transkription (Whisper + WHISPER_INITIAL_PROMPT) ──"
TRANSCRIPT_JSON="$OUT/transkript_response.json"
curl -sf -X POST "$BACKEND/api/transcribe" \
  -F "file=@${AUDIO}" \
  -o "$TRANSCRIPT_JSON"

# Transkript-Text extrahieren
python3 -c "
import json
d = json.load(open('$TRANSCRIPT_JSON'))
t = d['transcript']
open('$OUT/transkript.txt','w').write(t)
print(f'  Transkript: {d[\"word_count\"]} Wörter, Sprache={d[\"language\"]}, {d[\"duration_seconds\"]:.0f}s Audio')
"
TRANSCRIPT_FILE="$OUT/transkript.txt"

# Issue-5-Sofortcheck: die kritischen Begriffe
echo ""
echo "  ── Issue-5-Check (ASR-Qualität kritischer Begriffe) ──"
python3 -c "
t = open('$TRANSCRIPT_FILE', encoding='utf-8').read().lower()
checks = [
    ('Familiengespräch (statt Armee)', 'familiengespräch' in t or 'familiengespraech' in t),
    ('Transfergespräch',               'transfergespräch' in t or 'transfergespraech' in t),
    ('KEIN \"Armee\"',                   'armee' not in t),
    ('KEIN \"Bruder treiben\"',          'bruder treiben' not in t and 'bruder-treiben' not in t),
]
for label, ok in checks:
    print(f'    {\"✓\" if ok else \"✗\"} {label}')
print()
print('  → Transkript gespeichert. Bei Bedarf manuell prüfen: $TRANSCRIPT_FILE')
"

# ── 2. GENERIERUNG je Modell (Issue-4 + Issue-2-Test) ────────────────────────
echo ""
echo "── Schritt 2: Doku-Generierung (dokumentation) je Modell ──"
echo "  (nutzt das ROH-Transkript aus Schritt 1 als Eingang)"

for MODEL in "${MODELS[@]}"; do
  SLUG=$(echo "$MODEL" | tr ':/.' '___')
  echo ""
  echo "  ▸ $MODEL"

  # Job starten (dokumentation, mit Transkript-Text + Modell-Override)
  JOB_JSON="$OUT/job_${SLUG}.json"
  curl -sf -X POST "$BACKEND/api/jobs/generate" \
    -F "workflow=dokumentation" \
    -F "model=${MODEL}" \
    -F "patientenname=Herr W." \
    -F "transcript=<${TRANSCRIPT_FILE}" \
    -o "$JOB_JSON" || { echo "    FEHLER beim Job-Start"; continue; }

  JOB_ID=$(python3 -c "import json; print(json.load(open('$JOB_JSON')).get('job_id',''))")
  if [[ -z "$JOB_ID" ]]; then
    echo "    FEHLER: keine job_id erhalten. Response:"; cat "$JOB_JSON"; continue
  fi
  echo "    Job: $JOB_ID — warte auf Fertigstellung ..."

  # Pollen bis done/error (max 5 min)
  for i in $(seq 1 60); do
    sleep 5
    STATUS_JSON="$OUT/status_${SLUG}.json"
    curl -sf "$BACKEND/api/jobs/${JOB_ID}" -o "$STATUS_JSON" 2>/dev/null || continue
    STATUS=$(python3 -c "import json; print(json.load(open('$STATUS_JSON')).get('status','?'))" 2>/dev/null || echo "?")
    if [[ "$STATUS" == "done" ]]; then
      python3 -c "
import json
d = json.load(open('$STATUS_JSON'))
out = d.get('result') or d.get('output') or d.get('text') or ''
open('$OUT/output_${SLUG}.txt','w').write(out)
print(f'    ✓ fertig — {len(out.split())} Wörter → output_${SLUG}.txt')
"
      break
    elif [[ "$STATUS" == "error" ]]; then
      echo "    ✗ Job-Fehler:"; python3 -c "import json; print('     ', json.load(open('$STATUS_JSON')).get('error_msg','?'))"
      break
    fi
  done
done

echo ""
echo "=================================================================="
echo "  Fertig. Ergebnisse in: $OUT"
echo "    transkript.txt         — das Whisper-Transkript (Issue-5-Check)"
echo "    output_<modell>.txt    — die vier Dokus zum Gegenlesen"
echo ""
echo "  Alles in eine Datei bündeln (zum Hochladen/Vergleichen):"
echo "    { echo '### TRANSKRIPT'; cat $OUT/transkript.txt; \\"
echo "      for f in $OUT/output_*.txt; do echo; echo \"### \$f\"; cat \"\$f\"; done; } > $OUT/referenzfall_bundle.txt"
echo "=================================================================="
