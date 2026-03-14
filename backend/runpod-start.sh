#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  sysTelios KI-Dokumentation – RunPod Start-Script (ohne Docker)
#
#  Startet alle Services direkt auf dem Pod:
#    1. PostgreSQL  (apt)
#    2. Ollama      (offizielles Install-Script)
#    3. Backend     (pip + uvicorn)
#
#  Aufruf:
#    bash /workspace/scriptTelios/backend/runpod-start.sh
#
#  Nach Pod-Neustart: gleiches Script erneut ausführen.
#  Modelle und Daten bleiben auf /workspace erhalten.
# ════════════════════════════════════════════════════════════════

set -e

BACKEND_DIR="/workspace/scriptTelios/backend"
VENV_DIR="/workspace/venv"
LOG_DIR="/workspace"
PG_DATA="/workspace/postgres"
OLLAMA_MODELS="/workspace/ollama"

# Farben für Ausgabe
OK="[OK]"
GO="[.....]"
WARN="[WARN]"
ERR="[ERR]"

echo ""
echo "================================================"
echo "   sysTelios KI-Dokumentation – Start"
echo "   $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"
echo ""

# ── 0. Verzeichnisse ─────────────────────────────────────────────
echo "$GO Verzeichnisse anlegen..."
mkdir -p "$PG_DATA"
mkdir -p "$OLLAMA_MODELS"
mkdir -p /workspace/uploads
mkdir -p /workspace/outputs
touch /workspace/systelios.log
echo "$OK Verzeichnisse bereit"

# ── 1. System-Pakete ─────────────────────────────────────────────
echo ""
echo "$GO System-Pakete prüfen..."

PKGS_NEEDED=""
command -v psql   >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED postgresql"
command -v pg_ctl >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED postgresql"
dpkg -l tesseract-ocr     >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED tesseract-ocr tesseract-ocr-deu"
dpkg -l poppler-utils     >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED poppler-utils"
dpkg -l ffmpeg            >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED ffmpeg"
dpkg -l libpq-dev         >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED libpq-dev"

if [ -n "$PKGS_NEEDED" ]; then
    echo "$GO Installiere:$PKGS_NEEDED"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $PKGS_NEEDED
    echo "$OK System-Pakete installiert"
else
    echo "$OK System-Pakete bereits vorhanden"
fi

# ── 2. PostgreSQL ─────────────────────────────────────────────────
echo ""
echo "$GO PostgreSQL starten..."

# Datenbankverzeichnis initialisieren falls leer
PG_VERSION=$(pg_lsclusters 2>/dev/null | awk 'NR==2{print $1}' || echo "14")
PG_BIN=$(find /usr/lib/postgresql -name "pg_ctl" 2>/dev/null | head -1 | xargs dirname 2>/dev/null || echo "/usr/lib/postgresql/${PG_VERSION}/bin")

# Prüfen ob Postgres schon läuft
if pg_isready -q 2>/dev/null; then
    echo "$OK PostgreSQL läuft bereits"
else
    # Cluster initialisieren falls nötig
    if [ ! -f "$PG_DATA/PG_VERSION" ]; then
        echo "$GO Datenbank initialisieren..."
        "$PG_BIN/initdb" -D "$PG_DATA" --encoding=UTF8 --locale=de_DE.UTF-8 2>/dev/null \
            || "$PG_BIN/initdb" -D "$PG_DATA" --encoding=UTF8 --locale=C 2>/dev/null \
            || true
    fi

    # PostgreSQL starten
    "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$LOG_DIR/postgres.log" start
    sleep 3

    # Warten bis bereit
    MAX=10
    for i in $(seq 1 $MAX); do
        if pg_isready -q 2>/dev/null; then
            echo "$OK PostgreSQL bereit"
            break
        fi
        [ "$i" = "$MAX" ] && { echo "$ERR PostgreSQL startet nicht – Log: $LOG_DIR/postgres.log"; exit 1; }
        sleep 2
    done

    # Benutzer und Datenbank anlegen (einmalig)
    psql -U postgres -tc "SELECT 1 FROM pg_roles WHERE rolname='systelios'" 2>/dev/null \
        | grep -q 1 || psql -U postgres -c "CREATE USER systelios WITH PASSWORD 'systelios';" 2>/dev/null || true

    psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='systelios'" 2>/dev/null \
        | grep -q 1 || psql -U postgres -c "CREATE DATABASE systelios OWNER systelios;" 2>/dev/null || true

    # pgvector Extension
    psql -U postgres -d systelios -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true
    echo "$OK Datenbank 'systelios' bereit"
