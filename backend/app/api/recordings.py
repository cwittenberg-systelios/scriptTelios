"""
backend/app/api/recordings.py

P0-Aufnahmen: Upload → sofortige Transkription im Hintergrund → Abruf in P1–P4.
Löschung läuft über externe Datenschutz-Prozesse (soft-delete via deleted_at,
physische Datei-Löschung durch separaten Retention-Job oder manuell).

Priorität: Ein P0-Recording das in P1–P4 als Quelle gewählt wird, überspringt
die Transkriptionsphase (Transkript ist bereits vorhanden) und landet sofort
in der LLM-Generierungsphase. Das spart typischerweise 2–5 Minuten pro Job.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, update

from app.core.database import async_session_factory
from app.core.files import recordings_dir, ALLOWED_AUDIO
from app.core.config import settings
from app.models.db import Recording
from app.services import transcription as _transcription

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recordings", tags=["Aufnahmen"])

# ── Prioritäts-Queue für P0-Transkriptionen ───────────────────────────────────
# Kleinere Zahl = höhere Priorität.
# P0-Jobs haben Priorität 10 — sie warten hinter jedem aktiven P1/P2-Job (Prio 1).
# Die Queue wird von p0_worker() abgearbeitet, der in main.py als Task gestartet wird.
_PRIO_P0 = 10

p0_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()


async def _active_job_running() -> bool:
    """True wenn mindestens ein P1/P2-Job mit status='running' in der DB ist."""
    from sqlalchemy import select, func
    from app.models.db import Job
    async with async_session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(Job).where(Job.status == "running")
        )
        return (result.scalar() or 0) > 0


async def p0_worker():
    """
    Dauerhafter Hintergrund-Worker für P0-Transkriptionen.
    Wartet solange ein aktiver P1/P2-Job läuft, dann arbeitet er
    das nächste Element aus der PriorityQueue ab.
    Neue P1-Jobs (niedrigere Prioritätszahl) werden automatisch
    vor wartenden P0-Jobs einsortiert.
    """
    logger.info("P0-Worker gestartet")
    while True:
        try:
            # Blockiert bis ein Job in der Queue ist
            prio, rec_id, audio_path = await p0_queue.get()

            # Warten solange aktiver P1/P2-Job läuft (max. 60 Min)
            waited = 0
            while await _active_job_running():
                if waited == 0:
                    logger.info("P0-Worker wartet auf aktiven Job (Recording %d)", rec_id)
                await asyncio.sleep(5)
                waited += 5
                if waited > 3600:
                    logger.warning("P0-Worker Timeout nach 60 Min — Recording %d wird trotzdem transkribiert", rec_id)
                    break

            await _transcribe_background(rec_id, audio_path)
            p0_queue.task_done()
        except asyncio.CancelledError:
            logger.info("P0-Worker beendet")
            break
        except Exception as e:
            logger.exception("P0-Worker unerwarteter Fehler: %s", e)
            await asyncio.sleep(10)  # kurze Pause vor nächstem Versuch


# ── Schemas ──────────────────────────────────────────────────────────────────

class RecordingOut(BaseModel):
    id: int
    label: Optional[str] = None
    duration_s: Optional[float] = None
    transcript: Optional[str] = None
    status: str
    error_msg: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _set_status(
    rec_id: int,
    status: str,
    error_msg: str = None,
    transcript: str = None,
    duration_s: float = None,
):
    async with async_session_factory() as session:
        values = {"status": status, "error_msg": error_msg}
        if transcript is not None:
            values["transcript"] = transcript
        if duration_s is not None:
            values["duration_s"] = duration_s
        await session.execute(
            update(Recording).where(Recording.id == rec_id).values(**values)
        )
        await session.commit()


async def _transcribe_background(rec_id: int, audio_path: Path):
    """Transkribiert im Hintergrund und aktualisiert DB-Status."""
    try:
        await _set_status(rec_id, "transcribing")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: asyncio.run(_transcription.transcribe_audio(audio_path))
        )
        await _set_status(
            rec_id,
            status="ready",
            transcript=result["transcript"],
            duration_s=result.get("duration_seconds"),
        )
        logger.info(
            "Recording %d transkribiert (%.0fs, %d Wörter)",
            rec_id,
            result.get("duration_seconds", 0),
            len(result["transcript"].split()),
        )
        # Audiodatei nach Transkription löschen (Datenschutz)
        if settings.DELETE_AUDIO_AFTER_TRANSCRIPTION and audio_path.exists():
            audio_path.unlink(missing_ok=True)
            logger.info("Audiodatei nach Transkription gelöscht: %s", audio_path.name)
    except Exception as e:
        logger.exception("Transkription Recording %d fehlgeschlagen", rec_id)
        await _set_status(rec_id, "error", error_msg=str(e)[:500])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=RecordingOut, status_code=201)
async def upload_recording(
    audio: UploadFile = File(...),
    label: Optional[str] = Form(None),
):
    """
    Nimmt Audiodatei entgegen, speichert sie persistent und startet
    Transkription asynchron. Antwortet sofort (status=uploading).
    """
    suffix = Path(audio.filename or "aufnahme.webm").suffix.lower() or ".webm"
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(
            status_code=422,
            detail=f"Dateiformat '{suffix}' nicht unterstützt. "
                   f"Erlaubt: {', '.join(sorted(ALLOWED_AUDIO))}",
        )

    content = await audio.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.MAX_UPLOAD_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Datei zu groß ({size_mb:.1f} MB). Maximum: {settings.MAX_UPLOAD_MB} MB",
        )

    filename = f"{uuid.uuid4().hex}{suffix}"
    audio_path = recordings_dir() / filename
    audio_path.write_bytes(content)

    async with async_session_factory() as session:
        rec = Recording(
            label=label.strip()[:120] if label and label.strip() else None,
            filename=filename,
            status="uploading",
        )
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        rec_id = rec.id
        created_at_iso = rec.created_at.isoformat()

    await p0_queue.put((_PRIO_P0, rec_id, audio_path))
    logger.info("Recording %d in P0-Queue eingereiht (Queuegröße: %d)", rec_id, p0_queue.qsize())

    return RecordingOut(
        id=rec_id,
        label=label,
        duration_s=None,
        transcript=None,
        status="uploading",
        error_msg=None,
        created_at=created_at_iso,
    )


@router.get("", response_model=list[RecordingOut])
async def list_recordings():
    """Alle nicht gelöschten Aufnahmen, neueste zuerst (max. 50)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(Recording.deleted_at.is_(None))
            .order_by(Recording.created_at.desc())
            .limit(50)
        )
        rows = result.scalars().all()

    return [
        RecordingOut(
            id=r.id,
            label=r.label,
            duration_s=r.duration_s,
            transcript=r.transcript,
            status=r.status,
            error_msg=r.error_msg,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/{rec_id}", response_model=RecordingOut)
async def get_recording(rec_id: int):
    """Einzelnes Recording abrufen (z.B. für Status-Polling)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(Recording.id == rec_id, Recording.deleted_at.is_(None))
        )
        rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording nicht gefunden")
    return RecordingOut(
        id=rec.id, label=rec.label, duration_s=rec.duration_s,
        transcript=rec.transcript, status=rec.status,
        error_msg=rec.error_msg, created_at=rec.created_at.isoformat(),
    )


@router.delete("/{rec_id}", status_code=204)
async def delete_recording(rec_id: int):
    """
    Soft-Delete + physische Audiodatei-Löschung.
    Transkript bleibt in DB bis externer Datenschutz-Prozess deleted_at-Zeilen bereinigt.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(Recording.id == rec_id, Recording.deleted_at.is_(None))
        )
        rec = result.scalar_one_or_none()
        if not rec:
            raise HTTPException(status_code=404, detail="Recording nicht gefunden")

        audio_path = recordings_dir() / rec.filename
        if audio_path.exists():
            audio_path.unlink(missing_ok=True)
            logger.info("Audiodatei gelöscht: %s", rec.filename)

        rec.deleted_at = datetime.now(timezone.utc)
        await session.commit()


@router.get("/{rec_id}/download")
async def download_recording(rec_id: int):
    """Audiodatei herunterladen (nur wenn noch nicht transkriptions-gelöscht)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(Recording.id == rec_id, Recording.deleted_at.is_(None))
        )
        rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording nicht gefunden")

    audio_path = recordings_dir() / rec.filename
    if not audio_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Audiodatei nicht mehr vorhanden (nach Transkription gelöscht)",
        )

    label = rec.label or f"aufnahme-{rec_id}"
    suffix = Path(rec.filename).suffix
    return FileResponse(
        path=str(audio_path),
        filename=f"{label}{suffix}",
        media_type="audio/webm",
    )
