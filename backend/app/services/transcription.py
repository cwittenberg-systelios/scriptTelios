"""
Transkriptions-Service.

Backends:
  local  – faster-whisper (On-Premise, kein Datentransfer)
  openai – OpenAI Whisper API (nur fuer Testphase mit anonymisierten Daten)
"""
import logging
import os
import time
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# Whisper-Instanz wird beim ersten Aufruf geladen (lazy loading)
_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        logger.info(
            "Lade Whisper-Modell '%s' auf '%s' ...",
            settings.WHISPER_MODEL,
            settings.WHISPER_DEVICE,
        )
        try:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(
                settings.WHISPER_MODEL,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
            )
            logger.info("Whisper-Modell geladen.")
        except ImportError:
            raise RuntimeError(
                "faster-whisper nicht installiert. "
                "Bitte: pip install faster-whisper"
            )
    return _whisper_model


async def transcribe_audio(file_path: Path) -> dict:
    """
    Transkribiert eine Audiodatei.

    Gibt zurueck:
      transcript   – vollstaendiger Text
      language     – erkannte Sprache
      duration_s   – Laenge der Aufnahme in Sekunden
      word_count   – Anzahl Woerter
    """
    t0 = time.time()

    if settings.WHISPER_BACKEND == "openai":
        result = await _transcribe_openai(file_path)
    else:
        result = await _transcribe_local(file_path)

    elapsed = round(time.time() - t0, 1)
    logger.info(
        "Transkription abgeschlossen: %d Woerter in %.1fs (Backend: %s)",
        result["word_count"],
        elapsed,
        settings.WHISPER_BACKEND,
    )

    # Audiodatei loeschen (Datenschutz)
    if settings.DELETE_AUDIO_AFTER_TRANSCRIPTION:
        try:
            file_path.unlink()
            logger.info("Audiodatei geloescht: %s", file_path.name)
        except OSError as e:
            logger.warning("Audiodatei konnte nicht geloescht werden: %s", e)

    return result


async def _transcribe_local(file_path: Path) -> dict:
    """faster-whisper (lokal, On-Premise)."""
    import asyncio

    model = _get_whisper_model()

    def _run():
        segments, info = model.transcribe(
            str(file_path),
            language="de",
            beam_size=5,
            vad_filter=True,           # Stille herausfiltern
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text_parts = []
        for seg in segments:
            text_parts.append(seg.text.strip())

        full_text = " ".join(text_parts)
        return {
            "transcript": full_text,
            "language": info.language,
            "duration_s": round(info.duration, 1),
            "word_count": len(full_text.split()),
        }

    # In Thread-Pool ausfuehren (CPU-intensiv)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


async def _transcribe_openai(file_path: Path) -> dict:
    """OpenAI Whisper API (nur Testphase mit anonymisierten Daten)."""
    if not settings.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY nicht gesetzt. "
            "Bitte in .env eintragen oder WHISPER_BACKEND=local verwenden."
        )

    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(file_path, "rb") as f:
            response = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                data={"model": "whisper-1", "language": "de", "response_format": "verbose_json"},
                files={"file": (file_path.name, f, "audio/mpeg")},
            )

    if response.status_code != 200:
        raise RuntimeError(f"OpenAI Whisper API Fehler: {response.text}")

    data = response.json()
    text = data.get("text", "")
    return {
        "transcript": text,
        "language": data.get("language", "de"),
        "duration_s": round(data.get("duration", 0.0), 1),
        "word_count": len(text.split()),
    }
