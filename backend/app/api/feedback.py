"""
backend/app/api/feedback.py — Nutzerfeedback zu generierten Ausgaben (Sprint F1).

POST /feedback nimmt {job_id, rating 1–5, text, context} entgegen und schreibt
EINE JSON-Zeile (JSONL) nach feedback.log im Log-Verzeichnis (neben
prompts.log / systelios.log). Über job_id ist jeder Eintrag direkt mit den
Prompt/Output-Paaren in prompts.log verlinkbar (dort: "JOB: <id>").

Design-Entscheidungen (Cars10, 2026-07-21):
- Nutzer wird SERVERSEITIG aus dem HMAC-verifizierten X-Systelios-User-Header
  übernommen (get_current_user) — nicht aus dem Body, nicht fälschbar.
- Append-only: Mehrfach-Feedback zum selben Job erlaubt (z.B. nach Repair);
  jede Abgabe = neue Zeile. Keine Dedup-Logik.
- Job nicht in DB gefunden → Feedback wird TROTZDEM geloggt (job: null),
  kein 404. Feedback darf nie verloren gehen (z.B. nach Retention-Cleanup).
- Job-Anreicherung: kompakte Metadaten + Telemetrie, aber KEINE Text-Blobs
  (result_text, source_*) — die stehen bereits in prompts.log/DB und würden
  feedback.log nur aufblähen.
- Rotation: täglich wie prompts.log, backupCount=90 Tage (§6a Einwilligung
  v1.1, Retention-Sprint 2026-08-12; vorher 3650). Der Freitext kann
  Patientenbezug enthalten — Auswertung muss innerhalb der 90-Tage-Frist
  erfolgen oder anonymisiert exportiert werden.
- Push (optional, FEEDBACK_NOTIFY): fire-and-forget im Hintergrund, ohne
  Freitext (DSGVO — siehe feedback_notify.py). Scheitert der Push, ist das
  für den POST irrelevant.
"""
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth import get_current_user
from app.core.database import async_session_factory
from app.models.db import Job
from app.services.feedback_notify import notify_feedback_background

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["Feedback"])

# ── feedback.log — dedizierter Logger, Tagesrotation, 90 Tage (§6a) ──
_FEEDBACK_LOG_BACKUP_DAYS = 90

_feedback_logger = logging.getLogger("systelios.feedback")


def _setup_feedback_logger() -> None:
    """Richtet den Feedback-Logger ein (einmalig beim Import). Muster wie
    _setup_prompt_logger() in jobs.py — gleiches Log-Verzeichnis."""
    if _feedback_logger.handlers:
        return
    _feedback_logger.setLevel(logging.INFO)
    _feedback_logger.propagate = False
    log_dir = Path(os.environ.get("LOG_FILE", "/workspace/systelios.log")).parent
    feedback_file = log_dir / "feedback.log"
    try:
        feedback_file.parent.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            str(feedback_file),
            when="midnight",
            interval=1,
            backupCount=_FEEDBACK_LOG_BACKUP_DAYS,
            encoding="utf-8",
            utc=False,
        )
        handler.suffix = "%Y-%m-%d"
        # Nackte Message: jede Zeile ist ein vollständiges JSON-Objekt (JSONL).
        # Timestamp steckt strukturiert im JSON ("ts"), kein Formatter-Präfix.
        handler.setFormatter(logging.Formatter("%(message)s"))
        _feedback_logger.addHandler(handler)
    except Exception as e:
        logger.warning("Feedback-Logger konnte nicht eingerichtet werden: %s", e)


_setup_feedback_logger()


class FeedbackIn(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)
    rating: int = Field(ge=1, le=5)
    text: str = Field(default="", max_length=10000)
    # Unterscheidet mehrere Ausgaben desselben Jobs (P2: "anamnese"/"befund").
    context: str = Field(default="", max_length=64)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


async def _load_job_info(job_id: str) -> dict | None:
    """Kompakte Job-Metadaten für die Log-Zeile. None wenn Job unbekannt."""
    async with async_session_factory() as session:
        job = (await session.execute(
            select(Job).where(Job.id == job_id)
        )).scalar_one_or_none()
    if job is None:
        return None
    return {
        "workflow":         job.workflow,
        "status":           job.status,
        "therapeut_id":     job.therapeut_id,
        "patient_kuerzel":  job.patient_kuerzel,
        "model_used":       job.model_used,
        "duration_s":       job.duration_s,
        "created_at":       _iso(job.created_at),
        "started_at":       _iso(job.started_at),
        "finished_at":      _iso(job.finished_at),
        "error_msg":        job.error_msg,
        "quality_check":    job.quality_check_json,
        "telemetry":        job.generation_telemetry,
    }


