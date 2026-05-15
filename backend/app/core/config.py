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
        # ENVs, die nicht als Field deklariert sind, werden ignoriert statt
        # einen ValidationError zu werfen. Notwendig, weil runpod-start.sh
        # die komplette .env in die Prozess-Umgebung exportiert (inkl.
        # DB_EXPECTED_OWNER, SYSTELIOS_APP_PASSWORD, Cloudflare-Token etc.).
        extra="ignore",
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

    # ── v19.2 Two-Stage-Pipeline (Verlauf-Verdichtung) ───────────
    # Stage 1: separater LLM-Call der die rohe Verlaufsdokumentation auf
    # eine strukturierte, quellentreue Zusammenfassung verdichtet. Stage 2
    # (eigentliche Antrags-Generierung) bekommt dann die Summary statt der
    # vollen Doku als Verlauf-Input.
    #
    # STAGE1_ENABLED:
    #   true  -> Stage 1 wird fuer verlaengerung/folgeverlaengerung/
    #            entlassbericht aktiv, wenn die bereinigte Verlaufsdoku
    #            >=1500 Woerter hat.
    #   false -> Stage 1 wird komplett uebersprungen (Pre-v19.2-Verhalten).
    # Notabschalter falls Stage 1 in Produktion Probleme macht.
    STAGE1_ENABLED: bool = True

    # Zielwortzahl der Stage-1-Zusammenfassung. Stage 2 bekommt ca. so viele
    # Woerter Verlauf-Input statt der Rohdoku (typisch ~10-13k Woerter).
    # Akzeptiert wird 40%-200% dieses Werts; ausserhalb -> Stage 1 Fallback.
    # None = proportional zum Input (raw_words * 0.12, min 800)
    STAGE1_TARGET_WORDS: int | None = None

    # ── v19.3 Transkript-Stage-1 (Transkript-Verdichtung) ────────
    # Stage 1 fuer Sitzungs-Transkripte: verdichtet rohe Whisper-Outputs
    # auf eine 3-Sektionen-Synthese, bevor sie in den Hauptcall gehen.
    # Verhindert dass _sample_uniformly in llm.py (greift ab ~5000-6000w
    # weil estimated_input_tokens + max_tokens > MAX_SAFE_CTX=20480) den
    # Inhalt in 10 Fenstern mit Luecken willkuerlich kuerzt.
    #
    # Wirkt aktuell nur fuer Workflows {dokumentation, anamnese}, da
    # akutantrag/verlaengerung/folgeverlaengerung/entlassbericht in
    # Produktion kein Transkript bekommen.
    #
    # TRANSCRIPT_STAGE1_ENABLED:
    #   true  -> Stage 1 wird fuer dokumentation/anamnese aktiv wenn das
    #            Transkript >= TRANSCRIPT_STAGE1_MIN_WORDS Woerter hat.
    #   false -> Stage 1 wird komplett uebersprungen (Pre-v19.3-Verhalten,
    #            _sample_uniformly greift weiter).
    TRANSCRIPT_STAGE1_ENABLED: bool = True

    # Schwelle ab der die Transkript-Verdichtung lohnt. 5000w liegt
    # bewusst UNTER der _sample_uniformly-Schwelle (~6000-7000w), damit
    # die Verdichtung sicher davor greift. Darunter passt das Transkript
    # ohnehin sauber in MAX_SAFE_CTX.
    TRANSCRIPT_STAGE1_MIN_WORDS: int = 5000

    # Zielwortzahl der Transkript-Verdichtung. None = proportional zum
    # Input mit Hard-Cap: min(1500, max(600, raw_words * 0.20)).
    # Beispiel:
    #   raw= 7000w → target=1400w
    #   raw=10000w → target=1500w (Hard-Cap)
    TRANSCRIPT_STAGE1_TARGET_WORDS: int | None = None

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


settings = Settings()
