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
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Separater Performance-Logger – schreibt JSON-Zeilen in eigene Datei
# Pfad: /workspace/performance.log (persistent über Pod-Neustarts)
perf_logger = logging.getLogger("systelios.performance")


def _minimize_input_meta(meta):
    """O2: Entfernt Klartextdaten aus Metadaten, behält nur Booleans/Anzahlen."""
    if not isinstance(meta, dict): return {}
    out = {}
    for key, val in meta.items():
        if isinstance(val, bool):
            out[key] = val
        elif isinstance(val, (list, tuple)):
            out[f"{key}_count"] = len(val)
        elif isinstance(val, str):
            out[f"has_{key}"] = bool(val)
        elif isinstance(val, (int, float)):
            out[key] = val
    return out


def _setup_perf_logger() -> None:
    """Richtet den Performance-Logger mit eigenem Logfile ein."""
    # Persistent auf /workspace falls vorhanden, sonst neben LOG_FILE
    import os
    if os.path.isdir("/workspace"):
        perf_log_path = "/workspace/performance.log"
    else:
        perf_log_path = str(Path(settings.LOG_FILE).parent / "performance.log")
    try:
        handler = logging.FileHandler(str(perf_log_path), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))  # nur die JSON-Zeile
        perf_logger.addHandler(handler)
        perf_logger.setLevel(logging.INFO)
        perf_logger.propagate = False  # nicht ins Haupt-Log schreiben
    except OSError:
        pass


_setup_perf_logger()


def _log_performance(job: "Job", queue_size: int) -> None:
    """Schreibt einen JSON-Eintrag ins Performance-Log."""
    entry = {
        "ts":           job.finished_at.isoformat() if job.finished_at else datetime.now(timezone.utc).isoformat(),
        "job_id":       job.job_id,
        "workflow":     job.workflow,
        "status":       job.status.value,
        "duration_s":   job.duration_s,
        "queue_size":   queue_size,
        "model":        job.model_used,
        "error":        job.error_msg if job.status.value == "error" else None,
        # Input-Metadaten
        "input":        _minimize_input_meta(job.input_meta) if job.input_meta else None,
        # Output-Statistiken
        "output_words": len(job.result_text.split()) if job.result_text else 0,
        "output_chars": len(job.result_text) if job.result_text else 0,
    }
    perf_logger.info(json.dumps(entry, ensure_ascii=False))


class JobStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    DONE       = "done"
    ERROR      = "error"
    CANCELLED  = "cancelled"


class Job:
    def __init__(self, job_id: str, workflow: str, description: str = ""):
        self.job_id             = job_id
        self.workflow           = workflow
        self.description        = description
        self.status             = JobStatus.PENDING
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
        self.style_info         : Optional[dict] = None   # Stil-Metadaten: source, chars, words
        self._cancel_requested  : bool = False  # gesetzt via cancel_job()
        # Performance-Tracking: Input-Metadaten
        self.input_meta         : Optional[dict] = None   # {has_audio, has_pdf, has_style, ...}

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
            "status":          self.status.value,
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
        queue_size = len([j for j in self._jobs.values() if j.status in (JobStatus.PENDING, JobStatus.RUNNING)])
        logger.info("Job erstellt: %s (%s) | Warteschlange: %d", job_id, workflow, queue_size)
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel_job(self, job_id: str) -> bool:
        """
        Markiert einen Job als abzubrechen.
        run_job() prüft das Flag und bricht ab bevor/nach dem LLM-Call.
        Gibt True zurück wenn der Job gefunden und noch nicht abgeschlossen war.
        """
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED):
            return False
        job._cancel_requested = True
        logger.info("Abbruch angefordert: %s (%s)", job_id, job.workflow)
        return True

    async def run_job(
        self,
        job: Job,
        coro: Coroutine,
    ) -> None:
        """Fuehrt einen Job asynchron aus und aktualisiert den Status."""
        job.status     = JobStatus.RUNNING
        job.set_progress(5, "Warteschlange")
        job.started_at = datetime.now(timezone.utc)
        t0 = asyncio.get_event_loop().time()

        try:
            # Vor dem Start: schon abgebrochen?
            if job._cancel_requested:
                job.status = JobStatus.CANCELLED
                job.duration_s = round(asyncio.get_event_loop().time() - t0, 1)
                logger.info("Job abgebrochen (vor Start): %s", job.job_id)
                return

            result = await coro

            # Ergebnis IMMER speichern – auch wenn inzwischen Abbruch angefordert.
            # Der Text wurde bereits generiert (GPU-Zeit verbraucht), also behalten.
            job.result_text        = result.get("text")
            job.result_transcript  = result.get("transcript")
            job.result_befund      = result.get("befund_text")
            job.result_akut        = result.get("akut_text")
            job.result_file        = result.get("file")
            job.model_used         = result.get("model_used")
            job.style_info         = result.get("style_info")
            job.duration_s  = round(asyncio.get_event_loop().time() - t0, 1)

            if job._cancel_requested:
                job.status = JobStatus.CANCELLED
                logger.info("Job abgebrochen (nach Generierung, Text behalten): %s", job.job_id)
            else:
                job.set_progress(100, "Fertig")
                job.status = JobStatus.DONE
                logger.info(
                    "Job abgeschlossen: %s (%s) in %.1fs", job.job_id, job.workflow, job.duration_s
                )
        except Exception as e:
            job.duration_s = round(asyncio.get_event_loop().time() - t0, 1)
            if job._cancel_requested:
                job.status = JobStatus.CANCELLED
                logger.info("Job abgebrochen (Exception während Abbruch): %s", job.job_id)
            else:
                job.status    = JobStatus.ERROR
                job.error_msg = str(e)
                logger.error("Job fehlgeschlagen: %s (%s) in %.1fs – %s", job.job_id, job.workflow, job.duration_s, e)
        finally:
            job.finished_at = datetime.now(timezone.utc)
            queue_size = len([j for j in self._jobs.values() if j.status in (JobStatus.PENDING, JobStatus.RUNNING)])
            _log_performance(job, queue_size)

    def _cleanup_old_jobs(self):
        if len(self._jobs) > self._max_jobs:
            # Aelteste abgeschlossene Jobs entfernen
            done_jobs = sorted(
                [j for j in self._jobs.values() if j.status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED)],
                key=lambda j: j.created_at,
            )
            for job in done_jobs[:len(self._jobs) - self._max_jobs]:
                del self._jobs[job.job_id]


# Globale Instanz
job_queue = JobQueue()
