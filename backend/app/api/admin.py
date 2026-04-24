"""
POST /api/admin/whisper-model - Whisper-Modell zur Laufzeit wechseln.

Nuetzlich fuer Eval-Tests: schnelleres Modell fuer automatisierte Testlaeufe,
ohne den Server neu starten zu muessen.

Der Endpoint ist mit dem CONFLUENCE_SHARED_SECRET geschuetzt um unautorisierte
Aenderungen zu verhindern. Bei deaktivierter Auth (AUTH_ENABLED=false) ist
er auch ohne Token erreichbar (Dev-Modus).
"""
import logging

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Gueltige Whisper-Modelle (gemaess faster-whisper)
VALID_MODELS = {
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large-v1", "large-v2", "large-v3",
    "distil-small.en", "distil-medium.en", "distil-large-v3",
}


@router.post("/admin/whisper-model")
async def set_whisper_model(
    model: str,
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
):
    """
    Setzt das Whisper-Modell zur Laufzeit. Der Modell-Cache wird geleert
    damit beim naechsten Transkriptions-Aufruf das neue Modell geladen wird.

    Request-Body: ?model=medium (Query-Parameter)
    Header:       X-Admin-Token: <CONFLUENCE_SHARED_SECRET>

    Antwort:
        { "ok": true, "whisper_model": "medium", "previous": "large-v3" }
    """
    # Auth: wenn AUTH_ENABLED, dann nur mit korrektem Token zulassen
    if settings.AUTH_ENABLED:
        if not x_admin_token or x_admin_token != settings.CONFLUENCE_SHARED_SECRET:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-Admin-Token fehlt oder ist ungueltig",
            )

    if model not in VALID_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unbekanntes Whisper-Modell '{model}'. "
                   f"Erlaubt: {sorted(VALID_MODELS)}",
        )

    previous = settings.WHISPER_MODEL

    # Modell wechseln + Cache leeren damit das neue Modell geladen wird
    settings.WHISPER_MODEL = model
    try:
        from app.services.transcription import _model_cache, _diarization_pipeline
        _model_cache.clear()
        # Diarization-Pipeline bleibt erhalten (von Modell unabhaengig)
    except Exception as e:
        logger.warning("Model-Cache-Clear fehlgeschlagen (ignoriert): %s", e)

    logger.warning(
        "Whisper-Modell zur Laufzeit gewechselt: %s → %s",
        previous, model,
    )

    return {
        "ok": True,
        "whisper_model": model,
        "previous": previous,
        "cache_cleared": True,
    }


@router.get("/admin/whisper-model")
async def get_whisper_model():
    """Gibt das aktuell konfigurierte Whisper-Modell zurueck."""
    return {
        "whisper_model": settings.WHISPER_MODEL,
        "whisper_device": settings.WHISPER_DEVICE,
        "whisper_compute_type": settings.WHISPER_COMPUTE_TYPE,
    }
