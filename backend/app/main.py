"""
sysTelios KI-Dokumentation – FastAPI Backend
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from app.middleware.audit import AuditMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import health, style_embeddings, jobs, admin, testrun, recordings, workflow_manifest
from app.core.config import settings
from app.core.database import init_db
from app.core.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


async def _cleanup_old_uploads():
    """
    Loescht Upload-Dateien aelter als 24 Stunden.
    Laeuft als Hintergrund-Task alle 60 Minuten.
    Audio-Dateien werden separat per DELETE_AUDIO_AFTER_TRANSCRIPTION behandelt,
    aber PDFs/DOCXs bleiben sonst fuer immer liegen.
    """
    import time
    MAX_AGE_HOURS = 24

    while True:
        try:
            upload_path = Path(settings.UPLOAD_DIR)
            if upload_path.exists():
                cutoff = time.time() - (MAX_AGE_HOURS * 3600)
                count = 0
                for f in upload_path.iterdir():
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                        count += 1
                if count > 0:
                    logger.info("Upload-Bereinigung: %d Dateien aelter als %dh geloescht", count, MAX_AGE_HOURS)
        except Exception as e:
            logger.debug("Upload-Bereinigung fehlgeschlagen: %s", e)
        await asyncio.sleep(3600)  # alle 60 Minuten


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("sysTelios Backend startet (Modell: %s)", settings.LLM_MODEL)
    await init_db()
    # Recordings-Verzeichnis sicherstellen (P0-Aufnahmen)
    from app.core.files import recordings_dir
    recordings_dir()
    # P0-Transkriptions-Queue (niedrige Priorität, läuft nur wenn kein aktiver Job)
    from app.api.recordings import p0_queue, p0_worker
    p0_worker_task = asyncio.create_task(p0_worker())
    app.state.p0_queue = p0_queue
    # Upload-Bereinigung im Hintergrund starten
    cleanup_task = asyncio.create_task(_cleanup_old_uploads())
    from app.services.retention import retention_task
    retention_task_handle = asyncio.create_task(retention_task())
    # v18: Embedding-Modell beim Start prüfen → klare Warnung wenn nicht geladen
    try:
        from app.services.embeddings import check_embedding_model_available
        asyncio.create_task(check_embedding_model_available())
    except Exception:
        pass
    yield
    cleanup_task.cancel()
    try: p0_worker_task.cancel()
    except: pass
    try: retention_task_handle.cancel()
    except: pass
    # Persistenten Ollama-Client schliessen
    from app.services.llm import _ollama_client
    if _ollama_client and not _ollama_client.is_closed:
        await _ollama_client.aclose()
    logger.info("sysTelios Backend beendet")


app = FastAPI(
    title="sysTelios KI-Dokumentation",
    description="Backend fuer KI-gestuetzte klinische Dokumentation",
    version="1.0.0",
    lifespan=lifespan,
)

from app.middleware.ratelimit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

app.add_middleware(AuditMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()] or settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization", "Content-Type",
        "X-Systelios-User", "X-Systelios-Timestamp", "X-Systelios-Signature",
        "X-Admin-Token",               # Admin-Endpoints (z.B. Whisper-Modell-Switch)
        "X-Atlassian-Mau-Ignore",      # Confluence-Tracking
    ],)

app.include_router(health.router,            prefix="/api", tags=["Health"])
app.include_router(style_embeddings.router,  prefix="/api", tags=["Stilprofil"])
app.include_router(jobs.router,              prefix="/api", tags=["Jobs"])
app.include_router(admin.router,             prefix="/api", tags=["Admin"])
app.include_router(testrun.router,           prefix="/api", tags=["Tests"])
app.include_router(recordings.router,        prefix="/api", tags=["Aufnahmen"])
app.include_router(workflow_manifest.router)

@app.options("/{full_path:path}")
async def options_catchall(full_path: str):
    return Response(status_code=204)


# Frontend-Bundle ausliefern (gebaut mit: cd frontend && npm run build)
# Erreichbar unter: http://systelios-backend:8000/static/systelios.js
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
