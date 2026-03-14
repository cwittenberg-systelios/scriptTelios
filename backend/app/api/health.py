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
    """Gibt Systemstatus und Ollama-Erreichbarkeit zurueck."""

    ollama_status = "ollama:checking"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.OLLAMA_HOST}/api/tags")
        ollama_status = "ollama:ok" if r.status_code == 200 else "ollama:error"
    except Exception:
        ollama_status = "ollama:unreachable"

    return HealthResponse(
        status="ok",
        llm_backend=ollama_status,
        llm_model=settings.LLM_MODEL,
        whisper_backend="local",
        whisper_model=settings.WHISPER_MODEL,
    )
