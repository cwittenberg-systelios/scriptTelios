"""
GET /api/health  – Systemstatus und Konfigurationsuebersicht.
"""
import logging

import httpx
from fastapi import APIRouter

from app.core.config import settings
from app.models.schemas import HealthResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
async def health():
    """Gibt Systemstatus und aktive Backend-Konfiguration zurueck."""

    # Ollama-Erreichbarkeit pruefen (nur wenn ollama-Backend aktiv)
    llm_status = settings.LLM_BACKEND
    if settings.LLM_BACKEND == "ollama":
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{settings.OLLAMA_HOST}/api/tags")
            llm_status = "ollama:ok" if r.status_code == 200 else "ollama:error"
        except Exception:
            llm_status = "ollama:unreachable"

    return HealthResponse(
        status="ok",
        llm_backend=llm_status,
        llm_model=settings.LLM_MODEL,
        whisper_backend=settings.WHISPER_BACKEND,
        whisper_model=settings.WHISPER_MODEL,
    )
