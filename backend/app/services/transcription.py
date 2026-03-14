"""
Transkriptions-Service.

Ausschliesslich faster-whisper (lokal, On-Premise).
Kein externer API-Aufruf – Audiodateien verlassen nie den Server.
Nach der Transkription wird die Audiodatei geloescht (Datenschutz).
"""
import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


async def transcribe_audio(file_path: Path) -> dict:
    """
    Transkribiert eine Audiodatei ausschliesslich lokal via faster-whisper.

    Gibt zurueck:
      transcript       – erkannter Text
      language         – erkannte Sprache
      duration_seconds – Laenge der Audiodatei
      word_count       – Anzahl Woerter im Transkript
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper nicht installiert. "
            "Bitte 'pip install faster-whisper' ausfuehren."
        )

    result = await _transcribe_local(file_path)

    # Audiodatei nach Transkription loeschen (Datenschutz / DSGVO)
    if settings.DELETE_AUDIO_AFTER_TRANSCRIPTION and file_path.exists():
        file_path.unlink()
        logger.info("Audiodatei geloescht nach Transkription: %s", file_path.name)

    return result


async def _transcribe_local(file_path: Path) -> dict:
    """faster-whisper lokal (CPU oder CUDA, kein externer Aufruf)."""
    import asyncio
    from faster_whisper import WhisperModel

    def _run() -> dict:
        model = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
        )
        segments, info = model.transcribe(
            str(file_path),
            language="de",
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(s.text.strip() for s in segments)
        return {
            "transcript": text.strip(),
            "language": info.language,
            "duration_seconds": info.duration,
            "word_count": len(text.split()),
        }

    # Im Thread-Pool ausfuehren um Event-Loop nicht zu blockieren
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)
