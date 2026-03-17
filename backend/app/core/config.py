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
    OLLAMA_MODEL: str = "llama3.2"

    @property
    def LLM_MODEL(self) -> str:
        return f"ollama/{self.OLLAMA_MODEL}"

    # ── Whisper / Transkription (lokal) ───────────────────────────
    WHISPER_MODEL:        str = "medium"   # tiny | base | small | medium | large-v3
    WHISPER_DEVICE:       str = "cpu"      # cpu | cuda
    WHISPER_COMPUTE_TYPE: str = "int8"     # int8 | float16 | float32

    # ── Dateien ───────────────────────────────────────────────────
    UPLOAD_DIR:    str = "uploads"
    OUTPUT_DIR:    str = "outputs"
    MAX_UPLOAD_MB: int = 100

    # Audiodateien nach Transkription loeschen (Datenschutz)
    DELETE_AUDIO_AFTER_TRANSCRIPTION: bool = True

    # ── Datenbank ─────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://systelios:systelios@db:5432/systelios"

    # ── Sicherheit ────────────────────────────────────────────────
    SECRET_KEY:                    str = "BITTE-AENDERN-IN-PRODUKTION"
    ACCESS_TOKEN_EXPIRE_MINUTES:   int = 480   # 8 Stunden

    # ── CORS ──────────────────────────────────────────────────────
    # Confluence-Instanz eintragen (internes Netz):
    # z.B. "http://confluence.intern:8090" oder "https://wiki.systelios.de"
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
        daher wird die gesamte proxy.runpod.net-Domain erlaubt.
        Nur aktiv wenn ALLOW_RUNPOD_PROXY=true gesetzt ist.
        """
        if self.ALLOW_RUNPOD_PROXY:
            return r"https://.*\.proxy\.runpod\.net"
        return ""

    # RunPod-Proxy-Domain erlauben (nur fuer Testphase – in Produktion deaktivieren)
    ALLOW_RUNPOD_PROXY: bool = False

    # ── Logging ───────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FILE:  str = "systelios.log"


settings = Settings()
