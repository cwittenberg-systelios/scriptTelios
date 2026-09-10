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
# Referenzen auf Fire-and-forget-Tasks halten, sonst kann der Event-Loop sie
# vor Abschluss einsammeln (RUF006; gleicher Fund wie job_queue._spawn_db_task).
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro, what: str) -> None:
    """Startet einen Fire-and-forget-Task mit gehaltener Referenz und loggt
    Fehler statt sie stumm zu verlieren."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _done(t: asyncio.Task, _what=what) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning("Hintergrund-Task %s fehlgeschlagen: %s", _what, exc)

    task.add_done_callback(_done)


async def reprioritize_recording(rec_id: int, audio_path: Path) -> None:
    """Stellt eine Aufnahme mit höchster Priorität erneut in die Queue.
    Aufgerufen von jobs.py wenn p0_recording_id übergeben wird aber
    das Transkript noch fehlt.
    """
    await p0_queue.put((_PRIO_URGENT, rec_id, audio_path))
    logger.info("Recording %d mit Priorität %d neu in Queue", rec_id, _PRIO_URGENT)


async def wait_for_transcript(rec_id: int, timeout_s: int = 600) -> Optional[str]:
    """Wartet bis das Transkript für rec_id verfügbar ist (max. timeout_s Sekunden).
    Gibt das Transkript zurück oder None bei Timeout/Fehler.
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


async def _wait_for_ollama_ready(timeout_s: float = 180.0) -> bool:
    """Wartet bis Ollama warm ist (Modell resident im VRAM).

    Hintergrund: Beim Server-Cold-Start laedt Ollama Qwen3:32b (~20 GB) im
    Hintergrund in den VRAM. Waehrend dieses Ladens ist die VRAM-Allokation
    transient instabil. Wenn pyannote + Whisper in diesem Fenster parallel
    allokieren, gibt es CUDA-OOM - auch wenn Steady-State (26/32 GB) passt.

    Ein 1-Token-Ping antwortet bei warmem Modell in <1s, bei kaltem in 60-90s.
    Wir pollen mit kurzem Timeout pro Versuch.
    """
    import time
    import httpx
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.post(
                    f"{settings.OLLAMA_HOST}/api/generate",
                    json={
                        "model":      settings.OLLAMA_MODEL,
                        "prompt":     "/no_think",
                        "stream":     False,
                        "keep_alive": -1,
                        "options":    {"num_predict": 1},
                    },
                )
            if r.status_code == 200:
                elapsed = time.monotonic() - t0
                if attempt > 1:
                    logger.info("Ollama warm nach %d Versuchen (%.1fs)", attempt, elapsed)
                return True
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.info("Ollama Cold-Start (Versuch %d, %s) - warte 3s", attempt, type(e).__name__)
        except Exception as e:
            logger.warning("Ollama-Ping unerwarteter Fehler (%s) - warte 3s", e)
        await asyncio.sleep(3)
    logger.warning("Ollama nicht innerhalb %.0fs warm geworden - fahre trotzdem fort", timeout_s)
    return False


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


class RecordingPatch(BaseModel):
    label: Optional[str] = None


class RecordingOut(BaseModel):
    id: int
    label: Optional[str] = None
    duration_s: Optional[float] = None
    transcript: Optional[str] = None
    status: str
    error_msg: Optional[str] = None
    created_at: str
    has_audio: bool = False  # v18: zeigt ob Audio noch auf Disk liegt
    # v19.16 (T4): Sekunden am Aufnahme-Ende, die das Transkript vermutlich
    # NICHT abdeckt (None = vollstaendig). Grundlage fuer P0-Badge + QC.
    coverage_gap_s: Optional[float] = None

    model_config = {"from_attributes": True}


