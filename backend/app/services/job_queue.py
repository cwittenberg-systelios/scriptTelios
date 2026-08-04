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
    # v19.2: Stage-1-Kompaktbericht im Performance-Log (kein voller Audit-Dump,
    # nur die Kennzahlen die fuer Trend-Auswertung relevant sind).
    audit = job.verlauf_summary_audit or {}
    if audit:
        entry["stage1"] = {
            "applied":            audit.get("applied"),
            "raw_words":          audit.get("raw_word_count"),
            "summary_words":      audit.get("summary_word_count"),
            "compression_ratio":  audit.get("compression_ratio"),
            "duration_s":         audit.get("duration_s"),
            "retry_used":         audit.get("retry_used", False),
            "degraded":           audit.get("degraded", False),
            "issue_count":        len(audit.get("issues") or []),
            "fallback_reason":    audit.get("fallback_reason"),
        }
    # v19 Phase 1: QualityCheck-Summary (klein gehalten - kein voller Issue-Dump).
    # Erlaubt spaetere Auswertung "wie oft hat LENGTH_TOO_SHORT getriggert".
    qc = job.quality_check or {}
    if qc:
        entry["quality_check"] = {
            "version": qc.get("version"),
            "summary": qc.get("summary"),
            "codes":   [i.get("code") for i in (qc.get("issues") or [])],
        }
    # v19 Phase C: Repair-Beziehung. Nur die ID, nicht der ganze Prompt -
    # der steht auf repair_input_json in der DB und im audit.log.
    if job.parent_job_id:
        entry["repair"] = {
            "parent_job_id": job.parent_job_id,
            "accepted_codes": (job.repair_input or {}).get("accepted_issue_codes", []),
            "had_user_hint": bool((job.repair_input or {}).get("user_hint")),
            "custom_prompt": bool(
                (job.repair_input or {}).get("custom_final_prompt_used")
            ),
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
    def __init__(
        self,
        job_id: str,
        workflow: str,
        description: str = "",
        therapeut_id: Optional[str] = None,
        patient_kuerzel: Optional[str] = None,
    ):
        self.job_id             = job_id
        self.workflow           = workflow
        self.description        = description
        # Sprint B: Therapeut-Owner und Patientenkennung als eigene Felder
        # (vorher nur in description bzw. nirgends). Werden vom API-Layer
        # gesetzt (jobs.py::create_generate_job) und ueber list_filtered()
        # fuer die Job-Liste pro Therapeut gefiltert.
        self.therapeut_id       : Optional[str] = therapeut_id
        self.patient_kuerzel    : Optional[str] = patient_kuerzel
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
        # v19.2: Stage-1-Ergebnis (Two-Stage-Pipeline).
        # verlauf_summary_text:  Verdichteter Verlauf (None wenn Stage 1
        #                        nicht lief oder fehlgeschlagen ist).
        # verlauf_summary_audit: Audit-Bundle mit applied/raw_word_count/
        #                        compression_ratio/retry_used/degraded/...
        self.verlauf_summary_text : Optional[str]  = None
        self.verlauf_summary_audit: Optional[dict] = None
        # v19.3: Repair-Kontext-Persistierung.
        # source_verlauf_text:        Roh-Verlaufsdoku nach clean_verlauf_text.
        # transcript_summary_text:    Verdichtetes Transkript nach Stage-1.
        # source_antragsvorlage_text: Antragsvorlage nach extract_text (wichtig
        #                             fuer Akutantrag/Verlaengerung/Entlassbericht).
        # source_vorantrag_text:      Vorantrag bei Folgeverlaengerung.
        # (Roh-Transkript steckt in self.result_transcript - schon vorhanden.)
        self.source_verlauf_text         : Optional[str] = None
        self.transcript_summary_text     : Optional[str] = None
        self.source_antragsvorlage_text  : Optional[str] = None
        self.source_vorantrag_text       : Optional[str] = None
        # v19.13: Prozessreflexion (P4, Abschlussreflexion des Klienten).
        self.source_prozessreflexion_text: Optional[str] = None
        # v19 Phase 1: QualityCheck-Ergebnis.
        # Wird nach DONE in run_job() ueber app.services.quality_check
        # berechnet und persistiert. Format: serialize_issues()-Output.
        # None = noch nicht berechnet (Job noch nicht fertig oder
        # ohne result_text). Repair-Jobs bekommen ebenfalls einen Check.
        self.quality_check         : Optional[dict] = None
        # v19 Phase C: Repair-Beziehung.
        # parent_job_id:   ID des Original-Jobs (None fuer normale Jobs).
        # repair_input:    Snapshot der Therapeut-Eingaben fuer Audit
        #                  {accepted_issue_codes, user_hint, final_prompt,
        #                   custom_final_prompt_used}
        self.parent_job_id : Optional[str]  = None
        self.repair_input  : Optional[dict] = None
        self._cancel_requested  : bool = False
        self.input_meta         : Optional[dict] = None

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
            "therapeut_id":    self.therapeut_id,
            "patient_kuerzel": self.patient_kuerzel,
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
            "generation_telemetry": self.generation_telemetry,
            # v19.2: Two-Stage-Pipeline – beide Felder default None bei
            # Jobs die Stage 1 nicht beruehrt haben.
            "verlauf_summary_text":  self.verlauf_summary_text,
            "verlauf_summary_audit": self.verlauf_summary_audit,
            # v19.3: Repair-Kontext-Felder. Default None bei Jobs ohne
            # passende Quellen.
            "source_verlauf_text":         self.source_verlauf_text,
            "transcript_summary_text":     self.transcript_summary_text,
            "source_antragsvorlage_text":  self.source_antragsvorlage_text,
            "source_vorantrag_text":       self.source_vorantrag_text,
            "source_prozessreflexion_text": self.source_prozessreflexion_text,
            # v19 Phase 1 + C:
            "quality_check":   self.quality_check,
            "parent_job_id":   self.parent_job_id,
            "repair_input":    self.repair_input,
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
        # Referenzen auf Hintergrund-DB-Tasks. Ohne Referenz darf der
        # Python-GC ensure_future-Tasks jederzeit einsammeln (offizielle
        # asyncio-Doku) - der DB-Insert kann dann still verloren gehen.
        # Das Set haelt die Referenz bis zum Abschluss; add_done_callback
        # raeumt auf und loggt Exceptions statt sie zu verschlucken.
        self._bg_tasks: set = set()

    def _spawn_db_task(self, coro, what: str) -> None:
        """Startet einen Hintergrund-DB-Task mit gehaltener Referenz.

        Ersetzt das nackte asyncio.ensure_future() (fire-and-forget):
          1. Referenz im Set -> kein GC-Verlust des laufenden Tasks.
          2. done_callback loggt Exceptions, die sonst nur beim
             Interpreter-Shutdown als 'Task exception was never retrieved'
             auftauchen wuerden.
          3. v19.13: expliziter get_running_loop() statt ensure_future().
             ensure_future() faellt ohne laufenden Loop auf das seit
             Python 3.10 deprecatete get_event_loop() zurueck, das unter
             3.12 RuntimeError wirft. create_job() ist SYNCHRON - der
             Fehler flog dem Aufrufer entgegen, nachdem der Job bereits
             im Cache lag (inkonsistenter Zustand: Job im Speicher, aber
             Request abgebrochen). Alle Produktions-Aufrufer sind
             async-Handler (jobs.py create_generate_job / repair_execute /
             cancel), dort gibt es immer einen laufenden Loop. Ohne Loop
             (reiner Sync-Kontext, Unit-Tests) wird der DB-Task jetzt
             uebersprungen und laut geloggt statt den Aufrufer zu sprengen.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Coroutine explizit schliessen, sonst RuntimeWarning
            # "coroutine ... was never awaited" beim naechsten GC-Lauf.
            coro.close()
            logger.error(
                "Kein laufender Event-Loop - Hintergrund-DB-Task (%s) "
                "uebersprungen. Der Job existiert nur im Cache, NICHT in "
                "der DB.", what,
            )
            return

        task = loop.create_task(coro)
        self._bg_tasks.add(task)

        def _done(t: asyncio.Task, _what=what) -> None:
            self._bg_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error("Hintergrund-DB-Task fehlgeschlagen (%s): %r",
                             _what, t.exception())

        task.add_done_callback(_done)

    def create_job(
        self,
        workflow: str,
        description: str = "",
        therapeut_id: Optional[str] = None,
        patient_kuerzel: Optional[str] = None,
    ) -> JobState:
        """Erstellt einen neuen Job im Cache und persistiert ihn asynchron in der DB.

        Sprint B: therapeut_id und patient_kuerzel werden NEU mit persistiert
        damit list_filtered() pro Therapeut filtern und das Frontend die
        Patientenkennung in der Job-Liste anzeigen kann. Beide optional fuer
        Backwards-Compat (alte Aufrufer ohne diese Felder).
        """
        job_id = uuid.uuid4().hex
        state = JobState(job_id, workflow, description, therapeut_id, patient_kuerzel)
        self._cache[job_id] = state
        self._cleanup_cache()

        queue_size = len([j for j in self._cache.values()
                          if j.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value)])
        logger.info("Job erstellt: %s (%s) | Warteschlange: %d", job_id, workflow, queue_size)

        # DB-Insert asynchron im Hintergrund - mit gehaltener Task-Referenz
        # (siehe _spawn_db_task; vorher fire-and-forget mit GC-Verlust-Risiko)
        self._spawn_db_task(self._db_insert_job(
            job_id, workflow, description, therapeut_id, patient_kuerzel,
        ), what=f"insert_job {job_id}")

        return state

    def create_repair_job(
        self,
        parent_job_id: str,
        workflow: str,
        description: str = "",
        repair_input: Optional[dict] = None,
    ) -> JobState:
        """v19 Phase C: Erstellt einen Repair-Job mit Parent-Verweis.

        Im Unterschied zu create_job:
          - parent_job_id wird im JobState gesetzt -> persistiert in
            jobs.parent_job_id
          - repair_input (= {accepted_issue_codes, user_hint, final_prompt,
            custom_final_prompt_used}) wird im JobState gesetzt -> persistiert
            in jobs.repair_input_json
        Sonst identisch zu create_job. Der Job geht durch dieselbe Queue,
        wird mit derselben run_job-Methode ausgefuehrt (mit eigener Coroutine).
        """
        state = self.create_job(workflow, description)
        state.parent_job_id = parent_job_id
        state.repair_input = repair_input or {}
        logger.info(
            "Repair-Job erstellt: %s (parent=%s, workflow=%s)",
            state.job_id, parent_job_id, workflow,
        )
        return state

    async def _db_insert_job(
        self,
        job_id: str,
        workflow: str,
        description: str,
        therapeut_id: Optional[str] = None,
        patient_kuerzel: Optional[str] = None,
    ) -> None:
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
                    therapeut_id=therapeut_id,
                    patient_kuerzel=patient_kuerzel,
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
                    "therapeut_id":    db_job.therapeut_id,
                    "patient_kuerzel": db_job.patient_kuerzel,
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
                    # v19.2: Two-Stage-Pipeline-Felder. None bei Jobs aus
                    # Pre-v19.2-Zeit oder Workflows die Stage 1 nicht nutzen.
                    "verlauf_summary_text":  db_job.verlauf_summary_text,
                    "verlauf_summary_audit": db_job.verlauf_summary_audit,
                    # v19.3: Repair-Kontext-Felder. None bei Pre-v19.3-Jobs
                    # oder Workflows ohne entsprechende Quelle.
                    # ACHTUNG: result_transcript ist hier bewusst NICHT
                    # enthalten (Datenschutz - kein Transkript ueber API).
                    # Fuer Repair-Coroutinen siehe get_repair_context().
                    "source_verlauf_text":         db_job.source_verlauf_text,
                    "transcript_summary_text":     db_job.transcript_summary_text,
                    "source_antragsvorlage_text":  db_job.source_antragsvorlage_text,
                    "source_vorantrag_text":       db_job.source_vorantrag_text,
                    "source_prozessreflexion_text": db_job.source_prozessreflexion_text,
                    # v19 Phase 1 + C: QualityCheck + Repair-Beziehung.
                    "quality_check":   db_job.quality_check_json,
                    "parent_job_id":   db_job.parent_job_id,
                    "repair_input":    db_job.repair_input_json,
                }
        except Exception as e:
            logger.warning("Job-DB-Lookup fehlgeschlagen: %s", e)
            return None

    async def get_repair_context(self, job_id: str) -> Optional[dict]:
        """v19.3: Laedt die Quelldaten fuer einen Repair-Call.

        Im Gegensatz zu to_dict()/get_job_from_db() enthaelt das Result
        ABSICHTLICH das Roh-Transkript (result_transcript) - das wird vom
        Repair-Coroutine als Kontext gebraucht. Diese Funktion darf NICHT
        ueber die API exposed werden (Datenschutz: Transkripte gehen nie
        ueber die externe API raus).

        Hierarchie (bevorzugt: Synthese, fallback: Roh-Version):
          1. verlauf_summary_text  → bei groesseren Verlaeufen (>1500w)
          2. source_verlauf_text   → Roh-Verlauf wenn Stage-1 nicht lief
          3. transcript_summary_text → bei groesseren Transkripten (>3500w)
          4. result_transcript     → Roh-Transkript wenn Stage-1 nicht lief
          5. source_antragsvorlage_text → Anamnese/Diagnosen aus Vorlage
          6. source_vorantrag_text → Bei Folgeverlaengerung

        Returns dict mit den Quellen (auch alle keys vorhanden bei NULL):
          {
            "verlauf_summary_text":        str | None,
            "source_verlauf_text":         str | None,
            "transcript_summary_text":     str | None,
            "result_transcript":           str | None,
            "source_antragsvorlage_text":  str | None,
            "source_vorantrag_text":       str | None,
            "result_text":                 str | None,  # Original-Bericht
            "workflow":                    str,
          }
        Returns None wenn Job nicht existiert.
        """
        # Zuerst Cache (laeuft gerade) - hat alle Quellen in JobState
        cached = self._cache.get(job_id)
        if cached:
            return {
                "verlauf_summary_text":        cached.verlauf_summary_text,
                "source_verlauf_text":         cached.source_verlauf_text,
                "transcript_summary_text":     cached.transcript_summary_text,
                "result_transcript":           cached.result_transcript,
                "source_antragsvorlage_text":  cached.source_antragsvorlage_text,
                "source_vorantrag_text":       cached.source_vorantrag_text,
                "source_prozessreflexion_text": cached.source_prozessreflexion_text,
                "result_text":                 cached.result_text,
                "workflow":                    cached.workflow,
            }
        # Sonst DB-Load mit allen Feldern
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
                    "verlauf_summary_text":        db_job.verlauf_summary_text,
                    "source_verlauf_text":         db_job.source_verlauf_text,
                    "transcript_summary_text":     db_job.transcript_summary_text,
                    "result_transcript":           db_job.result_transcript,
                    "source_antragsvorlage_text":  db_job.source_antragsvorlage_text,
                    "source_vorantrag_text":       db_job.source_vorantrag_text,
                    "source_prozessreflexion_text": db_job.source_prozessreflexion_text,
                    "result_text":                 db_job.result_text,
                    "workflow":                    db_job.workflow,
                }
        except Exception as e:
            logger.warning("Repair-Kontext-Lookup fehlgeschlagen: %s", e)
            return None

    def get_all_jobs(self) -> list[JobState]:
        return sorted(self._cache.values(), key=lambda j: j.created_at, reverse=True)

    async def list_filtered(
        self,
        workflow: Optional[str] = None,
        therapeut_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Sprint B (Multi-Job-Liste P1): Job-Liste aus der DB, optional gefiltert.

        Quelle ist die DB, NICHT der Cache - damit auch aus dem Cache evictete
        oder nach Restart neu hochgefahrene Jobs sichtbar bleiben.

        Fuer Jobs die zusaetzlich noch im Cache liegen (laufend/kuerzlich
        fertig) wird der Cache-Wert bevorzugt - der hat Live-Progress, der DB-
        Wert ist immer ein Schnappschuss vom letzten _persist_job().

        Bei DB-Fehler: Fallback auf reinen Cache-Inhalt mit denselben Filtern.
        """
        try:
            from app.core.database import async_session_factory
            from app.models.db import Job as JobModel
            from sqlalchemy import select, desc
            async with async_session_factory() as db:
                q = (
                    select(JobModel)
                    .order_by(desc(JobModel.created_at))
                    .limit(limit)
                )
                if workflow:
                    q = q.where(JobModel.workflow == workflow)
                if therapeut_id:
                    q = q.where(JobModel.therapeut_id == therapeut_id)
                result = await db.execute(q)
                db_jobs = result.scalars().all()

            out: list[dict] = []
            for db_job in db_jobs:
                cached = self._cache.get(db_job.id)
                if cached is not None:
                    out.append(cached.to_dict())
                else:
                    out.append(self._db_job_to_dict(db_job))
            return out
        except Exception as e:
            logger.warning(
                "Job-Liste aus DB fehlgeschlagen: %s - Fallback auf Cache", e,
            )
            jobs = self.get_all_jobs()
            if workflow:
                jobs = [j for j in jobs if j.workflow == workflow]
            if therapeut_id:
                jobs = [j for j in jobs if j.therapeut_id == therapeut_id]
            return [j.to_dict() for j in jobs[:limit]]

    @staticmethod
    def _db_job_to_dict(db_job) -> dict:
        """Sprint B: zentraler Konverter SQLAlchemy-Job -> API-Dict.

        Bewusste Duplikation der Felder aus get_job_from_db (gleiches Schema).
        Wenn dort ein Feld dazukommt, hier auch ergaenzen - sonst sieht das
        Frontend in list_filtered() weniger als in get_job().
        """
        return {
            "job_id":          db_job.id,
            "workflow":        db_job.workflow,
            "description":     db_job.description or "",
            "therapeut_id":    db_job.therapeut_id,
            "patient_kuerzel": db_job.patient_kuerzel,
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
            "generation_telemetry":        db_job.generation_telemetry,
            "verlauf_summary_text":        db_job.verlauf_summary_text,
            "verlauf_summary_audit":       db_job.verlauf_summary_audit,
            "source_verlauf_text":         db_job.source_verlauf_text,
            "transcript_summary_text":     db_job.transcript_summary_text,
            "source_antragsvorlage_text":  db_job.source_antragsvorlage_text,
            "source_vorantrag_text":       db_job.source_vorantrag_text,
            "source_prozessreflexion_text": db_job.source_prozessreflexion_text,
            "quality_check":   db_job.quality_check_json,
            "parent_job_id":   db_job.parent_job_id,
            "repair_input":    db_job.repair_input_json,
        }

    def cancel_job(self, job_id: str) -> bool:
        """Markiert einen Job als abzubrechen (Cache + DB)."""
        state = self._cache.get(job_id)
        if not state:
            return False
        if state.status in (JobStatus.DONE.value, JobStatus.ERROR.value, JobStatus.CANCELLED.value):
            return False
        state._cancel_requested = True
        logger.info("Abbruch angefordert: %s (%s)", job_id, state.workflow)
        # Async DB-Update - mit gehaltener Task-Referenz (siehe _spawn_db_task)
        self._spawn_db_task(self._db_set_cancel(job_id), what=f"set_cancel {job_id}")
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

    async def delete_job_permanent(self, job_id: str) -> dict:
        """Sprint B (Multi-Job-Liste P1): endgueltiges Loeschen aus Cache + DB.

        Nur erlaubt fuer terminale Stati (done, error, cancelled). Pending/running
        Jobs muessen zuerst per cancel_job() abgebrochen werden.

        Anders als Recording (das deleted_at-Soft-Delete fuer Audit nutzt) ist
        Job-Permanent-Delete eine harte DELETE-Operation, weil der Nutzer
        bewusst seine alten Jobs aufraeumen koennen will. Wenn das spaeter
        problematisch ist, kann hier auf Soft-Delete umgestellt werden.

        Rueckgabe:
          {"job_id": ..., "deleted": True,  "reason": None}        - Erfolg
          {"job_id": ..., "deleted": False, "reason": "..."}       - blockiert
        Wirft KeyError wenn Job weder im Cache noch in der DB ist.
        """
        terminal = (
            JobStatus.DONE.value,
            JobStatus.ERROR.value,
            JobStatus.CANCELLED.value,
        )

        # 1. Status ermitteln - Cache zuerst (frischer), dann DB
        status: Optional[str] = None
        cached = self._cache.get(job_id)
        if cached is not None:
            status = cached.status
        else:
            db_dict = await self.get_job_from_db(job_id)
            if db_dict is not None:
                status = db_dict["status"]

        if status is None:
            raise KeyError(job_id)

        if status not in terminal:
            return {
                "job_id":  job_id,
                "deleted": False,
                "reason":  (
                    f"Job hat Status '{status}' - bitte erst per "
                    f"DELETE /api/jobs/{job_id} abbrechen."
                ),
            }

        # 2. Aus DB loeschen
        try:
            from app.core.database import async_session_factory
            from app.models.db import Job as JobModel
            from sqlalchemy import delete
            async with async_session_factory() as db:
                await db.execute(delete(JobModel).where(JobModel.id == job_id))
                await db.commit()
        except Exception as e:
            logger.warning("Job-DB-Delete fehlgeschlagen: %s", e)

        # 3. Aus Cache entfernen
        self._cache.pop(job_id, None)

        logger.info("Job dauerhaft geloescht: %s", job_id)
        return {"job_id": job_id, "deleted": True, "reason": None}

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
                        generation_telemetry=state.generation_telemetry,
                        # v19.2: Stage-1-Pipeline-Felder
                        verlauf_summary_text=state.verlauf_summary_text,
                        verlauf_summary_audit=state.verlauf_summary_audit,
                        # v19.3: Repair-Kontext
                        source_verlauf_text=state.source_verlauf_text,
                        transcript_summary_text=state.transcript_summary_text,
                        source_antragsvorlage_text=state.source_antragsvorlage_text,
                        source_vorantrag_text=state.source_vorantrag_text,
                        source_prozessreflexion_text=state.source_prozessreflexion_text,
                        # v19 Phase 1 + C
                        quality_check_json=state.quality_check,
                        parent_job_id=state.parent_job_id,
                        repair_input_json=state.repair_input,
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
            # v19.1: Telemetrie aus dem LLM-Result (Pipeline jobs.py
            # haengt sie an result["generation_telemetry"] an).
            job.generation_telemetry = result.get("generation_telemetry")
            # v19.2: Stage-1-Pipeline-Ergebnis.
            # verlauf_summary_text wird aus dem Audit-Bundle abgeleitet,
            # falls Stage 1 erfolgreich war (applied=True). Der eigentliche
            # Summary-Text wird in der jobs.py-Pipeline NICHT separat an
            # result angehaengt – er wandert direkt als verlaufsdoku_text in
            # die Generierung. Wir rekonstruieren ihn fuer die Persistierung
            # NICHT aus dem Result; statt dessen wird er von jobs.py optional
            # an result["verlauf_summary_text"] gehaengt (kann None bleiben).
            job.verlauf_summary_audit = result.get("verlauf_summary_audit")
            job.verlauf_summary_text  = result.get("verlauf_summary_text")
            # v19.3: Repair-Kontext-Felder
            job.source_verlauf_text         = result.get("source_verlauf_text")
            job.transcript_summary_text     = result.get("transcript_summary_text")
            job.source_antragsvorlage_text  = result.get("source_antragsvorlage_text")
            job.source_vorantrag_text       = result.get("source_vorantrag_text")
            job.source_prozessreflexion_text = result.get("source_prozessreflexion_text")
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
            # v19 Phase 1: QualityCheck NUR fuer erfolgreich abgeschlossene
            # Jobs mit Text. Eigener try/except - ein QC-Bug darf einen
            # produktiven Job nicht in den ERROR-State kippen.
            if job.status == JobStatus.DONE.value and job.result_text:
                try:
                    from app.services.quality_check import (
                        combined_result_text, run_quality_check, serialize_issues,
                    )
                    from app.services.quality_specs import split_stichpunkte
                    # Anamnese liefert Anamnese-Text + Befund-Text als zwei
                    # getrennte Felder. Der QualityCheck braucht den verketteten
                    # Text (mit ###BEFUND###-Separator) damit
                    # MISSING_KEYWORD/SECTION-Checks und BEFUND_SEPARATOR_MISSING
                    # konsistent mit dem Eval-Framework greifen.
                    qc_text = combined_result_text(
                        job.workflow, job.result_text, job.result_befund,
                    )
                    # v19.5/v19.14a: Quellentreue-Check braucht die Quelle.
                    # Aufbau siehe _qc_fidelity_source() am Dateiende - dort
                    # auch der Repair-Job-Pfad (v19.14a).
                    _fidelity_source = _qc_fidelity_source(job)
                    if not _fidelity_source:
                        # D2: legitimer Fall (fehlende Felder, DB-Luecke nach
                        # Pod-Neustart) - Schritt 7 entfaellt dann still.
                        # Log macht im Betrieb unterscheidbar: "Check lief und
                        # fand nichts" vs. "Check lief gar nicht".
                        logger.info(
                            "QualityCheck %s (%s): keine Quelle fuer "
                            "SOURCE_FIDELITY - Schritt entfaellt.",
                            job.job_id, job.workflow,
                        )
                    # v19.6: Datenschutz-Namensleck (Punkt 1) + Stichpunkt-Check
                    # (Punkt 6) brauchen den realen Namen bzw. die Fokus-Themen.
                    # Beide werden in jobs.py als Ad-hoc-Attribute auf dem Job
                    # hinterlegt (in-process, kein DB-Feld). getattr -> robust,
                    # falls nicht gesetzt (aeltere Aufrufer, Repair-Jobs).
                    _patient_name = getattr(job, "patient_name", None)
                    _stichpunkte = split_stichpunkte(getattr(job, "fokus_themen", None))
                    _sa_empty = getattr(job, "selbstauskunft_empty", None)
                    # v19.13: Ad-hoc-Flag aus jobs.py (Pattern selbstauskunft_empty).
                    # None/False bei Repair-Jobs und aelteren Aufrufern -> Check entfaellt.
                    _reflexion_present = getattr(job, "prozessreflexion_present", None)
                    # v19.15 (B3): Antragsvorlagen-Text fuer den Platzhalter-Check
                    # (Muster-/Stilvorlage im falschen Slot). Ad-hoc-Attribut aus
                    # jobs.py, None bei Repair-Jobs/aelteren Aufrufern -> Check entfaellt.
                    _antrag_qc_text = getattr(job, "antragsvorlage_qc_text", None)
                    # v19.15 (C1): vermutlich abgeschnittene Quellen (jobs.py).
                    _trunc_sources = getattr(job, "truncated_sources", None)
                    # v19.16 (T4): Coverage-Luecke des verwendeten Recordings.
                    _cov_gap = getattr(job, "transcript_coverage_gap_s", None)
                    issues = run_quality_check(
                        qc_text, job.workflow, source_text=_fidelity_source,
                        stichpunkte=_stichpunkte, patient_name=_patient_name,
                        selbstauskunft_empty=_sa_empty,
                        prozessreflexion_present=_reflexion_present,
                        antragsvorlage_text=_antrag_qc_text,
                        truncated_sources=_trunc_sources,
                        transcript_coverage_gap_s=_cov_gap,
                    )
                    job.quality_check = serialize_issues(issues, workflow=job.workflow)
                    logger.info(
                        "QualityCheck %s (%s): %d Issues (%s)",
                        job.job_id, job.workflow,
                        job.quality_check["summary"]["total"],
                        ", ".join(
                            f"{k}={v}" for k, v in job.quality_check["summary"].items()
                            if k != "total"
                        ),
                    )
                except Exception as qc_err:
                    logger.warning(
                        "QualityCheck fehlgeschlagen fuer Job %s: %s",
                        job.job_id, qc_err,
                    )
                    job.quality_check = None
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


def _qc_fidelity_source(job) -> str:
    """v19.14a: baut die Quelle fuer den Quellentreue-Check (SOURCE_FIDELITY).

    Normale Generate-Jobs: Roh-Transkript + volle Dokument-Extrakte (die
    Felder werden in run_job aus dem Coroutine-Result gesetzt). Volle
    Extrakte, KEIN Summary -> keine Falsch-Positive durch Verdichtung
    (v19.5-Learning). source_prozessreflexion_text ist seit v19.13 dabei,
    sonst flaggt der Check korrekt eingebaute Reflexions-Passagen als
    unbelegt.

    Repair-Jobs: die source_*-Felder sind dort None (das Repair-Coroutine-
    Result traegt sie nicht) - der Quellentreue-Check lief auf Repair-Output
    deshalb bis v19.13 ins Leere. Der LLM-Voll-Repair schreibt aber den
    GESAMTEN Text neu und braucht das Netz gegen aufgestuelptes Vokabular
    genauso wie die Originalgenerierung. Fix: repair_execute (jobs.py) legt
    die Parent-Quellen als in-process Attribut qc_source_text auf den
    Repair-Job (getattr-Lesepfad, analog patient_name/fokus_themen).
    Bewusst NICHT als source_*-Felder persistiert - das wuerde Transkript/
    Extrakte redundant auf der Repair-Zeile duplizieren (Datenschutz:
    kleinste noetige PII-Flaeche; die Quellen liegen bereits beim Parent).
    """
    parts = (
        getattr(job, "result_transcript", None),
        getattr(job, "source_verlauf_text", None),
        getattr(job, "source_antragsvorlage_text", None),
        getattr(job, "source_vorantrag_text", None),
        getattr(job, "source_prozessreflexion_text", None),
        getattr(job, "qc_source_text", None),   # v19.14a: Repair-Jobs
    )
    return "\n\n".join(s for s in parts if s and s.strip())


# Globale Instanz
job_queue = JobQueue()
