"""
Job-Queue fuer asynchrone Verarbeitung langer Aufgaben.

Alle zeitintensiven Operationen laufen als Hintergrund-Jobs:
  - Audio-Transkription (Whisper, kann Minuten dauern)
  - LLM-Generierung (Ollama, 10-60 Sekunden)
  - Dokument-Verarbeitung (OCR, PDF-Extraktion)

Frontend pollt GET /api/jobs/{job_id} bis status="done" oder "error".

Speicherung: In-Memory (reicht fuer Testphase).
Produktion: PostgreSQL-Tabelle jobs (Modell bereits in db.py vorhanden).
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    ERROR    = "error"


class Job:
    def __init__(self, job_id: str, workflow: str, description: str = ""):
        self.job_id       = job_id
        self.workflow     = workflow
        self.description  = description
        self.status       = JobStatus.PENDING
        self.result_text  : Optional[str] = None
        self.result_file  : Optional[str] = None
        self.error_msg    : Optional[str] = None
        self.created_at   = datetime.now(timezone.utc)
        self.started_at   : Optional[datetime] = None
        self.finished_at  : Optional[datetime] = None
        self.model_used   : Optional[str] = None
        self.duration_s   : Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "job_id":       self.job_id,
            "workflow":     self.workflow,
            "description":  self.description,
            "status":       self.status.value,
            "result_text":  self.result_text,
            "result_file":  self.result_file,
            "error_msg":    self.error_msg,
            "created_at":   self.created_at.isoformat(),
            "started_at":   self.started_at.isoformat() if self.started_at else None,
            "finished_at":  self.finished_at.isoformat() if self.finished_at else None,
            "model_used":   self.model_used,
            "duration_s":   self.duration_s,
        }


class JobQueue:
    """Einfache In-Memory Job-Queue fuer asynchrone Verarbeitung."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._max_jobs = 500  # Aelteste Jobs werden entfernt

    def create_job(self, workflow: str, description: str = "") -> Job:
        job_id = uuid.uuid4().hex
        job = Job(job_id, workflow, description)
        self._jobs[job_id] = job
        self._cleanup_old_jobs()
        logger.info("Job erstellt: %s (%s)", job_id, workflow)
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    async def run_job(
        self,
        job: Job,
        coro: Coroutine,
    ) -> None:
        """Fuehrt einen Job asynchron aus und aktualisiert den Status."""
        job.status     = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        t0 = asyncio.get_event_loop().time()

        try:
            result = await coro
            job.status      = JobStatus.DONE
            job.result_text = result.get("text")
            job.result_file = result.get("file")
            job.model_used  = result.get("model_used")
            job.duration_s  = round(asyncio.get_event_loop().time() - t0, 1)
            logger.info(
                "Job abgeschlossen: %s in %.1fs", job.job_id, job.duration_s
            )
        except Exception as e:
            job.status    = JobStatus.ERROR
            job.error_msg = str(e)
            job.duration_s = round(asyncio.get_event_loop().time() - t0, 1)
            logger.error("Job fehlgeschlagen: %s – %s", job.job_id, e)
        finally:
            job.finished_at = datetime.now(timezone.utc)

    def _cleanup_old_jobs(self):
        if len(self._jobs) > self._max_jobs:
            # Aelteste abgeschlossene Jobs entfernen
            done_jobs = sorted(
                [j for j in self._jobs.values() if j.status in (JobStatus.DONE, JobStatus.ERROR)],
                key=lambda j: j.created_at,
            )
            for job in done_jobs[:len(self._jobs) - self._max_jobs]:
                del self._jobs[job.job_id]


# Globale Instanz
job_queue = JobQueue()
