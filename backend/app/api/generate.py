"""
POST /api/generate              – Text generieren (alle 4 Workflows)
POST /api/generate/with-files   – Text generieren inkl. Datei-Uploads

Workflow 1 – Gespraechsdokumentation:  Transkript/Audio + Stichpunkte → Verlaufsnotiz
Workflow 2 – Anamnese & Befund:        Selbstauskunft-PDF + Vorbefunde + Audio → Anamnese + AMDP
Workflow 3 – Verlaengerungsantrag:     (Vorlage + Verlauf → via /api/documents/fill)
Workflow 4 – Entlassbericht:           (Vorlage + Verlauf → via /api/documents/fill)
"""
import logging
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from typing import Annotated, Optional

from app.core.files import save_upload, ALLOWED_DOCS, ALLOWED_IMAGES, ALLOWED_AUDIO
from app.models.schemas import GenerateRequest, GenerateResponse
from app.services.extraction import extract_text, extract_style_context
from app.services.llm import generate_text
from app.services.prompts import build_system_prompt, build_user_content
from app.services.transcription import transcribe_audio

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    """
    Einfache Text-Generierung ohne Datei-Upload.
    Fuer Faelle, in denen Transkript und Texte bereits im Frontend vorliegen.
    """
    system = build_system_prompt(
        workflow=req.workflow,
        custom_prompt=req.prompt,
        style_context=req.style_context,
        diagnosen=req.diagnosen,
    )
    user = build_user_content(
        workflow=req.workflow,
        transcript=req.transcript,
        bullets=req.bullets,
        diagnosen=req.diagnosen,
    )

    try:
        result = await generate_text(system, user)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    job_id = uuid.uuid4().hex
    logger.info("Generierung [%s] Workflow=%s Modell=%s", job_id, req.workflow, result["model_used"])

    return GenerateResponse(
        job_id=job_id,
        text=result["text"],
        model_used=result["model_used"],
        duration_seconds=result["duration_s"],
        token_count=result.get("token_count"),
    )


@router.post("/generate/with-files", response_model=GenerateResponse)
async def generate_with_files(
    workflow:   Annotated[str,  Form()],
    prompt:     Annotated[str,  Form()],
    diagnosen:  Annotated[Optional[str], Form()] = None,   # kommagetrennt
    transcript: Annotated[Optional[str], Form()] = None,
    bullets:    Annotated[Optional[str], Form()] = None,
    audio:      Optional[UploadFile] = File(None),
    selbstauskunft: Optional[UploadFile] = File(None),
    vorbefunde: Optional[UploadFile] = File(None),
    style_file: Optional[UploadFile] = File(None),
):
    """
    Text-Generierung mit optionalen Datei-Uploads.

    Verarbeitungsreihenfolge:
    1. Audio  → Transkription (faster-whisper)
    2. PDFs   → Textextraktion (pdfplumber / OCR)
    3. Stil   → Stilprofil-Extraktion per LLM
    4. Text   → Generierung per LLM
    """
    dx_list = [d.strip() for d in diagnosen.split(",") if d.strip()] if diagnosen else []

    # ── 1. Audio transkribieren ──────────────────────────────────
    audio_transcript = transcript or ""
    if audio and audio.filename:
        logger.info("Audio-Upload: %s", audio.filename)
        audio_path = await save_upload(audio, ALLOWED_AUDIO)
        try:
            tr = await transcribe_audio(audio_path)
            audio_transcript = tr["transcript"]
            logger.info("Transkription: %d Woerter", tr["word_count"])
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=f"Transkriptions-Fehler: {e}")

    # ── 2. Dokumente extrahieren ─────────────────────────────────
    selbstauskunft_text = ""
    if selbstauskunft and selbstauskunft.filename:
        path = await save_upload(selbstauskunft, ALLOWED_DOCS | ALLOWED_IMAGES)
        try:
            selbstauskunft_text = await extract_text(path)
        except Exception as e:
            logger.warning("Selbstauskunft-Extraktion fehlgeschlagen: %s", e)

    vorbefunde_text = ""
    if vorbefunde and vorbefunde.filename:
        path = await save_upload(vorbefunde, ALLOWED_DOCS | ALLOWED_IMAGES)
        try:
            vorbefunde_text = await extract_text(path)
        except Exception as e:
            logger.warning("Vorbefunde-Extraktion fehlgeschlagen: %s", e)

    # ── 3. Stilprofil extrahieren ────────────────────────────────
    style_context = ""
    if style_file and style_file.filename:
        path = await save_upload(style_file, ALLOWED_DOCS)
        try:
            style_context = await extract_style_context(path, generate_text)
            logger.info("Stilprofil extrahiert: %d Zeichen", len(style_context))
        except Exception as e:
            logger.warning("Stilprofil-Extraktion fehlgeschlagen: %s", e)

    # ── 4. Prompts bauen und generieren ─────────────────────────
    system = build_system_prompt(
        workflow=workflow,
        custom_prompt=prompt,
        style_context=style_context,
        diagnosen=dx_list,
    )
    user = build_user_content(
        workflow=workflow,
        transcript=audio_transcript,
        bullets=bullets,
        selbstauskunft_text=selbstauskunft_text,
        vorbefunde_text=vorbefunde_text,
        diagnosen=dx_list,
    )

    try:
        result = await generate_text(system, user)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    job_id = uuid.uuid4().hex
    logger.info(
        "Generierung [%s] Workflow=%s Modell=%s Dauer=%.1fs",
        job_id, workflow, result["model_used"], result["duration_s"],
    )

    return GenerateResponse(
        job_id=job_id,
        text=result["text"],
        model_used=result["model_used"],
        duration_seconds=result["duration_s"],
        token_count=result.get("token_count"),
    )
