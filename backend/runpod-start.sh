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
#    bash /workspace/scriptTelios/backend/runpod-start.sh [OPTIONEN]
#
#  Optionen:
#    --hf-token   hf_xxxx   HuggingFace Token fuer pyannote Diarization
#    --confluence  URL       Confluence-Intranet-URL fuer CORS
#    --model       NAME      Ollama-Modell (Standard: qwen3:32b)
#    --help                  Diese Hilfe anzeigen
#
#  Beispiel (neuer Pod):
#    bash runpod-start.sh --hf-token hf_abc123 --confluence https://wiki.klinik.de
#
#  Modelle und Daten bleiben auf /workspace erhalten.
# ════════════════════════════════════════════════════════════════

set -e

# ── Argumente parsen ──────────────────────────────────────────────────────────
ARG_HF_TOKEN=""
ARG_CONFLUENCE=""
ARG_MODEL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hf-token)   ARG_HF_TOKEN="$2";   shift 2 ;;
        --confluence) ARG_CONFLUENCE="$2"; shift 2 ;;
        --model)      ARG_MODEL="$2";      shift 2 ;;
        --help)
            grep "^#  " "$0" | sed 's/^#  //'
            exit 0 ;;
        *) echo "Unbekannte Option: $1 (--help fuer Hilfe)"; exit 1 ;;
    esac
done

# ── /workspace/.env laden falls vorhanden ────────────────────────────────────
# Zentraler Ort für alle Secrets:
#   CLOUDFLARE_TUNNEL_TOKEN, CONFLUENCE_SHARED_SECRET, AUTH_ENABLED,
#   ALLOWED_ORIGINS, RATE_LIMIT_* etc.
if [ -f "/workspace/.env" ]; then
    set -a
    source /workspace/.env
    set +a
    echo "[INFO] Environment aus /workspace/.env geladen"
fi

BACKEND_DIR="/workspace/scriptTelios/backend"
VENV_DIR="/workspace/venv"
LOG_DIR="/workspace"
# Variante B: PostgreSQL läuft direkt auf dem Network Volume.
# Kein Sync-cp mehr — Daten sind automatisch persistent bei Pod-Terminate.
PG_DATA="/workspace/postgres_data"
OLLAMA_MODELS_DIR="/workspace/ollama"
PG_USER="systelios_pg"

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
mkdir -p "$OLLAMA_MODELS_DIR"
mkdir -p /workspace/uploads
mkdir -p /workspace/outputs
touch /workspace/systelios.log
echo "${OK}Verzeichnisse bereit"

# 1. System-Pakete
echo ""
echo "${GO}System-Pakete pruefen..."
PKGS_NEEDED=""
dpkg -l postgresql             >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED postgresql"
dpkg -l tesseract-ocr          >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED tesseract-ocr tesseract-ocr-deu"
dpkg -l poppler-utils          >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED poppler-utils"
dpkg -l ffmpeg                 >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED ffmpeg"
dpkg -l libpq-dev              >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED libpq-dev"
dpkg -l zstd                   >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED zstd"
dpkg -l curl                   >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED curl"
dpkg -l gpg                    >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED gpg"
# lshw + pciutils: Ollama braucht diese fuer GPU-Erkennung beim Install
dpkg -l lshw                   >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED lshw"
dpkg -l pciutils               >/dev/null 2>&1 || PKGS_NEEDED="$PKGS_NEEDED pciutils"

if [ -n "$PKGS_NEEDED" ]; then
    echo "${GO}Installiere: $PKGS_NEEDED"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $PKGS_NEEDED
    echo "${OK}System-Pakete installiert"
else
    echo "${OK}System-Pakete bereits vorhanden"
fi

# pgvector – braucht offizielles PostgreSQL-Repo
if ! dpkg -l postgresql-14-pgvector >/dev/null 2>&1; then
    echo "${GO}pgvector installieren..."
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg
    echo "deb https://apt.postgresql.org/pub/repos/apt jammy-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-14-pgvector
    echo "${OK}pgvector installiert"
else
    echo "${OK}pgvector bereits vorhanden"
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

# PostgreSQL kann nicht als root laufen – eigenen User zuerst anlegen
if ! id "$PG_USER" >/dev/null 2>&1; then
    echo "${GO}User '$PG_USER' anlegen..."
    useradd -m "$PG_USER"
fi

# Lock-File-Verzeichnis muss dem PG_USER gehoeren (nach useradd!)
mkdir -p /var/run/postgresql
chown "$PG_USER" /var/run/postgresql
chmod 775 /var/run/postgresql