fi

# ── 3. Ollama ─────────────────────────────────────────────────────
echo ""
echo "$GO Ollama prüfen..."

if ! command -v ollama >/dev/null 2>&1; then
    echo "$GO Ollama installieren..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "$OK Ollama installiert"
fi

# Modell-Verzeichnis auf Network Volume
export OLLAMA_MODELS="$OLLAMA_MODELS"

# Ollama starten falls nicht aktiv
if ! pgrep -x ollama >/dev/null 2>&1; then
    echo "$GO Ollama starten..."
    OLLAMA_MODELS="$OLLAMA_MODELS" ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
    sleep 5
fi

# Warten bis Ollama API antwortet
MAX=12
for i in $(seq 1 $MAX); do
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "$OK Ollama bereit"
        break
    fi
    [ "$i" = "$MAX" ] && { echo "$ERR Ollama antwortet nicht – Log: $LOG_DIR/ollama.log"; exit 1; }
    sleep 3
done

# Modelle laden (nur wenn noch nicht vorhanden)
echo "$GO Modelle prüfen..."
MODELS=("mistral-nemo" "nomic-embed-text" "llava")
for model in "${MODELS[@]}"; do
    if OLLAMA_MODELS="$OLLAMA_MODELS" ollama list 2>/dev/null | grep -q "$model"; then
        echo "$OK $model vorhanden"
    else
        echo "$GO $model laden (~kann einige Minuten dauern)..."
        OLLAMA_MODELS="$OLLAMA_MODELS" ollama pull "$model"
        echo "$OK $model geladen"
    fi
done

# ── 4. Python Virtual Environment ────────────────────────────────
echo ""
echo "$GO Python-Umgebung prüfen..."

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "$GO Virtual Environment anlegen..."
    python3 -m venv "$VENV_DIR"
    echo "$OK venv angelegt"
fi

source "$VENV_DIR/bin/activate"

# Pakete installieren (nur wenn nötig)
if ! python -c "import fastapi" 2>/dev/null; then
    echo "$GO Python-Pakete installieren..."
    pip install --quiet --upgrade pip
    pip install --quiet -r "$BACKEND_DIR/requirements.txt"
    echo "$OK Pakete installiert"
else
    echo "$OK Python-Pakete vorhanden"
fi

# ── 5. .env prüfen ───────────────────────────────────────────────
echo ""
if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo "$WARN Keine .env gefunden – erstelle Vorlage..."
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
    echo "$OK .env erstellt (SECRET_KEY automatisch generiert)"
else
    # DATABASE_URL sicherstellen dass localhost statt 'db' verwendet wird
    sed -i 's|@db:5432|@localhost:5432|g' "$BACKEND_DIR/.env"
    echo "$OK .env vorhanden"
fi

# ── 6. Backend starten ───────────────────────────────────────────
echo ""
echo "$GO Backend starten..."

# Alten Prozess stoppen falls läuft
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 2

cd "$BACKEND_DIR"
source "$VENV_DIR/bin/activate"

# Umgebungsvariablen laden
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

# Backend im Hintergrund starten
nohup python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    >> "$LOG_DIR/backend.log" 2>&1 &

BACKEND_PID=$!
echo "$GO Backend PID: $BACKEND_PID"

# ── 7. Health Check ──────────────────────────────────────────────
echo "$GO Warte auf Backend..."
MAX=20
for i in $(seq 1 $MAX); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        echo "$OK Backend erreichbar"
        break
    fi
    if [ "$i" = "$MAX" ]; then
        echo "$WARN Backend antwortet noch nicht – Log prüfen:"
        echo "      tail -50 $LOG_DIR/backend.log"
    else
        echo "      Warte... ($i/$MAX)"
        sleep 3
    fi
done

# ── 8. Zusammenfassung ───────────────────────────────────────────
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
echo "  Stop Backend:  pkill -f uvicorn"
echo "  Stop Ollama:   pkill ollama"
echo "================================================"
echo ""
