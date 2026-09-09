"""
E2 — Löschkonzept / Retention Policy

Definiert maximale Aufbewahrungszeiten pro Datenkategorie und stellt
einen periodischen Cleanup-Task bereit. Wird beim Backend-Start
parallel zum bestehenden Upload-Cleanup gestartet.

Alle Werte konfigurierbar via Environment-Variablen oder Settings.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Retention-Policy ─────────────────────────────────────────────────────────
# Werte in Sekunden. Default-Werte sind konservativ auf Patientenschutz
# ausgelegt — können via Settings überschrieben werden.
RETENTION = {
    "uploads_audio":          24 * 3600,           # 24h
    "uploads_documents":      24 * 3600,           # 24h
    "recordings_audio":       24 * 3600,           # 24h — P0-Audiodateien
    "jobs_done":               1 * 3600,           # 1h nach Abschluss
    "jobs_error":             24 * 3600,           # 24h zur Fehleranalyse
    "style_embeddings":  365 * 24 * 3600,          # 1 Jahr Inaktivität
    "audit_log":          90 * 24 * 3600,          # 90 Tage
    "performance_log":   180 * 24 * 3600,          # 180 Tage
    # §6a Einwilligung v1.1 (Qualitaetssicherung Einfuehrungsphase):
    # Verarbeitungsdaten mit Patienteninhalten (Jobs inkl. Transkripten/
    # Entwuerfen, Recording-Zeilen inkl. DB-Transkript) werden hoechstens
    # 90 Tage vorgehalten, danach geloescht. Frist NICHT verlaengern ohne
    # Anpassung der Einwilligungserklaerung.
    "qa_artifacts":       90 * 24 * 3600,          # 90 Tage
}


async def cleanup_recordings_audio() -> int:
    """
    Löscht Audiodateien aus recordings_dir die älter als 24h sind.
    Das Transkript bleibt in der DB erhalten — nur die Audiodatei wird entfernt.

    v18: DELETE_AUDIO_AFTER_TRANSCRIPTION ist jetzt False. Stattdessen läuft
    dieser Cleanup alle 6h und entfernt Audio nach 24h. Das gibt dem Therapeuten
    Zeit das Audio zu prüfen oder herunterzuladen, bevor es gelöscht wird.
    Die DB wird ebenfalls informiert — hat_audio-Flag in RecordingOut.

    Aufnahmen die bereits per DELETE-Endpoint gelöscht wurden (deleted_at gesetzt)
    oder deren Datei nicht mehr existiert werden übersprungen.
    """
    from app.core.files import recordings_dir as _recordings_dir
    from app.core.database import async_session_factory
    from app.models.db import Recording
    from sqlalchemy import select

    from datetime import timezone as _tz
    cutoff = datetime.now(tz=_tz.utc) - timedelta(seconds=RETENTION["recordings_audio"])
    count = 0
    try:
        rec_dir = _recordings_dir()
        if not rec_dir.exists():
            return 0

        # Kandidaten aus DB: nicht soft-gelöscht, created_at älter als cutoff.
        # st_mtime wird NICHT verwendet — auf RunPod-Volumes nach rsync/Remount
        # unzuverlässig (mtime wird auf Zeitpunkt des Mounts gesetzt).
        async with async_session_factory() as db:
            result = await db.execute(
                select(Recording.filename)
                .where(
                    Recording.deleted_at.is_(None),
                    Recording.created_at < cutoff,
                )
            )
            old_filenames = [row.filename for row in result]

        for filename in old_filenames:
            f_path = rec_dir / filename
            if not f_path.exists():
                continue
            try:
                f_path.unlink(missing_ok=True)
                count += 1
            except Exception as e:
                logger.warning("recordings_audio Cleanup Fehler %s: %s", filename, e)

        if count:
            logger.info(
                "Retention recordings_audio: %d Audiodateien älter als %dh gelöscht",
                count, RETENTION["recordings_audio"] // 3600,
            )
    except Exception as e:
        logger.warning("recordings_audio Cleanup fehlgeschlagen: %s", e)
    return count


async def cleanup_uploads() -> int:
    """Löscht alte Dateien aus dem Upload-Verzeichnis."""
    from app.core.files import upload_dir
    cutoff = time.time() - RETENTION["uploads_documents"]
    count = 0
    try:
        for f in upload_dir().iterdir():
            if not f.is_file(): continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    count += 1
            except Exception as e:
                logger.warning("Cleanup-Fehler %s: %s", f.name, e)
    except Exception as e:
        logger.warning("Upload-Cleanup fehlgeschlagen: %s", e)
    if count:
        logger.info("Retention: %d Upload-Dateien gelöscht (>%dh)",
                    count, RETENTION["uploads_documents"] // 3600)
    return count


async def cleanup_inactive_style_embeddings() -> int:
    """Löscht Stil-Embeddings deren letztes Update länger her ist als Retention."""
    from app.core.database import async_session_factory
    from app.models.db import StyleEmbedding
    from sqlalchemy import delete
    from datetime import timezone as _tz
    cutoff = datetime.now(tz=_tz.utc) - timedelta(seconds=RETENTION["style_embeddings"])
    count = 0
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                delete(StyleEmbedding).where(StyleEmbedding.updated_at < cutoff)
            )
            count = result.rowcount or 0
            await db.commit()
    except Exception as e:
        logger.warning("Style-Cleanup fehlgeschlagen: %s", e)
    if count:
        logger.info("Retention: %d inaktive Stilvorlagen gelöscht", count)
    return count


def _ts_to_epoch(ts) -> Optional[float]:
    """Konvertiert einen Log-Timestamp in Unix-Epoch oder None.

    Akzeptiert beide in unseren Logs vorkommenden Formate:
      - int/float  -> Unix-Epoch (z.B. audit.py schreibt int(time.time()))
      - ISO 8601   -> Strings wie '2026-05-15T13:42:01+00:00' oder
                       '2026-05-15T13:42:01Z' (z.B. job_queue.py via
                       datetime.isoformat())

    Hintergrund: Frueher griff nur der int/float-Pfad. ISO-Strings fielen
    in den else-Zweig der Hauptschleife und wurden unabhaengig vom
    tatsaechlichen Alter geloescht — performance.log war faktisch nicht
    retentiert. Dieses Modul-Level-Helper macht beide Pfade gleichwertig.

    Returns None fuer:
      - leeren String oder None
      - unparsbare Strings ('not-a-date')
      - unbekannte Typen (bool, dict, list)
    """
    if isinstance(ts, bool):
        # bool ist int-Subklasse - explizit ausschliessen, sonst wird True=1
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts:
        try:
            # fromisoformat akzeptiert auch +HH:MM Offsets ab Python 3.11
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


async def cleanup_old_logs() -> int:
    """Trunkiert performance.log und audit.log jenseits der Retention."""
    count = 0
    for log_path, max_age in [
        (settings.AUDIT_LOG_PATH, RETENTION["audit_log"]),
        ("/workspace/performance.log", RETENTION["performance_log"]),
    ]:
        try:
            p = Path(log_path)
            if not p.exists(): continue
            cutoff_ts = time.time() - max_age
            lines = p.read_text(encoding="utf-8").splitlines()
            kept = []
            removed = 0
            import json as _j

            for line in lines:
                try:
                    e = _j.loads(line)
                    ts_raw = e.get("ts") or e.get("timestamp")
                    if not ts_raw:
                        kept.append(line)  # Zeilen ohne Timestamp behalten
                        continue
                    ts_epoch = _ts_to_epoch(ts_raw)
                    if ts_epoch is None:
                        # unbekanntes Format -> sicherheitshalber behalten
                        kept.append(line)
                    elif ts_epoch >= cutoff_ts:
                        kept.append(line)
                    else:
                        removed += 1
                except Exception:
                    kept.append(line)
            if removed:
                p.write_text("\n".join(kept) + "\n", encoding="utf-8")
                count += removed
                logger.info("Retention: %d Zeilen aus %s gelöscht",
                            removed, p.name)
        except Exception as e:
            logger.warning("Log-Cleanup %s: %s", log_path, e)
    return count


async def cleanup_feedback_cases() -> int:
    """v19.19 (R5): feedback_cases/*.log aelter als 90 Tage loeschen
    (gleiche Frist wie feedback.log, §6a Einwilligung)."""
    import os as _os
    log_dir = Path(_os.environ.get("LOG_FILE", "/workspace/systelios.log")).parent
    case_dir = log_dir / "feedback_cases"
    if not case_dir.exists():
        return 0
    cutoff = time.time() - 90 * 86400
    count = 0
    for f in case_dir.glob("*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                count += 1
        except Exception as e:
            logger.warning("feedback_cases-Cleanup %s: %s", f.name, e)
    if count:
        logger.info("Retention: %d Feedback-Fallkopien geloescht", count)
    return count


async def cleanup_jobs_db() -> int:
    """§6a (Einwilligung v1.1): Löscht Job-Zeilen älter als RETENTION['qa_artifacts'].

    Betrifft alle Inhalts-Spalten (result_text, result_transcript,
    transcript_summary_text, source_*, Telemetrie). ISM-Jobs (P6) sind
    Jobs wie alle anderen und werden mit erfasst. Massgeblich ist
    created_at — finished_at kann bei abgebrochenen Jobs NULL sein.
    """
    from app.core.database import async_session_factory
    from app.models.db import Job
    from sqlalchemy import delete
    from datetime import timezone as _tz
    cutoff = datetime.now(tz=_tz.utc) - timedelta(seconds=RETENTION["qa_artifacts"])
    count = 0
    try:
        async with async_session_factory() as db:
            result = await db.execute(delete(Job).where(Job.created_at < cutoff))
            count = result.rowcount or 0
            await db.commit()
    except Exception as e:
        logger.warning("Jobs-DB-Cleanup fehlgeschlagen: %s", e)
    if count:
        logger.info("Retention §6a: %d Jobs älter als %d Tage gelöscht",
                    count, RETENTION["qa_artifacts"] // 86400)
    return count


async def cleanup_recordings_db() -> int:
    """§6a (Einwilligung v1.1): Löscht Recording-Zeilen älter als RETENTION['qa_artifacts'].

    Anders als cleanup_recordings_audio (entfernt nur die Audiodatei nach
    24h, Transkript bleibt) wird hier die komplette DB-Zeile inklusive
    Transkript hart gelöscht — auch Soft-Deleted-Zeilen (deleted_at
    gesetzt), die bisher unbegrenzt liegen blieben. Etwaige noch
    vorhandene Audiodateien werden vor dem DB-Delete mit entfernt.
    """
    from app.core.files import recordings_dir as _recordings_dir
    from app.core.database import async_session_factory
    from app.models.db import Recording
    from sqlalchemy import select, delete
    from datetime import timezone as _tz
    cutoff = datetime.now(tz=_tz.utc) - timedelta(seconds=RETENTION["qa_artifacts"])
    count = 0
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Recording.filename).where(Recording.created_at < cutoff)
            )
            old_filenames = [row.filename for row in result if row.filename]
            res = await db.execute(delete(Recording).where(Recording.created_at < cutoff))
            count = res.rowcount or 0
            await db.commit()
        # Dateireste erst nach erfolgreichem Commit entfernen — schlaegt das
        # DB-Delete fehl, bleiben Datei und Zeile konsistent beisammen.
        try:
            rec_dir = _recordings_dir()
            for filename in old_filenames:
                try:
                    (rec_dir / filename).unlink(missing_ok=True)
                except Exception as e:
                    logger.warning("recordings_db Datei-Cleanup %s: %s", filename, e)
        except Exception as e:
            logger.warning("recordings_db Verzeichniszugriff: %s", e)
    except Exception as e:
        logger.warning("Recordings-DB-Cleanup fehlgeschlagen: %s", e)
    if count:
        logger.info("Retention §6a: %d Recordings (inkl. Transkript) älter als %d Tage gelöscht",
                    count, RETENTION["qa_artifacts"] // 86400)
    return count


async def retention_task():
    """
    Periodischer Task: läuft alle 6 Stunden alle Cleanup-Funktionen durch.
    Startet beim Backend-Start, läuft im Hintergrund.
    """
    INTERVAL = 6 * 3600  # 6 Stunden
    while True:
        try:
            await cleanup_recordings_audio()
            await cleanup_uploads()
            await cleanup_inactive_style_embeddings()
            await cleanup_old_logs()
            await cleanup_feedback_cases()   # v19.19 (R5)
            await cleanup_jobs_db()
            await cleanup_recordings_db()
        except asyncio.CancelledError:
            logger.info("Retention-Task gestoppt")
            return
        except Exception as e:
            logger.error("Retention-Task Fehler: %s", e)
        try:
            await asyncio.sleep(INTERVAL)
        except asyncio.CancelledError:
            return