# Variante B: PG-Daten liegen direkt auf /workspace (Network Volume).
# Keine Sync-Kopie mehr nötig — Daten sind persistent über Pod-Neustarts.
#
# Wichtig: RunPod erlaubt auf Network-Volume-Mounts kein chown, deshalb
# verwenden wir dort POSIX-ACLs oder (Fallback) schreiben als UID 999
# (systelios_pg). Das Verzeichnis muss dem PG-User gehören.

mkdir -p "$PG_DATA"

# Variante B: PostgreSQL läuft direkt auf /workspace/postgres_data (Network Volume).
# Keine Migration, kein Sync — alle Daten persistent auf Volume.
# Erstmaliger Start: initdb wird unten automatisch ausgeführt wenn PG_VERSION fehlt.

# Ownership setzen — auf Network Volume tolerieren wir Fehler
# (manche RunPod-Mounts verweigern chown, dann greift eine andere Strategie unten)
chown -R "$PG_USER" "$PG_DATA" 2>/dev/null || {
    echo "${WARN}chown auf $PG_DATA nicht möglich (Network-Volume-Limit)"
    echo "         Fallback: setze alle Files world-writable für PG-User-Zugriff"
    chmod -R u+rwX,g+rwX,o+rwX "$PG_DATA" 2>/dev/null || true
}
chmod 700 "$PG_DATA" 2>/dev/null || chmod 755 "$PG_DATA"

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

    # pgvector Extension aktivieren
    su -m "$PG_USER" -c "psql -d systelios -c \"CREATE EXTENSION IF NOT EXISTS vector;\"" 2>/dev/null \
        && echo "${OK}pgvector aktiviert" \
        || echo "${WARN}pgvector konnte nicht aktiviert werden"

    echo "${OK}Datenbank 'systelios' bereit"
fi

# 3. Ollama
echo ""
echo "${GO}Ollama pruefen..."

OLLAMA_BIN="/workspace/bin/ollama"
OLLAMA_MIN_VERSION="0.17.0"
mkdir -p /workspace/bin

# Versionsnummer vergleichen (major.minor.patch)
_version_gte() {
    # Gibt 0 (true) zurueck wenn $1 >= $2
    local a="$1" b="$2"
    [ "$(printf '%s\n%s' "$a" "$b" | sort -V | head -1)" = "$b" ]
}

_ollama_version() {
    "$OLLAMA_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1
}

_install_ollama() {
    echo "${GO}Ollama installieren/aktualisieren..."

    # zstd MUSS installiert sein BEVOR Ollama installiert wird!
    # Ohne zstd laed das Install-Skript das abgespeckte .tgz-Fallback-Archiv,
    # dem cuda_v13-Runner UND libggml-base.so.0 fehlen.
    # → GPU wird nicht erkannt, Ollama faellt still auf CPU zurueck.
    if ! command -v zstd >/dev/null 2>&1; then
        echo "${GO}zstd installieren (benoetigt fuer Ollama GPU-Support)..."
        apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd
    fi

    # Ollama installieren – OHNE Version-Pin, damit die neueste Version kommt.
    # Blackwell-GPUs (RTX Pro 4500, RTX 50xx) benoetigen Ollama >= 0.17 (cuda_v13).
    curl -fsSL https://ollama.com/install.sh | sh

    # Nach Installation: Binary in /workspace/bin sichern (bleibt bei Pod-Neustart)
    if [ -f /usr/local/bin/ollama ]; then
        cp /usr/local/bin/ollama "$OLLAMA_BIN"
        echo "${OK}Ollama $("$OLLAMA_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1) installiert → $OLLAMA_BIN"
    else
        echo "${ERR}Ollama-Binary nicht gefunden nach Installation"
        exit 1
    fi

    # Verifizieren: cuda_v13 UND libggml-base.so muessen vorhanden sein.
    # Fehlen sie, wurde das .tgz-Fallback geladen (zstd war beim Install nicht da).
    _verify_ollama_cuda_libs

    # Nach Installation immer neu starten damit cuda_v13 aktiv wird
    pkill -f "ollama serve" 2>/dev/null || true
    sleep 2
}