async def _set_status(
    rec_id: int,
    status: str,
    error_msg: Optional[str] = None,
    transcript: Optional[str] = None,
    duration_s: Optional[float] = None,
    coverage_gap_s: Optional[float] = None,
):
    async with async_session_factory() as session:
        values = {"status": status, "error_msg": error_msg}
        if transcript is not None:
            values["transcript"] = transcript
        if duration_s is not None:
            values["duration_s"] = duration_s
        if coverage_gap_s is not None:
            values["coverage_gap_s"] = coverage_gap_s
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
        # Cold-Start-Schutz: warten bis Ollama warm ist. Verhindert CUDA-OOM
        # wenn Whisper+pyannote VRAM allokieren waehrend Qwen3:32b noch laedt.
        # Bei warmem Ollama (Normalfall) kostet das ~0.3s, beim Cold-Start nach
        # Server-Boot bis zu 90s. Siehe _wait_for_ollama_ready() fuer Details.
        await _wait_for_ollama_ready()
        # transcribe_audio ist async und delegiert die CPU-lastige Whisper-
        # Arbeit selbst per run_in_executor in einen Thread - kein zusaetzliches
        # Wrapping noetig. Frueher: loop.run_in_executor(None, lambda:
        # asyncio.run(transcribe_audio(...))). Das erzeugte einen Thread mit
        # eigenem Event-Loop, der intern dann nochmal einen Executor-Thread
        # spawnte - reine Verschwendung und potenzielle Loop-Konflikte.
        result = await _transcription.transcribe_audio(audio_path)
        # v19.16 (T4): Coverage-Check - endet das Transkript deutlich vor dem
        # Audio-Ende, wird die Luecke am Recording vermerkt (P0-Badge + QC).
        # Schwelle konservativ: VAD schneidet End-Stille legitim weg.
        _dur = float(result.get("duration_seconds") or 0.0)
        _until = float(result.get("transcribed_until_s") or 0.0)
        _gap_threshold = float(getattr(settings, "WHISPER_COVERAGE_WARN_GAP_S", 30.0))
        _gap = round(_dur - _until, 1) if (_dur > 0 and _until > 0) else 0.0
        _coverage_gap = _gap if _gap > _gap_threshold else None
        if _coverage_gap:
            logger.warning(
                "Recording %d: Transkript endet %.0fs vor Audio-Ende "
                "(%.0fs von %.0fs abgedeckt) - Coverage-Warnung gesetzt.",
                rec_id, _coverage_gap, _until, _dur,
            )
        await _set_status(
            rec_id,
            status="ready",
            transcript=result["transcript"],
            duration_s=result.get("duration_seconds"),
            coverage_gap_s=_coverage_gap,
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
        coverage_gap_s=getattr(r, "coverage_gap_s", None),
    )


def _assert_owner(rec: Recording, therapeut_id: str) -> None:
    """Wirft 403 wenn das Recording einem anderen Therapeuten gehört.
    Aufnahmen ohne therapeut_id (vor v18 angelegt) sind für alle sichtbar.
    """
    if rec.therapeut_id and rec.therapeut_id != therapeut_id:
        raise HTTPException(status_code=403, detail="Zugriff verweigert")


async def _load_owned_recording(session, rec_id: int, therapeut_id: str) -> Recording:
    """Laedt ein nicht geloeschtes Recording aus der uebergebenen Session,
    404 wenn unbekannt, 403 wenn es einem anderen Therapeuten gehoert.
    v19.21 (S4): ersetzt sechs identische Query+404+Owner-Bloecke in den
    Endpunkten; Aufrufer, die schreiben, bleiben in derselben Session."""
    result = await session.execute(
        select(Recording)
        .where(Recording.id == rec_id, Recording.deleted_at.is_(None))
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording nicht gefunden")
    _assert_owner(rec, therapeut_id)
    return rec


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("", response_model=RecordingOut, status_code=201)
async def upload_recording(
    audio: UploadFile = File(...),
    label: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
):
    """Nimmt Audiodatei entgegen, speichert sie persistent und startet
    Transkription asynchron. Antwortet sofort (status=uploading).
    Audio bleibt 24h auf Disk (retention.py löscht danach automatisch).
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
            therapeut_id=current_user,
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
                out.id, current_user, p0_queue.qsize())
    # v19.20 (D1): Dauer sofort im Hintergrund bestimmen - nicht erst mit dem
    # Transkriptionsergebnis. Damit (a) zeigt P0 die Dauer schon waehrend
    # 'Transkribiert...', (b) greift der dynamische Transkript-Timeout
    # (v19.16 G3, max(600, 2xDauer+120)) auch bei frisch hochgeladenen
    # Aufnahmen statt auf 900 s zurueckzufallen (Fall 11.08.: 62-min-Aufnahme,
    # Job nach 15 min abgebrochen). Fire-and-forget: Fehler sind unkritisch.
    _spawn_background(_set_duration_early(out.id, audio_path), f"set_duration_early({out.id})")
    return out


async def _set_duration_early(rec_id: int, audio_path: Path) -> None:
    """v19.20 (D1): duration_s per ffprobe/ffmpeg ermitteln und speichern,
    sofern noch nicht gesetzt (Transkriptionsergebnis ueberschreibt spaeter
    mit demselben Wert). Bei Browser-webm ohne Header dekodiert _get_duration
    die Datei komplett (~10 s bei 60 min) - deshalb im Thread, nicht im
    Request."""
    try:
        from app.services import transcription as _transcription
        duration = await asyncio.to_thread(_transcription._get_duration, audio_path)
        if not duration or duration <= 1.0:
            return
        async with async_session_factory() as session:
            rec = (await session.execute(
                select(Recording).where(Recording.id == rec_id)
            )).scalar_one_or_none()
            if rec is None or rec.duration_s:
                return
            rec.duration_s = round(float(duration), 1)
            await session.commit()
        logger.info("Recording %d: Dauer vorab bestimmt: %.0fs", rec_id, duration)
    except Exception as e:  # noqa: BLE001
        logger.debug("Recording %d: Vorab-Dauer fehlgeschlagen: %s", rec_id, e)


@router.get("", response_model=list[RecordingOut])
async def list_recordings(current_user: str = Depends(get_current_user)):
    """Eigene nicht-gelöschte Aufnahmen, neueste zuerst (max. 50).
    Aufnahmen ohne therapeut_id (vor v18) werden ebenfalls angezeigt.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Recording)
            .where(
                Recording.deleted_at.is_(None),
                Recording.therapeut_id == current_user,
            )
            .order_by(Recording.created_at.desc())
            .limit(50)
        )
        rows = result.scalars().all()
    return [_rec_to_out(r) for r in rows]


