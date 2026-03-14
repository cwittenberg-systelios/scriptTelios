#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  sysTelios KI-Dokumentation – RunPod Start-Script (ohne Docker)
#
#  Startet alle Services direkt auf dem Pod:
#    1. PostgreSQL  (apt, laeuft als unprivilegierter User)
#    2. Ollama      (offizielles Install-Script)
#    3. Backend     (pip + uvicorn)
#
#  Aufruf:
#    bash /workspace/scriptTelios/backend/runpod-start.sh
#
#  Modelle und Daten bleiben auf /workspace erhalten.
# ════════════════════════════════════════════════════════════════

set -e

BACKEND_DIR="/workspace/scriptTelios/backend"
VENV_DIR="/workspace/venv"
LOG_DIR="/workspace"
PG_DATA="/home/pg_systelios/postgres"
OLLAMA_MODELS_DIR="/workspace/ollama"
PG_USER="pg_systelios"

OK="[OK]    "
GO="[.....] "
WARN="[WARN]  "
ERR="[ERR]   "

echo ""
echo "================================================"
echo "   sysTelios KI-Dokumentation - Start"
echo "   $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"
echo ""

# 0. Verzeichnisse
echo "${GO}Verzeichnisse anlegen..."
# PG_DATA liegt im Home des pg_systelios-Users – wird im PostgreSQL-Abschnitt angelegt
mkdir -p "$OLLAMA_MODELS_DIR"
mkdir -p /workspace/uploads
mkdir -p /workspace/outputs
touch /workspace/systelios.log
echo "${OK}Verzeichnisse bereit"

# 1. System-Pakete
echo ""
echo "${GO}System-Pakete pruefen..."
PKGS_NEEDED=""
dpkg -l postgresql    >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED postgresql"
dpkg -l tesseract-ocr >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED tesseract-ocr tesseract-ocr-deu"
dpkg -l poppler-utils >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED poppler-utils"
dpkg -l ffmpeg        >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED ffmpeg"
dpkg -l libpq-dev     >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED libpq-dev"

if [ -n "$PKGS_NEEDED" ]; then
    echo "${GO}Installiere: $PKGS_NEEDED"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $PKGS_NEEDED
    echo "${OK}System-Pakete installiert"
else
    echo "${OK}System-Pakete bereits vorhanden"
fi

# 2. PostgreSQL
echo ""
echo "${GO}PostgreSQL starten..."

PG_BIN=$(find /usr/lib/postgresql -name "pg_ctl" 2>/dev/null | head -1 | xargs dirname 2>/dev/null || echo "")
if [ -z "$PG_BIN" ]; then
    echo "${ERR}pg_ctl nicht gefunden"
    exit 1
fi
echo "${OK}PostgreSQL Binary: $PG_BIN"

# PostgreSQL kann nicht als root laufen - eigenen User anlegen
if ! id "$PG_USER" >/dev/null 2>&1; then
    echo "${GO}User '$PG_USER' anlegen..."
    useradd -m "$PG_USER"
    mkdir -p "$PG_DATA"
fi

# Pruefen ob Postgres schon laeuft
if su -m "$PG_USER" -c "$PG_BIN/pg_ctl -D $PG_DATA status" 2>/dev/null | grep -q "server is running"; then
    echo "${OK}PostgreSQL laeuft bereits"
else
    # Cluster initialisieren falls noetig
    if [ ! -f "$PG_DATA/PG_VERSION" ]; then
        echo "${GO}Datenbank initialisieren..."
        su -m "$PG_USER" -c "$PG_BIN/initdb -D $PG_DATA --encoding=UTF8 --locale=C"
        echo "${OK}initdb abgeschlossen"
    fi

    # Starten
    echo "${GO}PostgreSQL starten..."
    su -m "$PG_USER" -c "$PG_BIN/pg_ctl -D $PG_DATA -l $LOG_DIR/postgres.log start"
    sleep 4

    # Warten bis bereit
    MAX=10
    for i in $(seq 1 $MAX); do
        if su -m "$PG_USER" -c "psql -d postgres -c 'SELECT 1'" >/dev/null 2>&1; then
            echo "${OK}PostgreSQL bereit"
            break
        fi
        [ "$i" = "$MAX" ] && { echo "${ERR}PostgreSQL startet nicht. Log: $LOG_DIR/postgres.log"; exit 1; }
        sleep 2
    done

    # User anlegen
    su -m "$PG_USER" -c "psql -d postgres -tc \"SELECT 1 FROM pg_roles WHERE rolname='systelios'\"" 2>/dev/null \
        | grep -q 1 \
        || su -m "$PG_USER" -c "psql -d postgres -c \"CREATE USER systelios WITH PASSWORD 'systelios';\""

    # Datenbank anlegen
    su -m "$PG_USER" -c "psql -d postgres -tc \"SELECT 1 FROM pg_database WHERE datname='systelios'\"" 2>/dev/null \
        | grep -q 1 \
        || su -m "$PG_USER" -c "psql -d postgres -c \"CREATE DATABASE systelios OWNER systelios;\""

    # pgvector Extension
    su -m "$PG_USER" -c "psql -d systelios -c \"CREATE EXTENSION IF NOT EXISTS vector;\"" 2>/dev/null \
        && echo "${OK}pgvector aktiviert" \
        || echo "${WARN}pgvector konnte nicht aktiviert werden"

    echo "${OK}Datenbank 'systelios' bereit"