# ── Ollama CUDA-Library Verifikation ──────────────────────────────────────────
# Stellt sicher dass cuda_v13 UND libggml-base.so.0 vorhanden sind.
# Ohne diese Files erkennt Ollama Blackwell-GPUs nicht und faellt auf CPU zurueck.
#
# WICHTIG: Ollama sucht Libraries relativ zum Binary-Pfad.
#   Binary in /usr/local/bin/ollama  → sucht /usr/local/lib/ollama/
#   Binary in /workspace/bin/ollama  → sucht /workspace/lib/ollama/
# Das Install-Skript installiert nach /usr/local/, wir kopieren das Binary
# nach /workspace/bin/ (persistent ueber Pod-Neustarts). Daher muessen die
# Libraries an BEIDEN Stellen liegen.
_verify_ollama_cuda_libs() {
    local INSTALL_LIB="/usr/local/lib/ollama"
    local WORKSPACE_LIB="/workspace/lib/ollama"
    local NEEDS_FIX=false

    # Pruefung 1: cuda_v13-Verzeichnis mit libggml-cuda.so (am Install-Ort)
    if [ ! -f "$INSTALL_LIB/cuda_v13/libggml-cuda.so" ]; then
        echo "${WARN}cuda_v13/libggml-cuda.so fehlt in $INSTALL_LIB"
        NEEDS_FIX=true
    fi

    # Pruefung 2: libggml-base.so.0 im Hauptverzeichnis
    if ! ls "$INSTALL_LIB"/libggml-base.so* >/dev/null 2>&1; then
        echo "${WARN}libggml-base.so fehlt (kritisch fuer GPU-Erkennung!)"
        NEEDS_FIX=true
    fi

    if [ "$NEEDS_FIX" = "true" ]; then
        echo "${GO}Fehlende CUDA-Libraries aus .tar.zst-Vollarchiv nachladen..."

        # zstd sicherstellen
        if ! command -v zstd >/dev/null 2>&1; then
            apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd
        fi

        # Vollarchiv herunterladen (.tar.zst enthaelt ALLE Libraries)
        local ARCH=$(uname -m)
        [ "$ARCH" = "x86_64" ] && ARCH="amd64"
        local ZST_URL="https://ollama.com/download/ollama-linux-${ARCH}.tar.zst"
        local TMP_ZST="/tmp/ollama-full.tar.zst"
        local TMP_EXTRACT="/tmp/ollama-lib-extract"

        echo "${GO}Lade $ZST_URL ..."
        curl -fsSL "$ZST_URL" -o "$TMP_ZST"

        # Alle Libraries extrahieren (cuda_v12, cuda_v13, libggml-base, etc.)
        rm -rf "$TMP_EXTRACT"
        mkdir -p "$TMP_EXTRACT"
        tar --use-compress-program=unzstd -xf "$TMP_ZST" -C "$TMP_EXTRACT" --wildcards 'lib/ollama/*'

        # Alles nach /usr/local/lib/ollama/ kopieren
        if [ -d "$TMP_EXTRACT/lib/ollama" ]; then
            cp -rf "$TMP_EXTRACT/lib/ollama/"* "$INSTALL_LIB/"
            chmod -R 755 "$INSTALL_LIB/"
            echo "${OK}CUDA-Libraries nach $INSTALL_LIB installiert"
        else
            echo "${ERR}Konnte Libraries nicht aus Archiv extrahieren"
        fi

        # Aufraeumen
        rm -f "$TMP_ZST"
        rm -rf "$TMP_EXTRACT"
    else
        echo "${OK}Ollama CUDA-Libraries vollstaendig (cuda_v12 + cuda_v13 + libggml-base)"
    fi

    # Libraries fuer /workspace/bin/ollama verfuegbar machen.
    # Das Binary sucht relativ zu seinem Pfad → /workspace/lib/ollama/.
    # Statt Kopie: Symlink auf /usr/local/lib/ollama (kein Duplikat,
    # bleibt automatisch aktuell bei Ollama-Updates).
    if [ -d "$INSTALL_LIB" ]; then
        local WORKSPACE_LIB_PARENT="/workspace/lib"
        local WORKSPACE_LIB="$WORKSPACE_LIB_PARENT/ollama"
        mkdir -p "$WORKSPACE_LIB_PARENT"
        # Altes Verzeichnis/Symlink entfernen falls vorhanden
        if [ -L "$WORKSPACE_LIB" ]; then
            rm -f "$WORKSPACE_LIB"
        elif [ -d "$WORKSPACE_LIB" ]; then
            rm -rf "$WORKSPACE_LIB"
        fi
        ln -sf "$INSTALL_LIB" "$WORKSPACE_LIB"
        echo "${OK}Libraries verlinkt: $WORKSPACE_LIB → $INSTALL_LIB"
    fi
}

if [ ! -f "$OLLAMA_BIN" ]; then
    _install_ollama