@router.patch("/{rec_id}", response_model=RecordingOut)
async def update_recording(
    rec_id: int,
    body: RecordingPatch,
    current_user: str = Depends(get_current_user),
):
    """Label einer Aufnahme nachträglich ändern."""
    async with async_session_factory() as session:
        rec = await _load_owned_recording(session, rec_id, current_user)
        rec.label = body.label.strip()[:120] if body.label and body.label.strip() else None
        await session.commit()
        await session.refresh(rec)
        return _rec_to_out(rec)


@router.get("/{rec_id}", response_model=RecordingOut)
async def get_recording(
    rec_id: int,
    current_user: str = Depends(get_current_user),
):
    async with async_session_factory() as session:
        rec = await _load_owned_recording(session, rec_id, current_user)
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
        rec = await _load_owned_recording(session, rec_id, current_user)

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
        rec = await _load_owned_recording(session, rec_id, current_user)

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


@router.post("/{rec_id}/retry", response_model=RecordingOut)
async def retry_recording(
    rec_id: int,
    current_user: str = Depends(get_current_user),
):
    """Stellt eine gescheiterte Aufnahme erneut in die Transkriptions-Queue.

    Voraussetzungen:
    - Recording im Status "error"
    - Audio-Datei noch auf Disk (24h-Retention nicht abgelaufen)

    Wird typischerweise nach Cold-Start-OOM aufgerufen wenn die GPU
    inzwischen wieder Kapazitaet hat.
    """
    async with async_session_factory() as session:
        rec = await _load_owned_recording(session, rec_id, current_user)

        if rec.status != "error":
            raise HTTPException(
                status_code=409,
                detail=f"Retry nur bei status=error moeglich (aktuell: {rec.status})",
            )

        audio_path = recordings_dir() / rec.filename
        if not audio_path.exists():
            raise HTTPException(
                status_code=410,
                detail="Audiodatei nicht mehr vorhanden (nach 24h gelöscht). "
                       "Retry nicht möglich.",
            )

        # Status zuruecksetzen, error_msg loeschen
        rec.status = "uploading"
        rec.error_msg = None
        await session.commit()
        await session.refresh(rec)
        out = _rec_to_out(rec)

    # Mit hoeherer Prioritaet einreihen damit der Therapeut die Retry-Wirkung
    # schnell sieht (statt hinter eventuellen Neu-Uploads zu warten)
    await p0_queue.put((_PRIO_URGENT, rec_id, audio_path))
    logger.info("Recording %d (Therapeut: %s) RETRY in P0-Queue (Größe: %d)",
                rec_id, current_user, p0_queue.qsize())
    return out


@router.get("/{rec_id}/transcript")
async def download_transcript(
    rec_id: int,
    current_user: str = Depends(get_current_user),
):
    """Transkript als .txt herunterladen.
    Auch dann verfügbar wenn Audio bereits gelöscht wurde.
    """
    async with async_session_factory() as session:
        rec = await _load_owned_recording(session, rec_id, current_user)

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
