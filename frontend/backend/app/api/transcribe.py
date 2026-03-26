"""
POST /api/transcribe  – Audio-Datei transkribieren.

Akzeptiert:  multipart/form-data  (Feld: file)
Gibt zurueck: TranscribeResponse (job_id, transcript, language, ...)
"""
import logging
import uuid

from fastapi import APIRouter, UploadFile, File, HTTPException

from app.core.files import save_upload, ALLOWED_AUDIO
from app.models.schemas import TranscribeResponse
import app.services.transcription as _transcription

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(file: UploadFile = File(..., description="Audio-Datei (.mp3, .m4a, .wav ...)")):
    """
    Transkribiert eine Audio-Aufnahme mit faster-whisper (lokal) oder
    OpenAI Whisper API (Testphase).

    Audiodateien werden nach der Transkription automatisch geloescht
    (konfigurierbar via DELETE_AUDIO_AFTER_TRANSCRIPTION).
    """
    file_path = await save_upload(file, allowed_extensions=ALLOWED_AUDIO)

    try:
        result = await _transcription.transcribe_audio(file_path)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    job_id = uuid.uuid4().hex

    logger.info(
        "Transkription [%s]: %d Woerter, Sprache=%s, Dauer=%.1fs",
        job_id,
        result["word_count"],
        result["language"],
        result["duration_s"],
    )

    return TranscribeResponse(
        job_id=job_id,
        transcript=result["transcript"],
        language=result["language"],
        duration_seconds=result["duration_s"],
        word_count=result["word_count"],
    )
