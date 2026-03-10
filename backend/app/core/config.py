"""
Konfiguration – alle Einstellungen aus Umgebungsvariablen.
Kopiere .env.example zu .env und passe die Werte an.
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── LLM ──────────────────────────────────────────────────────
    # "ollama" = lokales Modell (Produktion)
    # "anthropic" = Claude API (Testphase / Fallback)
    LLM_BACKEND: str = "ollama"

    # Ollama (lokal)
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"

    # Anthropic API (Testphase)
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"

    # Welches Modell wird nach aussen als "aktiv" gemeldet
    @property
    def LLM_MODEL(self) -> str:
        if self.LLM_BACKEND == "ollama":
            return f"ollama/{self.OLLAMA_MODEL}"
        return f"anthropic/{self.ANTHROPIC_MODEL}"

    # ── Whisper / Transkription ───────────────────────────────────
    # "local" = faster-whisper lokal
    # "openai" = OpenAI Whisper API (Testphase)
    WHISPER_BACKEND: str = "local"
    WHISPER_MODEL: str = "medium"          # tiny | base | small | medium | large-v3
    WHISPER_DEVICE: str = "cpu"            # cpu | cuda
    WHISPER_COMPUTE_TYPE: str = "int8"     # int8 | float16 | float32

    # OpenAI API (Testphase)
    OPENAI_API_KEY: str = ""

    # ── Dateien ───────────────────────────────────────────────────
    UPLOAD_DIR: str = "uploads"
    OUTPUT_DIR: str = "outputs"
    MAX_UPLOAD_MB: int = 100

    # Audiodateien nach Transkription loeschen (Datenschutz)
    DELETE_AUDIO_AFTER_TRANSCRIPTION: bool = True

    # ── Datenbank ─────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./systelios.db"

    # ── Sicherheit ────────────────────────────────────────────────
    SECRET_KEY: str = "BITTE-AENDERN-IN-PRODUKTION"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480   # 8 Stunden

    # ── CORS ──────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
    ]

    # ── Logging ───────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "systelios.log"


settings = Settings()
