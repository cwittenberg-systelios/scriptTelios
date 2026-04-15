"""
GET /api/health  – Systemstatus und Konfigurationsuebersicht.

Ollama-Status wird gecacht (max 10s alt), damit der Health-Endpoint
unter Last nicht vom Ollama-Backend ausgebremst wird.
"""
import logging
import time

import httpx
from fastapi import APIRouter

from app.core.config import settings
from app.models.schemas import HealthResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# Cache: (timestamp, status_string)
_OLLAMA_CACHE: dict = {"ts": 0.0, "status": "ollama:checking"}
_CACHE_TTL = 10.0  # Sekunden


async def _check_ollama() -> str:
    """Frischer Check gegen Ollama (max 2s)."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{settings.OLLAMA_HOST}/api/tags")
        return "ollama:ok" if r.status_code == 200 else "ollama:error"
    except Exception:
        return "ollama:unreachable"


@router.get("/health", response_model=HealthResponse)
async def health():
    """Gibt Systemstatus und Ollama-Erreichbarkeit zurueck.

    Ollama-Check ist gecacht (10s TTL), damit haeufiges Frontend-Polling
    den Endpoint nicht zum Bottleneck macht.
    """
    now = time.monotonic()
    if now - _OLLAMA_CACHE["ts"] > _CACHE_TTL:
        _OLLAMA_CACHE["status"] = await _check_ollama()
        _OLLAMA_CACHE["ts"] = now

    return HealthResponse(
        status="ok",
        llm_backend=_OLLAMA_CACHE["status"],
        llm_model=settings.LLM_MODEL,
        whisper_backend="local",
        whisper_model=settings.WHISPER_MODEL,
    )