else
    CURRENT_VER=$(_ollama_version)
    if [ -z "$CURRENT_VER" ]; then
        echo "${WARN}Ollama-Version nicht lesbar – neu installieren..."
        _install_ollama
    elif _version_gte "$CURRENT_VER" "$OLLAMA_MIN_VERSION"; then
        echo "${OK}Ollama v${CURRENT_VER} (>= ${OLLAMA_MIN_VERSION} erforderlich fuer Blackwell + qwen3)"
        # Pruefen ob GPU-Libraries vorhanden – fehlen wenn zstd beim
        # letzten Install nicht installiert war (tgz-Fallback hat nur cuda_v12).
        if ! ls /usr/local/lib/ollama/cuda_v12/libggml-cuda.so >/dev/null 2>&1 && \
           ! ls /usr/local/lib/ollama/cuda_v13/libggml-cuda.so >/dev/null 2>&1; then
            echo "${WARN}Ollama CUDA-Libraries fehlen – neu installieren mit GPU-Support..."
            pkill -f "ollama serve" 2>/dev/null || true
            sleep 2
            _install_ollama
        else
            # Libraries da, aber vollstaendig? (libggml-base.so kann fehlen)
            _verify_ollama_cuda_libs
        fi
    else
        echo "${WARN}Ollama v${CURRENT_VER} zu alt (mind. v${OLLAMA_MIN_VERSION} fuer qwen3) – aktualisiere..."
        pkill -f "ollama serve" 2>/dev/null || true
        sleep 2
        _install_ollama
    fi
fi

# Symlink damit 'ollama' systemweit verfuegbar ist
ln -sf "$OLLAMA_BIN" /usr/local/bin/ollama 2>/dev/null || \
    ln -sf "$OLLAMA_BIN" /usr/bin/ollama 2>/dev/null || true

# systemd-Ollama-Service deaktivieren – er laeuft ohne OLLAMA_MODELS und
# ohne korrekte Library-Pfade → GPU wird nicht erkannt.
if systemctl is-active ollama >/dev/null 2>&1; then
    echo "${WARN}systemd-Ollama-Service aktiv – wird deaktiviert..."
    systemctl stop ollama 2>/dev/null || true
    systemctl disable ollama 2>/dev/null || true
    sleep 2
    echo "${OK}systemd-Ollama-Service gestoppt"
fi

# Laufenden Ollama-Prozess pruefen
# Strategie: Wenn Ollama laeuft UND GPU erkennt → nicht neu starten.
# Nur neu starten wenn kein Prozess laeuft oder GPU nicht erkannt wird.
NEED_RESTART=false
OLLAMA_GPU_OK=false

