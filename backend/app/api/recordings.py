"""
backend/app/api/recordings.py

P0-Aufnahmen: Upload → sofortige Transkription im Hintergrund → Abruf in P1–P4.

v18 Änderungen:
- therapeut_id Pflichtfeld beim Upload (aus Auth-Header, wie jobs.py)
- List/Delete/Download/Transkript-Download nur für eigene Aufnahmen
- Audio-Datei wird NICHT mehr sofort nach Transkription gelöscht:
  DELETE_AUDIO_AFTER_TRANSCRIPTION=False, stattdessen 24h-Cleanup in retention.py
- Neuer Endpoint GET /{rec_id}/transcript → .txt Download
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select, update

from app.core.auth import get_current_user
from app.core.database import async_session_factory
from app.core.files import recordings_dir, ALLOWED_AUDIO
from app.core.config import settings
from app.models.db import Recording

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recordings", tags=["Aufnahmen"])

_PRIO_P0 = 10
_PRIO_URGENT = 1  # Wenn Nutzer eine noch-transcribierende Aufnahme auswählt
p0_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()


async def reprioritize_recording(rec_id: int, audio_path: Path) -> None:
    """Stellt eine Aufnahme mit höchster Priorität erneut in die Queue.
    Wird von jobs.py aufgerufen wenn p0_recording_id übergeben wird aber
    das Transkript noch fehlt. Der p0_worker verarbeitet sie als nächstes.
    """
    await p0_queue.put((_PRIO_URGENT, rec_id, audio_path))
    logger.info("Recording %d mit Priorität %d neu in Queue gestellt", rec_id, _PRIO_URGENT)


async def wait_for_transcript(rec_id: int, timeout_s: int = 600) -> Optional[str]:
    """Wartet bis das Transkript für rec_id verfügbar ist (max. timeout_s Sekunden).
    Gibt das Transkript zurück oder None bei Timeout/Fehler.
    Wird von jobs.py aufgerufen wenn transcript-Feld fehlt aber p0_recording_id gesetzt.
    """
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Recording).where(Recording.id == rec_id)
            )
            rec = result.scalar_one_or_none()
        if not rec:
            return None
        if rec.status == "ready" and rec.transcript:
            return rec.transcript
        if rec.status == "error":
            logger.warning("Recording %d Transkription fehlgeschlagen — kann nicht auf Transkript warten", rec_id)
            return None
        await asyncio.sleep(5)
    logger.warning("wait_for_transcript: Timeout nach %ds für Recording %d", timeout_s, rec_id)
    return None


async def _active_job_running() -> bool:
    from sqlalchemy import func
    from app.models.db import Job
    async with async_session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(Job).where(Job.status == "running")
        )
        return (result.scalar() or 0) > 0


async def p0_worker():
    logger.info("P0-Worker gestartet")
    while True:
        try:
            prio, rec_id, audio_path = await p0_queue.get()
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
            await asyncio.sleep(10)


class RecordingOut(BaseModel):
    id: int
    label: Optional[str] = None
    duration_s: Optional[float] = None
    transcript: Optional[str] = None
    status: str
    error_msg: Optional[str] = None
    created_at: str
    has_audio: bool = False  # v18: zeigt ob Audio noch auf Disk liegt

    model_config = {"from_attributes": True}


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
    """Transkribiert im Hintergrund und aktualisiert DB-Status.

    v18: Audio wird NICHT mehr nach Transkription gelöscht.
    Stattdessen löscht retention.py Audiodateien nach 24h.
    Das gibt dem Therapeuten Zeit das Audio zu prüfen/herunterladen.
    """
    from app.services import transcription as _transcription
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
        # v18: Audio bleibt 24h erhalten (retention.py cleanup_recordings_audio
        # löscht Dateien älter als 24h). Kein sofortiges DELETE mehr.
    except Exception as e:
        logger.exception("Transkription Recording %d fehlgeschlagen", rec_id)
        await _set_status(rec_id, "error", error_msg=str(e)[:500])


def _rec_to_out(r: Recording) -> RecordingOut:
    audio_path = recordings_dir() / r.filename
    return RecordingOut(
        id=r.id,
        label=r.label,
        duration_s=r.duration_s,
        transcript=r.transcript,
        status=r.status,
        error_msg=r.error_msg,
        created_at=r.created_at.isoformat(),
        has_audio=audio_path.exists(),
    )


def _assert_owner(rec: Recording, therapeut_id: str) -> None:
    """Wirft 403 wenn das Recording einem anderen Therapeuten gehört.
    Aufnahmen ohne therapeut_id (vor v18 angelegt) sind für alle sichtbar.
    """
    if rec.therapeut_id and rec.therapeut_id != therapeut_id:
        raise HTTPException(status_code=403, detail="Zugriff verweigert")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("", response_model=RecordingOut, status_code=201)
async def upload_recording(
    audio: UploadFile = File(...),
    label: Optional[str] = Form(None),
    therapeut_id: Optional[str] = None,
    current_user: str = Depends(get_current_user),
):
    """Nimmt Audiodatei entgegen, speichert sie persistent und startet
    Transkription asynchron. Antwortet sofort (status=uploading).
    Audio bleibt 24h auf Disk (retention.py löscht danach automatisch).
    Query-Parameter therapeut_id überschreibt current_user wenn AUTH_ENABLED=False.
    """
    effective_user = therapeut_id.strip() if (therapeut_id and therapeut_id.strip()) else current_user
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
            therapeut_id=effective_user,
            label=label.strip()[:120] if label and label.strip() else None,
            filename=filename,
            status="uploading",
        )
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        out = _rec_to_out(rec)

    await p0_queue.put((_PRIO_P0, out.id, audio_path))
    logger.info("Recording %d (Therapeut: %s) in P0-Queue (Größe: %d)",
                out.id, effective_user, p0_queue.qsize())
    return out


@router.get("", response_model=list[RecordingOut])
async def list_recordings(
    current_user: str = Depends(get_current_user),
    therapeut_id: Optional[str] = None,
):
    """Eigene nicht-gelöschte Aufnahmen, neueste zuerst (max. 50).
    Aufnahmen ohne therapeut_id (vor v18) werden ebenfalls angezeigt.
    Query-Parameter therapeut_id überschreibt current_user wenn AUTH_ENABLED=False.
    """
    effective_user = therapeut_id.strip() if (therapeut_id and therapeut_id.strip()) else current_user
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(
                Recording.deleted_at.is_(None),
                # Eigene ODER alte (ohne therapeut_id)
                (Recording.therapeut_id == effective_user) | Recording.therapeut_id.is_(None),
            )
            .order_by(Recording.created_at.desc())
            .limit(50)
        )
        rows = result.scalars().all()
    return [_rec_to_out(r) for r in rows]


@router.get("/{rec_id}", response_model=RecordingOut)
async def get_recording(
    rec_id: int,
    current_user: str = Depends(get_current_user),
):
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(Recording.id == rec_id, Recording.deleted_at.is_(None))
        )
        rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording nicht gefunden")
    _assert_owner(rec, current_user)
    return _rec_to_out(rec)


@router.delete("/{rec_id}", status_code=204)
async def delete_recording(
    rec_id: int,
    current_user: str = Depends(get_current_user),
):
    """Soft-Delete + physische Audiodatei-Löschung.
    Transkript bleibt in DB bis deleted_at-Bereinigung.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(Recording.id == rec_id, Recording.deleted_at.is_(None))
        )
        rec = result.scalar_one_or_none()
        if not rec:
            raise HTTPException(status_code=404, detail="Recording nicht gefunden")
        _assert_owner(rec, current_user)

        audio_path = recordings_dir() / rec.filename
        if audio_path.exists():
            audio_path.unlink(missing_ok=True)
            logger.info("Audiodatei gelöscht: %s", rec.filename)

        rec.deleted_at = datetime.now(timezone.utc)
        await session.commit()


