#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  sysTelios – RunPod Start-Script
#  Aufruf: bash /runpod-volume/scriptTelios-updated/backend/runpod-start.sh
# ════════════════════════════════════════════════════════════════

set -e
COMPOSE="docker compose -f /runpod-volume/scriptTelios-updated/backend/docker-compose.runpod.yml"
BACKEND_DIR="/runpod-volume/scriptTelios-updated/backend"

echo ""
echo "================================================"
echo "   sysTelios KI-Dokumentation – Start"
echo "================================================"
echo ""

# Verzeichnisse anlegen
echo "-> Verzeichnisse pruefen..."
mkdir -p /runpod-volume/ollama
mkdir -p /runpod-volume/postgres
mkdir -p /runpod-volume/uploads
mkdir -p /runpod-volume/outputs
touch /runpod-volume/systelios.log

# .env pruefen
if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo "WARNUNG: Keine .env gefunden – erstelle Vorlage..."
    cat > "$BACKEND_DIR/.env" << 'ENVEOF'
OLLAMA_MODEL=mistral-nemo
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
SECRET_KEY=bitte-aendern-vor-produktionseinsatz
DELETE_AUDIO_AFTER_TRANSCRIPTION=true
LOG_LEVEL=INFO
ENVEOF
    echo "   .env erstellt – bitte SECRET_KEY anpassen!"
else
    echo "OK .env vorhanden"
fi

# Docker Images bauen
echo ""
echo "-> Docker Images bauen..."
cd "$BACKEND_DIR"
$COMPOSE build --quiet
echo "OK Images bereit"

# Stack starten
echo ""
echo "-> Stack starten..."
$COMPOSE up -d
echo "OK Container gestartet"

# Ollama Modelle pruefen
echo ""
echo "-> Warte auf Ollama (5s)..."
sleep 5

check_model() {
    $COMPOSE exec -T ollama ollama list 2>/dev/null | grep -q "$1"
}

MODELS=("mistral-nemo" "nomic-embed-text" "llava")
for model in "${MODELS[@]}"; do
    if check_model "$model"; then
        echo "OK $model bereits vorhanden"
    else
        echo "-> $model wird geladen..."
        $COMPOSE exec -T ollama ollama pull "$model"
        echo "OK $model geladen"
    fi
done

# Health Check
echo ""
echo "-> Health Check..."
sleep 5
MAX=12
for i in $(seq 1 $MAX); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        echo "OK Backend erreichbar"
        break
    fi
    if [ "$i" = "$MAX" ]; then
        echo "WARNUNG Backend antwortet nicht – Logs pruefen:"
        echo "   $COMPOSE logs backend"
    else
        echo "   Warte... ($i/$MAX)"
        sleep 5
    fi
done

echo ""
echo "================================================"
echo "  Backend:   http://localhost:8000"
echo "  API-Docs:  http://localhost:8000/docs"
echo "  Ollama:    http://localhost:11434"
echo ""
echo "  Logs:  $COMPOSE logs -f backend"
echo "  Stop:  $COMPOSE down"
echo "================================================"
echo ""