if pgrep -f "ollama serve" >/dev/null 2>&1; then
    # Ollama laeuft – GPU-Status per API pruefen (schneller als Log-Analyse)
    sleep 2
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        # Kurzen Testaufruf machen und VRAM pruefen
        GPU_INFO=$(curl -s http://localhost:11434/api/ps 2>/dev/null)
        if grep -q "library=cuda" "$LOG_DIR/ollama.log" 2>/dev/null; then
            OLLAMA_GPU_OK=true
            echo "${OK}Ollama laeuft mit GPU-Support"
        else
            echo "${WARN}Ollama laeuft aber ohne GPU – neu starten..."
            pkill -f "ollama serve" 2>/dev/null || true
            sleep 3
            NEED_RESTART=true
        fi
    else
        pkill -f "ollama serve" 2>/dev/null || true
        sleep 3
        NEED_RESTART=true
    fi
else
    NEED_RESTART=true
fi

export OLLAMA_MODELS="$OLLAMA_MODELS_DIR"
if [ "$NEED_RESTART" = "true" ]; then
    echo "${GO}Ollama starten..."

    # CUDA-Library-Pfad ermitteln
    # Ollama 0.18.x bringt eigene CUDA-Libraries unter /usr/local/lib/ollama mit
    OLLAMA_LIB_PATH=""
    for cuda_dir in \
        /usr/local/lib/ollama/cuda_v13 \
        /usr/local/lib/ollama/cuda_v12 \
        /usr/local/lib/ollama \
        /workspace/lib/ollama/cuda_v13 \
        /workspace/lib/ollama/cuda_v12 \
        /workspace/lib/ollama; do
        if [ -d "$cuda_dir" ] && ls "$cuda_dir"/libggml-cuda.so >/dev/null 2>&1; then
            OLLAMA_LIB_PATH="$cuda_dir"
            echo "${OK}Ollama CUDA-Libraries: $cuda_dir"
            break
        fi
    done

    # Blackwell-GPU erkennen (Compute Capability 12.0 = sm_120)
    # Betrifft: RTX Pro 4500/5000/6000 Blackwell, RTX 5070/5080/5090
    # Diese GPUs brauchen cuda_v13 – ohne OLLAMA_LLM_LIBRARY=cuda_v13
    # faellt Ollama still auf CPU zurueck.
    OLLAMA_LLM_LIB=""
    GPU_COMPUTE=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')
    if [ -n "$GPU_COMPUTE" ]; then
        GPU_MAJOR=$(echo "$GPU_COMPUTE" | cut -d. -f1)
        if [ "$GPU_MAJOR" -ge 12 ] 2>/dev/null; then
            echo "${OK}Blackwell-GPU erkannt (Compute $GPU_COMPUTE) → cuda_v13 wird verwendet"
            OLLAMA_LLM_LIB="cuda_v13"
        else
            echo "${OK}GPU erkannt (Compute $GPU_COMPUTE) → cuda_v12 wird verwendet"
        fi
    else
        echo "${WARN}GPU Compute Capability nicht lesbar – Ollama waehlt automatisch"
    fi

    # Ollama starten – kein CUDA_VISIBLE_DEVICES (verhindert GPU-Erkennung in 0.9+)
    export OLLAMA_MODELS="$OLLAMA_MODELS_DIR"
    export OLLAMA_DEBUG=1
    if [ -n "$OLLAMA_LLM_LIB" ]; then
        export OLLAMA_LLM_LIBRARY="$OLLAMA_LLM_LIB"
    fi
    nohup "$OLLAMA_BIN" serve > "$LOG_DIR/ollama.log" 2>&1 &
    sleep 10

    # GPU-Erkennung pruefen.
    # Ollama 0.18+ loggt GPU-Info beim Start wenn OLLAMA_DEBUG gesetzt ist.
    # Wir pruefen den Log direkt statt ein Modell zu laden (das blockiert).
    if grep -q "library=CUDA" "$LOG_DIR/ollama.log" 2>/dev/null; then
        VRAM=$(grep "total=" "$LOG_DIR/ollama.log" 2>/dev/null | grep -oE 'total="[^"]+"' | tail -1)
        GPU_NAME=$(grep "description=" "$LOG_DIR/ollama.log" 2>/dev/null | grep -oE 'description="[^"]+"' | tail -1)
        echo "${OK}GPU aktiv: ${GPU_NAME} (${VRAM})"
    elif grep -q "initial_count=0\|total_vram.*0 B" "$LOG_DIR/ollama.log" 2>/dev/null; then
        echo "${WARN}GPU nicht erkannt – Ollama laeuft auf CPU"
        echo "${WARN}Pruefe: Libraries in /workspace/lib/ollama/cuda_v13/ vorhanden?"
    else
        # Kein Debug-Log → kann GPU-Status nicht pruefen, wird beim Warmup klar
        echo "${OK}Ollama gestartet (GPU-Status wird beim Warmup geprueft)"
    fi
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

# .env-Migration: alten/falschen Modellnamen korrigieren
if [ -f "$BACKEND_DIR/.env" ]; then
    if grep -q "OLLAMA_MODEL=mistral-nemo\|OLLAMA_MODEL=qwen2.5\|OLLAMA_MODEL=qwen2.5:32b\|OLLAMA_MODEL=qwen3:30b" "$BACKEND_DIR/.env"; then
        echo "${WARN}.env enthaelt veralteten/suboptimalen Modellnamen – migriere auf qwen3:32b..."
        sed -i 's|OLLAMA_MODEL=.*|OLLAMA_MODEL=qwen3:32b|' "$BACKEND_DIR/.env"
        echo "${OK}OLLAMA_MODEL=qwen3:32b gesetzt"
    fi
fi

# Empfohlene Modelle:
#   RTX Pro 4500 / 32GB:  qwen3:32b       (~19GB)  <- Empfehlung
#   RTX 4090 / 24GB:      qwen3:32b-q4_K_S        (~18GB)  <- Empfehlung
#   Alternative (beide):  qwen3:14b-q5_K_M        (~10GB)
#   Reasoning:            deepseek-r1:32b          (~19GB)
LLM_MODEL=$(grep "^OLLAMA_MODEL=" "$BACKEND_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "qwen3:32b")
for model in "$LLM_MODEL" "nomic-embed-text"; do
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

# Defektes venv erkennen: activate vorhanden aber pip fehlt oder ist kaputt
VENV_BROKEN=false
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
    if ! python -m pip --version >/dev/null 2>&1; then
        echo "${WARN}venv defekt (pip fehlt) – loesche und erstelle neu..."
        deactivate 2>/dev/null || true
        rm -rf "$VENV_DIR"
        VENV_BROKEN=true
    fi
fi

if [ ! -f "$VENV_DIR/bin/activate" ] || [ "$VENV_BROKEN" = "true" ]; then
    echo "${GO}Virtual Environment anlegen..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    python -m pip install --quiet --upgrade pip
    echo "${OK}venv angelegt"
else
    source "$VENV_DIR/bin/activate"
fi

if ! python -c "import fastapi" 2>/dev/null; then
    echo "${GO}Python-Pakete installieren (kann 5-10 Min dauern)..."
    python -m pip install --quiet --upgrade pip
    if python -m pip install -r "$BACKEND_DIR/requirements.txt"; then
        echo "${OK}Pakete installiert"
    else
        echo "${ERR}requirements.txt Installation fehlgeschlagen"
        exit 1
    fi
else
    echo "${OK}Python-Pakete vorhanden"
fi

# pyannote.audio – nur installieren wenn DIARIZATION_ENABLED=true
DIARIZATION_ENABLED=$(grep "^DIARIZATION_ENABLED=" "$BACKEND_DIR/.env" 2>/dev/null \
    | cut -d= -f2 | tr -d '"' | tr '[:upper:]' '[:lower:]')
if [ "$DIARIZATION_ENABLED" = "true" ]; then
    if ! pip show pyannote.audio >/dev/null 2>&1; then
        echo "${GO}pyannote.audio installieren (~500MB + Abhaengigkeiten, 3-8 Min)..."
        if python -m pip install pyannote.audio; then
            echo "${OK}pyannote.audio installiert"
        else
            echo "${WARN}pyannote.audio Installation fehlgeschlagen – Diarization nicht verfuegbar"
        fi
    else
        echo "${OK}pyannote.audio vorhanden"
    fi
    HF_TOKEN=$(grep "^DIARIZATION_HF_TOKEN=" "$BACKEND_DIR/.env" 2>/dev/null \
        | cut -d= -f2 | tr -d '"')
    if [ -z "$HF_TOKEN" ] || [ "$HF_TOKEN" = "hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" ]; then
        echo "${WARN}DIARIZATION_HF_TOKEN fehlt in .env – Diarization wird deaktiviert."
        echo "       Token erstellen: huggingface.co/settings/tokens"
        echo "       Modell freischalten: huggingface.co/pyannote/speaker-diarization-3.1"
    else
        echo "${OK}HuggingFace Token gesetzt – pyannote Diarization aktiv"
    fi
else
    echo "${OK}Sprecher-Diarization deaktiviert (Pausen-Heuristik aktiv)"
fi

# 5. Frontend bauen
echo ""
echo "${GO}Frontend pruefen..."

FRONTEND_DIR="/workspace/scriptTelios/frontend"
STATIC_DIR="$BACKEND_DIR/static"
BUNDLE="$STATIC_DIR/systelios.js"

# Node.js pruefen / installieren
if ! command -v node >/dev/null 2>&1; then
    echo "${GO}Node.js installieren..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
    echo "${OK}Node.js $(node --version) installiert"
else
    echo "${OK}Node.js $(node --version) vorhanden"
fi

# Pruefen ob Bundle aktueller als Quellcode ist
NEEDS_BUILD=false
if [ ! -f "$BUNDLE" ]; then
    NEEDS_BUILD=true
    echo "${GO}Bundle nicht vorhanden – wird gebaut..."
elif [ "$FRONTEND_DIR/klinische-dokumentation.jsx" -nt "$BUNDLE" ]; then
    NEEDS_BUILD=true
    echo "${GO}Quellcode neuer als Bundle – wird neu gebaut..."
else
    echo "${OK}Bundle aktuell – kein Rebuild noetig"
fi

if [ "$NEEDS_BUILD" = "true" ]; then
    mkdir -p "$STATIC_DIR"
    cd "$FRONTEND_DIR"
    # node_modules nur bei Bedarf installieren
    if [ ! -d "node_modules" ] || [ "package.json" -nt "node_modules/.package-lock.json" ]; then
        echo "${GO}npm install..."
        npm install --silent
    fi
    echo "${GO}npm run build..."
    npm run build
    if [ -f "$BUNDLE" ]; then
        BUNDLE_KB=$(du -k "$BUNDLE" | cut -f1)
        echo "${OK}Bundle erstellt: systelios.js (${BUNDLE_KB} KB)"
    else
        echo "${WARN}Bundle wurde nicht erstellt – Frontend evtl. nicht verfuegbar"
    fi
    cd "$BACKEND_DIR"
fi

# 6. .env pruefen
echo ""
if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo "${WARN}Keine .env - erstelle automatisch..."
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    # Confluence-URL: Argument > Default
    CONF_URL="${ARG_CONFLUENCE:-http://intranet.systelios.local}"
    # Modell: Argument > Default
    MODEL_NAME="${ARG_MODEL:-qwen3:32b}"
    # Diarization: automatisch aktivieren wenn Token mitgegeben
    DIAR_ENABLED="false"
    DIAR_TOKEN=""
    if [ -n "$ARG_HF_TOKEN" ]; then
        DIAR_ENABLED="true"
        DIAR_TOKEN="$ARG_HF_TOKEN"
        echo "${OK}HuggingFace Token gesetzt – Diarization wird aktiviert"
    fi
    cat > "$BACKEND_DIR/.env" << ENVEOF
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=${MODEL_NAME}
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
DATABASE_URL=postgresql+asyncpg://systelios:systelios@127.0.0.1:5432/systelios
SECRET_KEY=${SECRET}
DELETE_AUDIO_AFTER_TRANSCRIPTION=true
LOG_LEVEL=INFO
UPLOAD_DIR=/workspace/uploads
OUTPUT_DIR=/workspace/outputs
LOG_FILE=/workspace/systelios.log
# Confluence-Intranet-URL fuer CORS:
CONFLUENCE_URL=${CONF_URL}
# Cloudflare-Tunnel und RunPod-Proxy fuer Testphase erlauben:
ALLOW_RUNPOD_PROXY=true
ALLOW_CLOUDFLARE_TUNNEL=true
# Sprecher-Diarization (pyannote.audio):
DIARIZATION_ENABLED=${DIAR_ENABLED}
DIARIZATION_HF_TOKEN=${DIAR_TOKEN}
ENVEOF
    echo "${OK}.env erstellt (SECRET_KEY automatisch generiert)"
else
    # Sicherstellen dass korrekte DATABASE_URL gesetzt ist
    sed -i 's|@db:5432|@127.0.0.1:5432|g'        "$BACKEND_DIR/.env"
    sed -i 's|@localhost:5432|@127.0.0.1:5432|g'   "$BACKEND_DIR/.env"
    sed -i 's|?ssl=false||g'                        "$BACKEND_DIR/.env"
    sed -i 's|?sslmode=disable||g'                  "$BACKEND_DIR/.env"
    # CLI-Argumente in bestehende .env ueberschreiben
    if [ -n "$ARG_HF_TOKEN" ]; then
        sed -i "s|DIARIZATION_HF_TOKEN=.*|DIARIZATION_HF_TOKEN=${ARG_HF_TOKEN}|" "$BACKEND_DIR/.env"
        sed -i "s|DIARIZATION_ENABLED=false|DIARIZATION_ENABLED=true|"           "$BACKEND_DIR/.env"
        echo "${OK}HuggingFace Token aktualisiert – Diarization aktiviert"
    fi
    if [ -n "$ARG_CONFLUENCE" ]; then
        sed -i "s|CONFLUENCE_URL=.*|CONFLUENCE_URL=${ARG_CONFLUENCE}|" "$BACKEND_DIR/.env"
        echo "${OK}Confluence-URL aktualisiert: ${ARG_CONFLUENCE}"
    fi
    if [ -n "$ARG_MODEL" ]; then
        sed -i "s|OLLAMA_MODEL=.*|OLLAMA_MODEL=${ARG_MODEL}|" "$BACKEND_DIR/.env"
        echo "${OK}Ollama-Modell aktualisiert: ${ARG_MODEL}"
    fi
    echo "${OK}.env vorhanden"
fi

# 7. Backend starten
echo ""
echo "${GO}Backend starten..."
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 2

cd "$BACKEND_DIR"
source "$VENV_DIR/bin/activate"

# Umgebungsvariablen sauber laden
unset DATABASE_URL
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

nohup python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    >> "$LOG_DIR/backend.log" 2>&1 &

echo "${OK}Backend gestartet (PID: $!)"

# 8. Health Check
echo "${GO}Warte auf Backend..."
MAX=20
for i in $(seq 1 $MAX); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        echo "${OK}Backend erreichbar (http://localhost:8000)"
        break
    fi
    if [ "$i" = "$MAX" ]; then
        echo "${WARN}Backend antwortet nicht - Log pruefen:"
        echo "       tail -50 $LOG_DIR/backend.log"
    else
        printf "       Warte... (%d/%d)\r" "$i" "$MAX"
        sleep 3
    fi
done

# 8b. Ollama-Modell in VRAM vorwaermen (im Hintergrund, blockiert Start nicht)
# Erstmaliges Laden eines 20GB-Modells dauert 60-90s (Disk→VRAM).
OLLAMA_MODEL=$(grep "^OLLAMA_MODEL=" "$BACKEND_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "qwen3:32b")
(
    WARMUP_RESPONSE=$(curl -s -X POST http://localhost:11434/api/generate \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${OLLAMA_MODEL}\", \"prompt\": \"/no_think\", \"keep_alive\": -1, \"stream\": false, \"options\": {\"num_predict\": 1}}" \
        --max-time 180 2>/dev/null) || true
    if echo "$WARMUP_RESPONSE" | grep -q '"done":true'; then
        echo "[OK]    ${OLLAMA_MODEL} im VRAM geladen (Hintergrund-Warmup abgeschlossen)"
    else
        echo "[WARN]  Warmup fehlgeschlagen – erster Request laedt Modell"
    fi
) &
echo "${OK}${OLLAMA_MODEL} Warmup gestartet (Hintergrund, ~60-90s)"

# 9. Cloudflare Named Tunnel starten (feste URL: https://scriptelios.win)
echo ""
echo "${GO}Cloudflare Named Tunnel pruefen..."

CLOUDFLARED_BIN="/workspace/bin/cloudflared"
TUNNEL_HOSTNAME="scriptelios.win"
TUNNEL_URL="https://${TUNNEL_HOSTNAME}"

# cloudflared installieren falls nicht vorhanden
if [ ! -f "$CLOUDFLARED_BIN" ]; then
    echo "${GO}cloudflared installieren..."
    mkdir -p "$(dirname "$CLOUDFLARED_BIN")"
    curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
        -o "$CLOUDFLARED_BIN"
    chmod +x "$CLOUDFLARED_BIN"
    echo "${OK}cloudflared installiert"
fi

# Alten Tunnel stoppen
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 2

# Token pruefen
if [ -z "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
    # Fallback: Token aus /workspace/cloudflare/.env laden
    if [ -f "/workspace/cloudflare/.env" ]; then
        set -a
        source /workspace/cloudflare/.env
        set +a
    fi
fi

if [ -z "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
    echo "${ERR}CLOUDFLARE_TUNNEL_TOKEN nicht gesetzt."
    echo "         Entweder als Pod-Env-Variable setzen oder in /workspace/cloudflare/.env:"
    echo "           CLOUDFLARE_TUNNEL_TOKEN=\"eyJhIjoi...\""
    echo "${WARN}Tunnel wird nicht gestartet - Backend nur lokal erreichbar."
    TUNNEL_URL=""
else
    # Named Tunnel mit Token starten (Hostname-Config liegt in Cloudflare Zero Trust)
    nohup "$CLOUDFLARED_BIN" tunnel \
        --no-autoupdate \
        run \
        --token "$CLOUDFLARE_TUNNEL_TOKEN" \
        > "$LOG_DIR/cloudflared.log" 2>&1 &
    CLOUDFLARED_PID=$!

    # Auf erfolgreiche Verbindung warten
    echo "${GO}Warte auf Tunnel-Verbindung..."
    MAX=15
    CONNECTED=false
    for i in $(seq 1 $MAX); do
        if ! kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
            echo "${ERR}cloudflared-Prozess beendet. Letzte Log-Zeilen:"
            tail -20 "$LOG_DIR/cloudflared.log"
            TUNNEL_URL=""
            break
        fi
        if grep -q "Registered tunnel connection" "$LOG_DIR/cloudflared.log" 2>/dev/null; then
            echo "${OK}Tunnel verbunden: $TUNNEL_URL"
            CONNECTED=true
            break
        fi
        [ "$i" = "$MAX" ] && {
            echo "${WARN}Tunnel-Verbindung nicht bestaetigt innerhalb ${MAX}s"
            echo "         Log: $LOG_DIR/cloudflared.log"
        }
        sleep 2
    done
fi

# 10. Zusammenfassung
echo ""
echo "================================================"
echo "  Backend:   http://localhost:8000"
echo "  API-Docs:  http://localhost:8000/docs"
echo "  Ollama:    http://localhost:11434"
echo ""
if [ -n "$TUNNEL_URL" ]; then
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║  BACKEND-URL (feste Named-Tunnel-URL):           ║"
echo "  ║                                                  ║"
echo "  ║  $TUNNEL_URL"
echo "  ║                                                  ║"
echo "  ║  Im Confluence-Macro einmalig eintragen,         ║"
echo "  ║  danach nie wieder aendern.                      ║"
echo "  ╚══════════════════════════════════════════════════╝"
else
echo "${WARN}  Kein Tunnel aktiv - siehe Fehlermeldung oben"
echo "         Log: $LOG_DIR/cloudflared.log"
fi
echo ""
echo "  Logs:"
echo "    tail -f $LOG_DIR/backend.log"
echo "    tail -f $LOG_DIR/cloudflared.log"
echo "    tail -f $LOG_DIR/ollama.log"
echo "    tail -f $LOG_DIR/postgres.log"
echo ""
echo "  Stop:  pkill -f uvicorn; pkill cloudflared; pkill ollama"
echo "  Stop PostgreSQL:"
echo "    su -m $PG_USER -c '$PG_BIN/pg_ctl -D $PG_DATA stop'"

# PG-Daten-Sync ENTFERNT (Variante B):
# PostgreSQL schreibt direkt auf /workspace/postgres_data (Network Volume),
# keine periodische Backup-Kopie mehr nötig.
#
# Bei Pod-Terminate bleiben die Daten automatisch erhalten, da /workspace
# ein persistentes Network Volume ist.

# Kein Shutdown-Trap mehr nötig (PG schreibt direkt auf Network Volume)
echo "================================================"
echo ""
