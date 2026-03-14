"""
Datei-Handling: Upload-Validierung, Speicherung, Bereinigung.
"""
import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_AUDIO = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".webm"}
ALLOWED_DOCS  = {".pdf", ".docx", ".doc", ".txt"}
ALLOWED_IMAGES = {".jpg", ".jpeg", ".png", ".tiff", ".bmp"}
ALLOWED_ALL   = ALLOWED_AUDIO | ALLOWED_DOCS | ALLOWED_IMAGES


def upload_dir() -> Path:
    p = Path(settings.UPLOAD_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def output_dir() -> Path:
    p = Path(settings.OUTPUT_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


async def save_upload(
    file: UploadFile,
    allowed_extensions: set[str] | None = None,
) -> Path:
    """
    Validiert und speichert eine hochgeladene Datei.
    Gibt den Pfad zur gespeicherten Datei zurueck.
    """
    if allowed_extensions is None:
        allowed_extensions = ALLOWED_ALL

    # Dateiendung pruefen
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=422,
            detail=f"Dateiformat '{suffix}' nicht unterstuetzt. "
                   f"Erlaubt: {', '.join(sorted(allowed_extensions))}",
        )

    # Dateigroesse pruefen
    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.MAX_UPLOAD_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Datei zu gross ({size_mb:.1f} MB). "
                   f"Maximum: {settings.MAX_UPLOAD_MB} MB",
        )

    # Sicheren Dateinamen vergeben (nie den Original-Dateinamen verwenden)
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    dest = upload_dir() / safe_name

    dest.write_bytes(content)
    logger.info(
        "Datei gespeichert: %s (%s, %.2f MB)",
        safe_name,
        file.content_type,
        size_mb,
    )
    return dest


def file_checksum(path: Path) -> str:
    """SHA-256-Pruefsumme einer Datei (fuer Audit-Log)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_delete(path: Path) -> None:
    """Loescht eine Datei, ignoriert Fehler."""
    try:
        path.unlink(missing_ok=True)
        logger.debug("Geloescht: %s", path.name)
    except OSError as e:
        logger.warning("Konnte nicht loeschen %s: %s", path.name, e)
