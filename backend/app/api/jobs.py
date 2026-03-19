"""
GET  /api/jobs/{job_id}   – Job-Status abfragen
GET  /api/jobs            – Alle Jobs auflisten (optional)
"""
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from typing import Annotated, Literal, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.files import save_upload, ALLOWED_DOCS, ALLOWED_IMAGES, ALLOWED_AUDIO
from app.services.job_queue import job_queue, JobStatus
from app.services.embeddings import retrieve_style_examples
from app.services.extraction import extract_text, extract_style_context
from app.services.llm import generate_text
from app.services.prompts import build_system_prompt, build_user_content
import app.services.transcription as _transcription

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Job-Status abfragen ───────────────────────────────────────────────────────

@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Gibt den aktuellen Status eines Jobs zurueck."""
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")
    return job.to_dict()


@router.get("/jobs")
async def list_jobs():
    """Listet alle Jobs auf (neueste zuerst)."""
    return [j.to_dict() for j in job_queue.get_all_jobs()[:50]]


# ── Asynchrone Generierung ────────────────────────────────────────────────────

@router.post("/jobs/generate")
async def create_generate_job(
    background_tasks: BackgroundTasks,
    workflow:       Annotated[Literal["dokumentation", "anamnese", "verlaengerung", "entlassbericht"], Form()],
    prompt:         Annotated[str,  Form()],
    therapeut_id:   Annotated[Optional[str], Form()] = None,
    diagnosen:      Annotated[Optional[str], Form()] = None,
    transcript:     Annotated[Optional[str], Form()] = None,
    bullets:        Annotated[Optional[str], Form()] = None,
    audio:          Optional[UploadFile] = File(None),
    selbstauskunft: Optional[UploadFile] = File(None),
    vorbefunde:     Optional[UploadFile] = File(None),
    style_file:     Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Startet einen asynchronen Generierungs-Job.
    Gibt sofort {job_id, status: "pending"} zurueck.
    Frontend pollt GET /api/jobs/{job_id} bis status="done".
    """
    # Dateien sofort einlesen (vor Background-Task, da UploadFile nicht thread-safe)
    audio_bytes         = await audio.read()         if audio         and audio.filename         else None
    audio_name          = audio.filename              if audio         and audio.filename         else None
    selbst_bytes        = await selbstauskunft.read() if selbstauskunft and selbstauskunft.filename else None
    selbst_name         = selbstauskunft.filename     if selbstauskunft and selbstauskunft.filename else None
    vorbef_bytes        = await vorbefunde.read()     if vorbefunde    and vorbefunde.filename    else None
    vorbef_name         = vorbefunde.filename         if vorbefunde    and vorbefunde.filename    else None
    style_bytes         = await style_file.read()     if style_file    and style_file.filename    else None
    style_name          = style_file.filename         if style_file    and style_file.filename    else None

    dx_list = [d.strip() for d in diagnosen.split(",") if d.strip()] if diagnosen else []

    # Job anlegen
    job = job_queue.create_job(
        workflow=workflow,
        description=f"Workflow: {workflow}" + (f" | Audio: {audio_name}" if audio_name else ""),
    )

    async def _run():
        # 1. Audio transkribieren
        audio_transcript = transcript or ""
        if audio_bytes and audio_name:
            from app.core.files import upload_dir
            import uuid
            from pathlib import Path
            suffix = Path(audio_name).suffix.lower()
            audio_path = upload_dir() / f"{uuid.uuid4().hex}{suffix}"
            audio_path.write_bytes(audio_bytes)
            tr = await _transcription.transcribe_audio(audio_path)
            audio_transcript = tr["transcript"]

        # 2. Dokumente extrahieren
        selbstauskunft_text = ""
        if selbst_bytes and selbst_name:
            from app.core.files import upload_dir
            import uuid
            from pathlib import Path
            suffix = Path(selbst_name).suffix.lower()
            path = upload_dir() / f"{uuid.uuid4().hex}{suffix}"
            path.write_bytes(selbst_bytes)
            try:
                selbstauskunft_text = await extract_text(path)
            except Exception as e:
                logger.warning("Selbstauskunft-Extraktion fehlgeschlagen: %s", e)

        vorbefunde_text = ""
        if vorbef_bytes and vorbef_name:
            from app.core.files import upload_dir
            import uuid
            from pathlib import Path
            suffix = Path(vorbef_name).suffix.lower()
            path = upload_dir() / f"{uuid.uuid4().hex}{suffix}"
            path.write_bytes(vorbef_bytes)
            try:
                vorbefunde_text = await extract_text(path)
            except Exception as e:
                logger.warning("Vorbefunde-Extraktion fehlgeschlagen: %s", e)

        # 3. Stilprofil
        style_context = ""
        if style_bytes and style_name:
            from app.core.files import upload_dir
            import uuid
            from pathlib import Path
            suffix = Path(style_name).suffix.lower()
            path = upload_dir() / f"{uuid.uuid4().hex}{suffix}"
            path.write_bytes(style_bytes)
            try:
                style_context = await extract_style_context(path, generate_text)
            except Exception as e:
                logger.warning("Stilprofil-Extraktion fehlgeschlagen: %s", e)
        elif therapeut_id and therapeut_id.strip():
            query_text = audio_transcript or transcript or bullets or ""
            style_context = await retrieve_style_examples(
                db, therapeut_id.strip(), workflow, query_text
            )

        # 4. Generieren
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
        result = await generate_text(system, user)
        return {
            "text":       result["text"],
            "model_used": result["model_used"],
        }

    background_tasks.add_task(job_queue.run_job, job, _run())
    return {"job_id": job.job_id, "status": "pending"}
