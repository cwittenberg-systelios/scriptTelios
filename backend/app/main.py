"""
sysTelios KI-Dokumentation – FastAPI Backend
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import transcribe, generate, documents, health
from app.api import style_embeddings, jobs
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
    # Upload-Bereinigung im Hintergrund starten
    cleanup_task = asyncio.create_task(_cleanup_old_uploads())
    yield
    cleanup_task.cancel()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router,            prefix="/api", tags=["Health"])
app.include_router(transcribe.router,        prefix="/api", tags=["Transkription"])
app.include_router(generate.router,          prefix="/api", tags=["Generierung"])
app.include_router(documents.router,         prefix="/api", tags=["Dokumente"])
app.include_router(style_embeddings.router,  prefix="/api", tags=["Stilprofil"])
app.include_router(jobs.router,              prefix="/api", tags=["Jobs"])

# Frontend-Bundle ausliefern (gebaut mit: cd frontend && npm run build)
# Erreichbar unter: http://systelios-backend:8000/static/systelios.js
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
