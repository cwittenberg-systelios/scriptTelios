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
    "jobs_done":               1 * 3600,           # 1h nach Abschluss
    "jobs_error":             24 * 3600,           # 24h zur Fehleranalyse
    "style_embeddings":  365 * 24 * 3600,          # 1 Jahr Inaktivität
    "audit_log":          90 * 24 * 3600,          # 90 Tage
    "performance_log":   180 * 24 * 3600,          # 180 Tage
}


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
    from sqlalchemy import select, delete
    cutoff = datetime.utcnow() - timedelta(seconds=RETENTION["style_embeddings"])
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
                    ts = e.get("ts") or e.get("timestamp") or 0
                    if isinstance(ts, (int, float)) and ts >= cutoff_ts:
                        kept.append(line)
                    elif not ts:
                        kept.append(line)  # Zeilen ohne Timestamp behalten
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


async def retention_task():
    """
    Periodischer Task: läuft alle 6 Stunden alle Cleanup-Funktionen durch.
    Startet beim Backend-Start, läuft im Hintergrund.
    """
    INTERVAL = 6 * 3600  # 6 Stunden
    while True:
        try:
            await cleanup_uploads()
            await cleanup_inactive_style_embeddings()
            await cleanup_old_logs()
        except asyncio.CancelledError:
            logger.info("Retention-Task gestoppt")
            return
        except Exception as e:
            logger.error("Retention-Task Fehler: %s", e)
        try:
            await asyncio.sleep(INTERVAL)
        except asyncio.CancelledError:
            return