@router.get("/{rec_id}/download")
async def download_recording(
    rec_id: int,
    current_user: str = Depends(get_current_user),
):
    """Audiodatei herunterladen. Audio wird 24h nach Transkription aufbewahrt."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(Recording.id == rec_id, Recording.deleted_at.is_(None))
        )
        rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording nicht gefunden")
    _assert_owner(rec, current_user)

    audio_path = recordings_dir() / rec.filename
    if not audio_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Audiodatei nicht mehr vorhanden (nach 24h automatisch gelöscht). "
                   "Das Transkript ist weiterhin verfügbar.",
        )

    label = rec.label or f"aufnahme-{rec_id}"
    suffix = Path(rec.filename).suffix
    return FileResponse(
        path=str(audio_path),
        filename=f"{label}{suffix}",
        media_type="audio/webm",
    )


@router.get("/{rec_id}/transcript")
async def download_transcript(
    rec_id: int,
    current_user: str = Depends(get_current_user),
):
    """Transkript als .txt herunterladen.
    Auch dann verfügbar wenn Audio bereits gelöscht wurde.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(Recording.id == rec_id, Recording.deleted_at.is_(None))
        )
        rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording nicht gefunden")
    _assert_owner(rec, current_user)

    if not rec.transcript:
        raise HTTPException(
            status_code=404,
            detail="Kein Transkript vorhanden (Transkription noch nicht abgeschlossen oder fehlgeschlagen).",
        )

    label = rec.label or f"aufnahme-{rec_id}"
    # Dateiname sauber machen
    safe_label = "".join(c if c.isalnum() or c in "-_ " else "_" for c in label).strip()
    filename = f"transkript_{safe_label}.txt".replace(" ", "_")

    return PlainTextResponse(
        content=rec.transcript,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