# ── v19.19 (R5): Prompt/Output-Kopie fuer bemaengelte Jobs ───────────────────
# prompts.log rotiert nach 14 Dateien (jobs.py: _PROMPT_LOG_BACKUP_DAYS) -
# der Fall e.krause vom 07.08. war bei der Analyse am 09.09. schon weg.
# Entscheid Cars10 (F4, 2026-09-09): NUR fuer Jobs mit Feedback die
# prompts.log-Bloecke (alle CALLs des Jobs: Stage 1, Hauptcall, Repair)
# in eine eigene Datei unter feedback_cases/ kopieren; Retention 90 Tage
# wie feedback.log (retention.cleanup_feedback_cases). Nicht in die
# feedback.log-Zeile selbst (Design: keine Text-Blobs dort).

def _prompt_log_dir():
    from pathlib import Path
    return Path(os.environ.get("LOG_FILE", "/workspace/systelios.log")).parent


def _collect_prompt_log_blocks(job_id: str) -> str:
    """Alle prompts.log-Bloecke (aktuelle + rotierte Dateien) mit JOB: <id>."""
    import re as _re
    log_dir = _prompt_log_dir()
    marker = f"JOB: {job_id}"
    found: list[str] = []
    for f in sorted(log_dir.glob("prompts.log*")):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if marker not in txt:
            continue
        for block in _re.split(r"\n(?=\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", txt):
            if marker in block:
                found.append(block.strip("\n"))
    return "\n\n".join(found)


def _write_feedback_case(job_id: str, record: dict) -> "str | None":
    """Schreibt feedback_cases/<ts>_<job8>.log; gibt den Pfad zurueck."""
    try:
        blocks = _collect_prompt_log_blocks(job_id)
        if not blocks:
            return None
        case_dir = _prompt_log_dir() / "feedback_cases"
        case_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = case_dir / f"{ts}_{job_id[:8]}.log"
        header = (
            f"# FEEDBACK-FALL {job_id}\n# rating={record.get('rating')} "
            f"user={record.get('user')} context={record.get('context')!r}\n"
            f"# text: {record.get('text')!r}\n"
            f"# (Kopie der prompts.log-Bloecke; Retention 90 Tage)\n\n"
        )
        path.write_text(header + blocks + "\n", encoding="utf-8")
        return str(path)
    except Exception as e:  # noqa: BLE001 - Kopie darf Feedback nie blockieren
        logger.warning("Feedback-Fallkopie fehlgeschlagen (%s): %s", job_id, e)
        return None


@router.post("")
async def submit_feedback(body: FeedbackIn, user: str = Depends(get_current_user)):
    """
    Nimmt Feedback entgegen, reichert es mit Job-Metadaten an, schreibt es als
    JSONL nach feedback.log und stößt (falls konfiguriert) den Push an.
    """
    job_info = None
    try:
        job_info = await _load_job_info(body.job_id)
    except Exception as e:  # noqa: BLE001 — DB-Ausfall darf Feedback nicht verlieren
        logger.warning("Feedback: Job-Anreicherung fehlgeschlagen (%s): %s", body.job_id, e)

    record = {
        "ts":       datetime.now(timezone.utc).astimezone().isoformat(),
        "user":     user,                    # serverseitig verifiziert (HMAC)
        "rating":   body.rating,
        "text":     body.text.strip(),
        "context":  body.context.strip(),
        "job_id":   body.job_id,             # → prompts.log: "JOB: <id>"
        "job":      job_info,                # null wenn Job nicht (mehr) in DB
    }
    # v19.19 (R5): Prompt/Output des Jobs als Fallkopie sichern (90 Tage).
    record["prompt_log_file"] = _write_feedback_case(body.job_id, record)
    _feedback_logger.info(json.dumps(record, ensure_ascii=False))

    workflow = (job_info or {}).get("workflow") or body.context or "?"
    notify_feedback_background(body.rating, workflow, body.job_id, user)

    return {"ok": True}
