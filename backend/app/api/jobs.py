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


# ── Verfuegbare Modelle ───────────────────────────────────────────────────────

@router.get("/models")
async def list_models():
    """
    Gibt alle installierten Ollama-Modelle zurueck.
    Frontend nutzt diesen Endpunkt um die Modell-Auswahl zu befuellen.
    Das aktuell konfigurierte Standardmodell wird markiert.
    """
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.OLLAMA_HOST}/api/tags")
        r.raise_for_status()
        data = r.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            size_gb = round(m.get("size", 0) / 1e9, 1)
            models.append({
                "name":       name,
                "size_gb":    size_gb,
                "is_default": name == settings.OLLAMA_MODEL
                              or name.split(":")[0] == settings.OLLAMA_MODEL.split(":")[0],
            })
        # Standardmodell zuerst
        models.sort(key=lambda m: (0 if m["is_default"] else 1, m["name"]))
        return {
            "models":  models,
            "default": settings.OLLAMA_MODEL,
        }
    except Exception as e:
        logger.warning("Modell-Liste nicht abrufbar: %s", e)
        return {
            "models":  [{"name": settings.OLLAMA_MODEL, "size_gb": None, "is_default": True}],
            "default": settings.OLLAMA_MODEL,
        }


# ── Job-Status abfragen ───────────────────────────────────────────────────────

@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Gibt den aktuellen Status eines Jobs zurueck (ohne Transkript)."""
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")
    return job.to_dict()


@router.get("/jobs/{job_id}/transcript")
async def get_job_transcript(job_id: str):
    """
    Gibt das Transkript eines abgeschlossenen Jobs zurueck.
    Nur verfuegbar wenn der Job Audio enthalten hat.
    Separater Endpunkt damit das Transkript nicht bei jedem Poll-Request
    uebertragen wird (kann >50k Zeichen sein).
    """
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")
    if job.result_transcript is None:
        raise HTTPException(status_code=404, detail="Kein Transkript fuer diesen Job vorhanden")
    return {
        "job_id":     job_id,
        "transcript": job.result_transcript,
        "word_count": len(job.result_transcript.split()),
    }


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
    style_text:     Annotated[Optional[str], Form()] = None,
    model:          Annotated[Optional[str], Form()] = None,
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
        from app.services.llm import truncate_style_context
        style_context = ""
        style_is_example = False
        if style_text and style_text.strip():
            # Direkt eingefügter Stiltext (C&P) – kein Extraktionsschritt nötig
            # Deduplizieren falls die eingefügte Vorlage selbst repetitiv ist
            from app.services.llm import deduplicate_paragraphs
            cleaned = deduplicate_paragraphs(style_text.strip())
            style_context = truncate_style_context(cleaned)
            style_is_example = True   # Rohtext → explizite "nur Stil"-Rahmung
            logger.info("Stilvorlage via Text-Input (%d Zeichen nach Bereinigung)", len(style_context))
        elif style_bytes and style_name:
            from app.core.files import upload_dir
            import uuid
            from pathlib import Path
            suffix = Path(style_name).suffix.lower()
            path = upload_dir() / f"{uuid.uuid4().hex}{suffix}"
            path.write_bytes(style_bytes)
            try:
                style_context = await extract_style_context(path, generate_text)
                style_context = truncate_style_context(style_context)
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
            style_is_example=style_is_example,
            diagnosen=dx_list,
        )
        user = build_user_content(
            workflow=workflow,
            transcript=audio_transcript,
            bullets=bullets,
            selbstauskunft_text=selbstauskunft_text,
            # Für verlaengerung/entlassbericht kommt die Verlaufsdoku als vorbefunde-Upload
            vorbefunde_text=vorbefunde_text if workflow not in ("verlaengerung", "entlassbericht") else None,
            verlauf_text=vorbefunde_text    if workflow in ("verlaengerung", "entlassbericht")    else None,
            diagnosen=dx_list,
        )
        result = await generate_text(system, user, model=model)
        return {
            "text":       result["text"],
            "transcript": audio_transcript or None,
            "model_used": result["model_used"],
        }

    background_tasks.add_task(job_queue.run_job, job, _run())
    return {"job_id": job.job_id, "status": "pending"}
