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
from app.middleware.activity import ActivityMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import health, style_embeddings, jobs, admin, testrun, recordings, workflow_manifest, selfcheck, feedback, activity, ism
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


ORPHAN_MSG = ("Verwaist: Der Pod wurde gestoppt, waehrend der Job in der "
              "Warteschlange stand oder lief. Bitte erneut starten.")

# Altersgrenze fuer die Verwaisten-Bereinigung. NICHT blosse Vorsicht:
# tests/eval/run_model_eval.sh startet `app.main:app` auf Port 8001 gegen
# DIESELBE Datenbank. Ohne Grenze wuerde jeder Eval-Lauf die gerade laufenden
# Jobs der Produktivinstanz als "error" markieren. 30 Minuten liegen
# komfortabel ueber der harten Obergrenze eines Jobs (httpx-Timeout 600 s in
# services/llm.py) — ein echter Job ist nie so alt und noch aktiv.
ORPHAN_MIN_AGE = "30 minutes"


async def _close_orphans() -> None:
    """Jobs und Recordings abschliessen, die einen Pod-Stopp erlebt haben.

    Die In-Memory-Queue in job_queue.py ist der einzige Executor. Was beim
    Start noch auf `pending`/`running` steht, nimmt niemand mehr auf — ohne
    diesen Hook bleibt es fuer immer stehen (real vorgefunden: drei Zeilen,
    38 Tage alt) und suggeriert im UI einen laufenden Auftrag, auf dessen
    Ergebnis jemand wartet.

    Fehler werden nur geloggt: eine misslungene Bereinigung darf den Start
    des Backends nicht verhindern.
    """
    try:
        from sqlalchemy import text as _sql
        from app.core.database import async_session_factory
        async with async_session_factory() as db:
            jobs = await db.execute(_sql(
                "UPDATE jobs "
                "   SET status = 'error', error_msg = :msg, finished_at = now() "
                " WHERE status IN ('pending','running') "
                f"  AND updated_at < now() - interval '{ORPHAN_MIN_AGE}'"
            ), {"msg": ORPHAN_MSG})
            recs = await db.execute(_sql(
                "UPDATE recordings "
                "   SET status = 'error' "
                " WHERE status IN ('uploading','transcribing') "
                "   AND deleted_at IS NULL "
                f"  AND created_at < now() - interval '{ORPHAN_MIN_AGE}'"
            ))
            await db.commit()
            if jobs.rowcount or recs.rowcount:
                logger.warning(
                    "Verwaiste Eintraege beim Start abgeschlossen: %d Job(s), %d Recording(s)",
                    jobs.rowcount or 0, recs.rowcount or 0,
                )
    except Exception as e:
        logger.warning("Bereinigung verwaister Eintraege fehlgeschlagen: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("sysTelios Backend startet (Modell: %s)", settings.LLM_MODEL)
    # Sprint F1: Feedback-Push-Konfiguration beim Start pruefen und loggen -
    # sonst faellt eine fehlende TELEGRAM_*-Variable erst dann auf, wenn ein
    # Feedback abgegeben wurde und die Nachricht ausbleibt.
    from app.services.feedback_notify import log_effective_config as _fb_cfg
    _fb_cfg()
    await init_db()
    # v19.10b: Verwaiste Jobs/Recordings aus einem frueheren Pod-Lauf abschliessen.
    await _close_orphans()
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
        # Referenz halten (RUF006): referenzlose Tasks darf der GC einsammeln.
        emb_check_task = asyncio.create_task(check_embedding_model_available())
        app.state.emb_check_task = emb_check_task
    except Exception:
        pass
    # v19.5.2: Verdichtungsmodell (SUMMARY_MODEL) beim Start prüfen → laute
    # Warnung wenn nicht geladen (Stage-1/Budget-Guard faellt sonst zur Laufzeit
    # auf ein Ersatzmodell zurueck; verhindert stillen Ollama-404).
    try:
        from app.services.llm import check_summary_model_available
        sum_check_task = asyncio.create_task(check_summary_model_available())
        app.state.sum_check_task = sum_check_task
    except Exception:
        pass
    # v19.7 S1: Ollama-Version beim Start pruefen → laute Warnung wenn < 0.5
    # (format=JSON-Schema wird sonst still ignoriert; der Structured-Befund-
    # Pfad faellt dann bei jedem Job auf den Freitext-Fallback zurueck).
    try:
        from app.services.llm import check_structured_output_support
        so_check_task = asyncio.create_task(check_structured_output_support())
        app.state.so_check_task = so_check_task
    except Exception:
        pass
    # v19.7: Whisper-Modell beim Start pruefen → laute Warnung wenn nicht im
    # lokalen HF-Cache (erster Transkriptionsjob haengt sonst an der
    # HF-Hub-Verfuegbarkeit; Live-Fund: HfHubHTTPError 504).
    try:
        from app.services.transcription import check_whisper_model_available
        whisper_check_task = asyncio.create_task(check_whisper_model_available())
        app.state.whisper_check_task = whisper_check_task
    except Exception:
        pass
    yield
    cleanup_task.cancel()
    try: p0_worker_task.cancel()
    except Exception: pass
    try: retention_task_handle.cancel()
    except Exception: pass
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

# v19.10: Idle-Tracking fuer den Auto-Stopp im Cloudflare-Worker.
# Muss VOR der CORS-Middleware stehen, damit auch abgelehnte Requests zaehlen.
app.add_middleware(ActivityMiddleware)
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
app.include_router(selfcheck.router,         prefix="/api", tags=["Health"])
app.include_router(style_embeddings.router,  prefix="/api", tags=["Stilprofil"])
app.include_router(jobs.router,              prefix="/api", tags=["Jobs"])
app.include_router(admin.router,             prefix="/api", tags=["Admin"])
app.include_router(testrun.router,           prefix="/api", tags=["Tests"])
app.include_router(recordings.router,        prefix="/api", tags=["Aufnahmen"])
app.include_router(feedback.router,          prefix="/api", tags=["Feedback"])
app.include_router(activity.router,           prefix="/api", tags=["Health"])
app.include_router(ism.router,               prefix="/api", tags=["ISM"])
app.include_router(workflow_manifest.router)

@app.options("/{full_path:path}")
async def options_catchall(full_path: str):
    return Response(status_code=204)


# Frontend-Bundle ausliefern (gebaut mit: cd frontend && npm run build)
# Erreichbar unter: http://systelios-backend:8000/static/systelios.js
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