fi

# 3. Ollama
echo ""
echo "${GO}Ollama pruefen..."

if ! command -v ollama >/dev/null 2>&1; then
    echo "${GO}Ollama installieren..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "${OK}Ollama installiert"
fi

export OLLAMA_MODELS="$OLLAMA_MODELS_DIR"
if ! pgrep -x ollama >/dev/null 2>&1; then
    echo "${GO}Ollama starten..."
    OLLAMA_MODELS="$OLLAMA_MODELS_DIR" ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
    sleep 5
fi

MAX=12
for i in $(seq 1 $MAX); do
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "${OK}Ollama bereit"
        break
    fi
    [ "$i" = "$MAX" ] && { echo "${ERR}Ollama antwortet nicht. Log: $LOG_DIR/ollama.log"; exit 1; }
    sleep 3
done

# Modelle pruefen und laden
echo "${GO}Modelle pruefen..."
MODELS=("mistral-nemo" "nomic-embed-text" "llava")
for model in "${MODELS[@]}"; do
    if OLLAMA_MODELS="$OLLAMA_MODELS_DIR" ollama list 2>/dev/null | grep -q "$model"; then
        echo "${OK}$model vorhanden"
    else
        echo "${GO}$model laden (kann einige Minuten dauern)..."
        OLLAMA_MODELS="$OLLAMA_MODELS_DIR" ollama pull "$model"
        echo "${OK}$model geladen"
    fi
done

# 4. Python Virtual Environment
echo ""
echo "${GO}Python-Umgebung pruefen..."

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "${GO}Virtual Environment anlegen..."
    python3 -m venv "$VENV_DIR"
    echo "${OK}venv angelegt"
fi

source "$VENV_DIR/bin/activate"

if ! python -c "import fastapi" 2>/dev/null; then
    echo "${GO}Python-Pakete installieren..."
    pip install --quiet --upgrade pip
    pip install --quiet -r "$BACKEND_DIR/requirements.txt"
    echo "${OK}Pakete installiert"
else
    echo "${OK}Python-Pakete vorhanden"
fi

# 5. .env pruefen
echo ""
if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo "${WARN}Keine .env - erstelle automatisch..."
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "$BACKEND_DIR/.env" << ENVEOF
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral-nemo
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
DATABASE_URL=postgresql+asyncpg://systelios:systelios@localhost:5432/systelios
SECRET_KEY=${SECRET}
DELETE_AUDIO_AFTER_TRANSCRIPTION=true
LOG_LEVEL=INFO
UPLOAD_DIR=/workspace/uploads
OUTPUT_DIR=/workspace/outputs
LOG_FILE=/workspace/systelios.log
ENVEOF
    echo "${OK}.env erstellt (SECRET_KEY automatisch generiert)"
else
    # Sicherstellen dass localhost statt 'db' verwendet wird
    sed -i 's|@db:5432|@localhost:5432|g' "$BACKEND_DIR/.env"
    echo "${OK}.env vorhanden"
fi

# 6. Backend starten
echo ""
echo "${GO}Backend starten..."
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 2

cd "$BACKEND_DIR"
source "$VENV_DIR/bin/activate"
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

nohup python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    >> "$LOG_DIR/backend.log" 2>&1 &

echo "${OK}Backend gestartet (PID: $!)"

# 7. Health Check
echo "${GO}Warte auf Backend..."
MAX=20
for i in $(seq 1 $MAX); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        echo "${OK}Backend erreichbar (http://localhost:8000)"
        break
    fi
    if [ "$i" = "$MAX" ]; then
        echo "${WARN}Backend antwortet noch nicht - Log pruefen:"
        echo "       tail -50 $LOG_DIR/backend.log"
    else
        printf "       Warte... (%d/%d)\r" "$i" "$MAX"
        sleep 3
    fi
done

# 8. Zusammenfassung
echo ""
echo "================================================"
echo "  Backend:   http://localhost:8000"
echo "  API-Docs:  http://localhost:8000/docs"
echo "  Ollama:    http://localhost:11434"
echo ""
echo "  Logs:"
echo "    Backend:    tail -f $LOG_DIR/backend.log"
echo "    Ollama:     tail -f $LOG_DIR/ollama.log"
echo "    PostgreSQL: tail -f $LOG_DIR/postgres.log"
echo ""
echo "  Stop:  pkill -f uvicorn; pkill ollama"
echo "  Stop PostgreSQL:"
echo "    su -m $PG_USER -c '$PG_BIN/pg_ctl -D $PG_DATA stop'"
echo "================================================"
echo ""
