"""
Konfiguration – alle Einstellungen aus Umgebungsvariablen.
Kopiere .env.example zu .env und passe die Werte an.

DATENSCHUTZ-HINWEIS:
  Alle LLM-Anfragen gehen ausschliesslich an den lokalen Ollama-Dienst.
  Es werden keine Daten an externe APIs (Anthropic, OpenAI etc.) gesendet.
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── LLM (ausschliesslich Ollama, lokal) ──────────────────────
    OLLAMA_HOST:  str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:32b"

    # Vision-Modell fuer OCR-Fallback (Stufe 3: wenn pdfplumber + Tesseract scheitern)
    # Modell einmalig laden: ollama pull llava
    VISION_MODEL: str = "llava"

    @property
    def LLM_MODEL(self) -> str:
        return f"ollama/{self.OLLAMA_MODEL}"

    # ── Whisper / Transkription (lokal) ───────────────────────────
    WHISPER_MODEL:        str = "medium"   # tiny | base | small | medium | large-v3
    WHISPER_DEVICE:       str = "cpu"      # cpu | cuda
    WHISPER_COMPUTE_TYPE: str = "int8"     # int8 | float16 | float32

    # Ollama vor Whisper aus VRAM entladen.
    # Nur nötig wenn Whisper + Ollama nicht gleichzeitig in den VRAM passen.
    # RTX 4090 (24GB): large-v3 (~3GB) + qwen3:32b (~5GB) passen gleichzeitig
    # → False lassen, spart ~30s Kaltstart des LLM nach jeder Transkription.
    # Kleinere GPUs (<12GB): auf True setzen.
    WHISPER_FREE_OLLAMA_VRAM: bool = False

    # ── Sprecher-Diarization (pyannote.audio) ─────────────────────
    # Echte Sprecher-Erkennung statt einfacher Pausen-Heuristik.
    # Benötigt: pip install pyannote.audio
    # Benötigt: HuggingFace-Token mit Zugriff auf pyannote/speaker-diarization-3.1
    #   → huggingface.co/pyannote/speaker-diarization-3.1 (kostenlos, Zugang beantragen)
    #   → huggingface.co/settings/tokens → Token generieren
    # DIARIZATION_ENABLED=false: verwendet die alte Pausen-Heuristik (kein Token nötig)
    DIARIZATION_ENABLED:    bool = False
    DIARIZATION_HF_TOKEN:   str  = ""   # HuggingFace Access Token
    # Modell wird beim ersten Aufruf heruntergeladen und gecacht
    DIARIZATION_MODEL:      str  = "pyannote/speaker-diarization-3.1"

    # ── Dateien ───────────────────────────────────────────────────
    UPLOAD_DIR:      str = "uploads"
    OUTPUT_DIR:      str = "outputs"
    RECORDINGS_DIR:  str = "/workspace/recordings"   # P0-Aufnahmen (persistent)
    MAX_UPLOAD_MB:   int = 100

    # v18: Audio wird NICHT mehr sofort nach Transkription gelöscht.
    # retention.py löscht Audiodateien nach 24h automatisch.
    # Das gibt dem Therapeuten Zeit das Audio zu prüfen/herunterzuladen.
    DELETE_AUDIO_AFTER_TRANSCRIPTION: bool = False

    # ── Datenbank ─────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://systelios:systelios@db:5432/systelios"

    # ── Sicherheit ────────────────────────────────────────────────
    SECRET_KEY:                    str = "BITTE-AENDERN-IN-PRODUKTION"

    # ── Datenschutz: Auth (K1) ───────────────────────────────────
    AUTH_ENABLED:                  bool = True
    CONFLUENCE_SHARED_SECRET:      str  = "CHANGE-ME-USE-secrets.token_urlsafe-32"
    AUTH_TIMESTAMP_WINDOW_SEC:     int  = 300

    # ── Datenschutz: CORS-Härtung (K3) ───────────────────────────
    # Komma-Liste erlaubter Origins, z.B. "http://intranet.systelios.local"
    # Wenn leer: Fallback auf CORS_ORIGINS (siehe unten)
    ALLOWED_ORIGINS:               str  = ""

    # ── Datenschutz: Audit-Log (E1) ──────────────────────────────
    AUDIT_LOG_PATH:                str  = "/workspace/audit.log"


    # ── Retention (E2) ───────────────────────────────────────────
    RETENTION_INTERVAL_HOURS: int = 6

    # ── Rate Limit (O1) ──────────────────────────────────────────
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS_PER_HOUR: int = 100
    RATE_LIMIT_PARALLEL_JOBS: int = 5
    ACCESS_TOKEN_EXPIRE_MINUTES:   int = 480   # 8 Stunden

    # ── CORS ──────────────────────────────────────────────────────
    # Confluence-Instanz eintragen (internes Netz):
    # z.B. "http://intranet.systelios.local" oder "https://wiki.systelios.de"
    CONFLUENCE_URL: str = ""

    # Zusaetzliche CORS-Origins (kommagetrennt, fuer weitere Clients):
    # z.B. "https://mein-server.de,https://weiterer-client.de"
    EXTRA_CORS_ORIGINS: str = ""

    @property
    def CORS_ORIGINS(self) -> List[str]:
        origins = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
        ]
        if self.CONFLUENCE_URL:
            origins.append(self.CONFLUENCE_URL.rstrip("/"))
        if self.EXTRA_CORS_ORIGINS:
            for o in self.EXTRA_CORS_ORIGINS.split(","):
                o = o.strip()
                if o:
                    origins.append(o)
        return origins

    @property
    def CORS_ALLOW_ORIGIN_REGEX(self) -> str:
        """
        Regex fuer dynamische Origins die nicht vorab bekannt sind.

        RunPod-URLs aendern sich bei jedem Neustart (neue Pod-ID),
        Cloudflare-Tunnel-URLs aendern sich ebenfalls bei jedem Neustart.
        Beide koennen per Flag aktiviert werden.
        In Produktion (feste URL) beide Flags deaktivieren und
        stattdessen CONFLUENCE_URL setzen.
        """
        patterns = []
        if self.ALLOW_RUNPOD_PROXY:
            patterns.append(r"https://[\w-]+\.proxy\.runpod\.net")
        if self.ALLOW_CLOUDFLARE_TUNNEL:
            patterns.append(r"https://[\w-]+\.trycloudflare\.com")
        if patterns:
            return "|".join(f"({p})" for p in patterns)
        return ""

    # RunPod-Proxy-Domain erlauben (nur fuer Testphase)
    ALLOW_RUNPOD_PROXY: bool = False

    # Cloudflare-Tunnel-Domain erlauben (nur fuer Testphase)
    # Aktivieren wenn Backend per 'cloudflared tunnel' erreichbar ist
    ALLOW_CLOUDFLARE_TUNNEL: bool = False

    # ── Logging ───────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FILE:  str = "/workspace/systelios.log"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",      # <-- statt "forbid": unbekannte ENVs einfach ignorieren
        case_sensitive=False,
    )
