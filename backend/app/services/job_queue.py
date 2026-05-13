"""
Job-Queue fuer asynchrone Verarbeitung langer Aufgaben.

Alle zeitintensiven Operationen laufen als Hintergrund-Jobs:
  - Audio-Transkription (Whisper, kann Minuten dauern)
  - LLM-Generierung (Ollama, 10-60 Sekunden)
  - Dokument-Verarbeitung (OCR, PDF-Extraktion)

Frontend pollt GET /api/jobs/{job_id} bis status="done" oder "error".

Speicherung: Hybrid – PostgreSQL fuer Persistenz, RAM-Cache fuer Progress.
Progress-Updates kommen vielfach pro Sekunde (Token-Zaehler) und sind zu
teuer fuer DB-Writes. Bei Job-Abschluss wird der finale State persistiert.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Coroutine, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Separater Performance-Logger – schreibt JSON-Zeilen in eigene Datei
perf_logger = logging.getLogger("systelios.performance")


# ── Performance-Logging ─────────────────────────────────────────────────────

def _setup_perf_logger():
    """Richtet den Performance-Logger ein (einmalig beim Import)."""
    if perf_logger.handlers:
        return
    perf_logger.setLevel(logging.INFO)
    perf_logger.propagate = False
    log_dir = Path(getattr(settings, "AUDIT_LOG_PATH", "/workspace/audit.log")).parent
    perf_file = log_dir / "performance.log"
    try:
        handler = logging.FileHandler(str(perf_file), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        perf_logger.addHandler(handler)
    except Exception:
        pass

_setup_perf_logger()


def _log_performance(job: "JobState", queue_size: int) -> None:
    """Loggt Performance-Metriken eines abgeschlossenen Jobs."""
    entry = {
        "ts":           datetime.now(timezone.utc).isoformat(),
        "job_id":       job.job_id,
        "workflow":     job.workflow,
        "status":       job.status,
        "duration_s":   job.duration_s,
        "model_used":   job.model_used,
        "output_words": len(job.result_text.split()) if job.result_text else 0,
        "output_chars": len(job.result_text) if job.result_text else 0,
        "queue_size":   queue_size,
    }
    # v19.1: Think-Block-Telemetrie ins Performance-Log einbetten.
    # Erlaubt Auswertung "wie oft hat der Retry-Layer angeschlagen" und
    # "wie oft kam degraded=true" ohne DB-Abfrage.
    tel = job.generation_telemetry or {}
    if tel:
        entry["telemetry"] = {
            "think_ratio":            tel.get("think_ratio"),
            "tokens_hit_cap":         tel.get("tokens_hit_cap"),
            "used_thinking_fallback": tel.get("used_thinking_fallback"),
            "retry_used":             tel.get("retry_used", False),
            "degraded":               tel.get("degraded", False),
        }
    perf_logger.info(json.dumps(entry, ensure_ascii=False))


# ── Job Status ───────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    DONE       = "done"
    ERROR      = "error"
    CANCELLED  = "cancelled"


# ── In-Memory Job State (transient, fuer schnelle Progress-Updates) ──────────

class JobState:
    """
    Transienter Job-Zustand im RAM.

    Progress-Updates kommen vielfach pro Sekunde (Token-Zaehler) —
    zu teuer fuer DB-Writes. Stattdessen halten wir den State im RAM
    und persistieren bei Abschluss in PostgreSQL.
    """
    def __init__(self, job_id: str, workflow: str, description: str = ""):
        self.job_id             = job_id
        self.workflow           = workflow
        self.description        = description
        self.status             = JobStatus.PENDING.value
        self.result_text        : Optional[str] = None
        self.progress           : int = 0
        self.progress_phase     : str = ""
        self.progress_detail    : str = ""
        self.result_transcript  : Optional[str] = None
        self.result_befund      : Optional[str] = None
        self.result_akut        : Optional[str] = None
        self.result_file        : Optional[str] = None
        self.error_msg          : Optional[str] = None
        self.created_at         = datetime.now(timezone.utc)
        self.started_at         : Optional[datetime] = None
        self.finished_at        : Optional[datetime] = None
        self.model_used         : Optional[str] = None
        self.duration_s         : Optional[float] = None
        self.style_info         : Optional[dict] = None
        # v19.1: Think-Block-Telemetrie aus llm.generate_text().
        # Kann Felder enthalten: think_ratio, tokens_hit_cap, retry_used,
        # degraded, degraded_reason, used_thinking_fallback, eval_count, ...
        self.generation_telemetry: Optional[dict] = None
        self._cancel_requested  : bool = False
        self.input_meta         : Optional[dict] = None
        # Optionales Quality-Check-Result (siehe app/services/quality_check.py).
        # Wird gesetzt wenn der Job mit aktivierter Qualitätsprüfung läuft.
        self.quality_check      : Optional[dict] = None

    def set_progress(self, pct: int, phase: str = "", detail: str = "") -> None:
        """Monotoner Progress (0-100). Thread-safe via atomic int write."""
        self.progress = max(self.progress, min(100, int(pct)))
        if phase:
            self.progress_phase = phase
        self.progress_detail = detail

    def to_dict(self) -> dict:
        return {
            "job_id":          self.job_id,
            "workflow":        self.workflow,
            "description":     self.description,
            "status":          self.status,
            "cancelled":       self._cancel_requested,
            "result_text":     self.result_text or "",
            "has_transcript":  self.result_transcript is not None,
            "progress":        self.progress,
            "progress_phase":  self.progress_phase,
            "progress_detail": self.progress_detail,
            "befund_text":     self.result_befund or "",
            "akut_text":       self.result_akut or "",
            "result_file":     self.result_file,
            "error_msg":       self.error_msg,
            "created_at":      self.created_at.isoformat(),
            "started_at":      self.started_at.isoformat() if self.started_at else None,
            "finished_at":     self.finished_at.isoformat() if self.finished_at else None,
            "model_used":      self.model_used,
            "duration_s":      self.duration_s,
            "style_info":      self.style_info,
            "quality_check":   self.quality_check,
            "generation_telemetry": self.generation_telemetry,
        }


# ── Job Queue (DB-backed + In-Memory Cache) ──────────────────────────────────

class JobQueue:
    """
    Hybride Job-Queue: DB fuer Persistenz, RAM fuer schnelle Progress-Updates.

    - create_job():  INSERT in DB + lokaler Cache
    - get_job():     lokaler Cache (schnell) oder DB-Fallback (multi-worker)
    - run_job():     async Coroutine, Progress in RAM, Ergebnis in DB
    - cancel_job():  Flag in RAM + UPDATE in DB
    """

    def __init__(self):
        self._cache: dict[str, JobState] = {}
        self._max_cache = 500

    def create_job(self, workflow: str, description: str = "") -> JobState:
        """Erstellt einen neuen Job im Cache und persistiert ihn asynchron in der DB."""
        job_id = uuid.uuid4().hex
        state = JobState(job_id, workflow, description)
        self._cache[job_id] = state
        self._cleanup_cache()

        queue_size = len([j for j in self._cache.values()
                          if j.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value)])
        logger.info("Job erstellt: %s (%s) | Warteschlange: %d", job_id, workflow, queue_size)

        # DB-Insert asynchron (fire-and-forget) – gleicher Ansatz wie cancel_job
        asyncio.ensure_future(self._db_insert_job(job_id, workflow, description))

        return state

    async def _db_insert_job(self, job_id: str, workflow: str, description: str) -> None:
        """Persistiert einen neuen Job in der DB (wird als Task gestartet)."""
        try:
            from app.core.database import async_session_factory
            from app.models.db import Job as JobModel
            async with async_session_factory() as db:
                db_job = JobModel(
                    id=job_id,
                    workflow=workflow,
                    description=description,
                    status="pending",
                )
                db.add(db_job)
                await db.commit()
        except Exception as e:
            logger.warning("Job-DB-Insert fehlgeschlagen (laeuft in-memory weiter): %s", e)

    def get_job(self, job_id: str) -> Optional[JobState]:
        """Holt Job aus dem Cache (schnell, fuer Polling)."""
        return self._cache.get(job_id)

    async def get_job_from_db(self, job_id: str) -> Optional[dict]:
        """Fallback: Job aus der DB laden (fuer multi-worker oder nach Restart)."""
        try:
            from app.core.database import async_session_factory
            from app.models.db import Job as JobModel
            from sqlalchemy import select
            async with async_session_factory() as db:
                result = await db.execute(select(JobModel).where(JobModel.id == job_id))
                db_job = result.scalar_one_or_none()
                if not db_job:
                    return None
                return {
                    "job_id":          db_job.id,
                    "workflow":        db_job.workflow,
                    "description":     db_job.description or "",
                    "status":          db_job.status,
                    "cancelled":       db_job.cancel_requested or False,
                    "result_text":     db_job.result_text or "",
                    "has_transcript":  db_job.result_transcript is not None,
                    "progress":        db_job.progress or 0,
                    "progress_phase":  db_job.progress_phase or "",
                    "progress_detail": db_job.progress_detail or "",
                    "befund_text":     db_job.result_befund or "",
                    "akut_text":       db_job.result_akut or "",
                    "result_file":     db_job.result_file,
                    "error_msg":       db_job.error_msg,
                    "created_at":      db_job.created_at.isoformat() if db_job.created_at else None,
                    "started_at":      db_job.started_at.isoformat() if db_job.started_at else None,
                    "finished_at":     db_job.finished_at.isoformat() if db_job.finished_at else None,
                    "model_used":      db_job.model_used,
                    "duration_s":      db_job.duration_s,
                    "style_info":      json.loads(db_job.style_info_json) if db_job.style_info_json else None,
                    "generation_telemetry": db_job.generation_telemetry,
                }
        except Exception as e:
            logger.warning("Job-DB-Lookup fehlgeschlagen: %s", e)
            return None

    def get_all_jobs(self) -> list[JobState]:
        return sorted(self._cache.values(), key=lambda j: j.created_at, reverse=True)

    def cancel_job(self, job_id: str) -> bool:
        """Markiert einen Job als abzubrechen (Cache + DB)."""
        state = self._cache.get(job_id)
        if not state:
            return False
        if state.status in (JobStatus.DONE.value, JobStatus.ERROR.value, JobStatus.CANCELLED.value):
            return False
        state._cancel_requested = True
        logger.info("Abbruch angefordert: %s (%s)", job_id, state.workflow)
        # Async DB-Update
        asyncio.ensure_future(self._db_set_cancel(job_id))
        return True

    async def _db_set_cancel(self, job_id: str):
        try:
            from app.core.database import async_session_factory
            from app.models.db import Job as JobModel
            from sqlalchemy import update
            async with async_session_factory() as db:
                await db.execute(
                    update(JobModel).where(JobModel.id == job_id)
                    .values(cancel_requested=True)
                )
                await db.commit()
        except Exception as e:
            logger.warning("Cancel-DB-Update fehlgeschlagen: %s", e)

    async def _persist_job(self, state: JobState):
        """Persistiert den finalen Job-Zustand in der DB."""
        try:
            from app.core.database import async_session_factory
            from app.models.db import Job as JobModel
            from sqlalchemy import update
            async with async_session_factory() as db:
                await db.execute(
                    update(JobModel).where(JobModel.id == state.job_id).values(
                        status=state.status,
                        cancel_requested=state._cancel_requested,
                        progress=state.progress,
                        progress_phase=state.progress_phase,
                        progress_detail=state.progress_detail,
                        result_text=state.result_text,
                        result_transcript=state.result_transcript,
                        result_befund=state.result_befund,
                        result_akut=state.result_akut,
                        result_file=state.result_file,
                        error_msg=state.error_msg,
                        started_at=state.started_at,
                        finished_at=state.finished_at,
                        model_used=state.model_used,
                        duration_s=state.duration_s,
                        style_info_json=json.dumps(state.style_info) if state.style_info else None,
<<<<<<< HEAD
                        quality_check_json=json.dumps(state.quality_check) if state.quality_check else None,
=======
                        generation_telemetry=state.generation_telemetry,
>>>>>>> main
                    )
                )
                await db.commit()
        except Exception as e:
            logger.warning("Job-DB-Persist fehlgeschlagen: %s", e)

    async def run_job(
        self,
        job: JobState,
        coro: Coroutine,
    ) -> None:
        """Fuehrt einen Job asynchron aus und aktualisiert den Status."""
        job.status     = JobStatus.RUNNING.value
        job.set_progress(5, "Warteschlange")
        job.started_at = datetime.now(timezone.utc)
        t0 = asyncio.get_event_loop().time()

        try:
            if job._cancel_requested:
                job.status = JobStatus.CANCELLED.value
                job.duration_s = round(asyncio.get_event_loop().time() - t0, 1)
                logger.info("Job abgebrochen (vor Start): %s", job.job_id)
                return

            result = await coro

            job.result_text        = result.get("text")
            job.result_transcript  = result.get("transcript")
            job.result_befund      = result.get("befund_text")
            job.result_akut        = result.get("akut_text")
            job.result_file        = result.get("file")
            job.model_used         = result.get("model_used")
            job.style_info         = result.get("style_info")
<<<<<<< HEAD
            job.quality_check      = result.get("quality_check")
=======
            # v19.1: Telemetrie aus dem LLM-Result (Pipeline jobs.py
            # haengt sie an result["generation_telemetry"] an).
            job.generation_telemetry = result.get("generation_telemetry")
>>>>>>> main
            job.duration_s  = round(asyncio.get_event_loop().time() - t0, 1)

            if job._cancel_requested:
                job.status = JobStatus.CANCELLED.value
                logger.info("Job abgebrochen (nach Generierung, Text behalten): %s", job.job_id)
            else:
                job.set_progress(100, "Fertig")
                job.status = JobStatus.DONE.value
                logger.info(
                    "Job abgeschlossen: %s (%s) in %.1fs", job.job_id, job.workflow, job.duration_s
                )
        except Exception as e:
            job.duration_s = round(asyncio.get_event_loop().time() - t0, 1)
            if job._cancel_requested or "__CANCELLED__" in str(e):
                job.status = JobStatus.CANCELLED.value
                logger.info("Job abgebrochen: %s (%s) in %.1fs", job.job_id, job.workflow, job.duration_s)
            else:
                job.status    = JobStatus.ERROR.value
                job.error_msg = str(e)
                logger.error("Job fehlgeschlagen: %s (%s) in %.1fs – %s", job.job_id, job.workflow, job.duration_s, e)
        finally:
            job.finished_at = datetime.now(timezone.utc)
            queue_size = len([j for j in self._cache.values()
                              if j.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value)])
            _log_performance(job, queue_size)
            await self._persist_job(job)

    def _cleanup_cache(self):
        if len(self._cache) > self._max_cache:
            done_jobs = sorted(
                [j for j in self._cache.values()
                 if j.status in (JobStatus.DONE.value, JobStatus.ERROR.value, JobStatus.CANCELLED.value)],
                key=lambda j: j.created_at,
            )
            for job in done_jobs[:len(self._cache) - self._max_cache]:
                del self._cache[job.job_id]


# Globale Instanz
job_queue = JobQueue()
