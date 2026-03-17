"""
POST /api/documents/fill        – DOCX-Vorlage befuellen (Workflow 3 & 4)
POST /api/documents/style       – Stilprofil aus Beispieltext extrahieren
GET  /api/documents/download/{filename} – Befuelltes DOCX herunterladen
"""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from typing import Annotated, Optional

from app.core.config import settings
from app.core.files import save_upload, ALLOWED_DOCS, ALLOWED_IMAGES, output_dir
from app.models.schemas import DocProcessResponse, ExtractionInfo, ExtractionResponse, StyleProfileResponse
from app.services.docx_fill import fill_docx_template
from app.services.extraction import extract_text, extract_style_context
from app.services.llm import generate_text
from app.services.prompts import build_system_prompt, build_user_content

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/documents/fill", response_model=DocProcessResponse)
async def fill_document(
    workflow:  Annotated[str, Form(description="'verlaengerung' oder 'entlassbericht'")],
    prompt:    Annotated[str, Form()],
    template:  UploadFile = File(..., description="DOCX-Vorlage"),
    verlauf:   UploadFile = File(..., description="Verlaufsdokumentation (PDF)"),
    style_file: Optional[UploadFile] = File(None),
):
    """
    Workflow 3 (Verlaengerungsantrag) und 4 (Entlassbericht):
    1. Verlaufsdokumentation (PDF) extrahieren
    2. Optional: Stilprofil aus Beispieltext
    3. Text per LLM generieren
    4. DOCX-Vorlage befuellen und zum Download bereitstellen
    """
    if workflow not in ("verlaengerung", "entlassbericht"):
        raise HTTPException(status_code=422, detail="workflow muss 'verlaengerung' oder 'entlassbericht' sein")

    # ── Vorlage speichern ────────────────────────────────────────
    template_path = await save_upload(template, {".docx", ".doc"})

    # ── Verlaufsdokumentation extrahieren ────────────────────────
    verlauf_path = await save_upload(verlauf, ALLOWED_DOCS)
    try:
        verlauf_text = await extract_text(verlauf_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Verlaufsdokumentation konnte nicht gelesen werden: {e}")

    if len(verlauf_text.strip()) < 50:
        raise HTTPException(status_code=422, detail="Verlaufsdokumentation scheint leer zu sein (OCR fehlgeschlagen?)")

    # ── Stilprofil (optional) ────────────────────────────────────
    style_context = ""
    if style_file and style_file.filename:
        style_path = await save_upload(style_file, ALLOWED_DOCS)
        try:
            style_context = await extract_style_context(style_path, generate_text)
        except Exception as e:
            logger.warning("Stilprofil-Extraktion fehlgeschlagen: %s", e)

    # ── LLM-Generierung ──────────────────────────────────────────
    system = build_system_prompt(
        workflow=workflow,
        custom_prompt=prompt,
        style_context=style_context,
    )
    user = build_user_content(
        workflow=workflow,
        verlauf_text=verlauf_text[:8000],   # Token-Limit beachten
    )

    try:
        result = await generate_text(system, user, max_tokens=3000)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    generated_text = result["text"]

    # ── DOCX befuellen ───────────────────────────────────────────
    try:
        out_path = await fill_docx_template(
            template_path=template_path,
            verlauf_text=verlauf_text,
            generated_text=generated_text,
            output_dir=output_dir(),
            workflow=workflow,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    job_id = uuid.uuid4().hex
    download_url = f"/api/documents/download/{out_path.name}"
    preview = generated_text[:500] + ("..." if len(generated_text) > 500 else "")

    logger.info("Dokument erstellt [%s]: %s", job_id, out_path.name)

    return DocProcessResponse(
        job_id=job_id,
        download_url=download_url,
        filename=out_path.name,
        preview_text=preview,
    )


@router.post("/documents/style", response_model=StyleProfileResponse)
async def extract_style(
    therapeut_id: Annotated[str, Form(description="Eindeutige Therapeuten-ID")],
    style_file:   UploadFile = File(..., description="Beispieltext des Therapeuten"),
):
    """
    Extrahiert das Schreibstil-Profil eines Therapeuten aus einem Beispieltext.
    Kann vorab gespeichert und bei kuenftigen Anfragen wiederverwendet werden.
    """
    path = await save_upload(style_file, ALLOWED_DOCS)

    try:
        style_context = await extract_style_context(path, generate_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stilprofil-Extraktion fehlgeschlagen: {e}")

    if not style_context.strip():
        raise HTTPException(status_code=422, detail="Kein verwertbarer Text im Dokument gefunden")

    raw_text = await extract_text(path)
    word_count = len(raw_text.split())

    profile_id = uuid.uuid4().hex
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    logger.info("Stilprofil erstellt fuer Therapeut '%s': %d Zeichen", therapeut_id, len(style_context))

    return StyleProfileResponse(
        profile_id=profile_id,
        therapeut_id=therapeut_id,
        style_context=style_context,
        word_count=word_count,
        created_at=now,
    )


@router.get("/documents/download/{filename}")
async def download_document(filename: str):
    """Stellt ein generiertes DOCX zum Download bereit."""
    # Sicherheitscheck: kein Path-Traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Ungueltiger Dateiname")

    path = output_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden oder bereits abgelaufen")

    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )

@router.post("/documents/extract", response_model=ExtractionResponse)
async def extract_document(
    file: UploadFile = File(..., description="PDF, DOCX, TXT oder Bild"),
):
    """
    Extrahiert Text aus einem Dokument und gibt Metadaten zurueck.
    Nuetzlich fuer:
    - Vorab-Vorschau vor der Generierung
    - Debugging: welche OCR-Stufe wurde verwendet?
    - Qualitaetspruefung: wie gut war die Extraktion?
    """
    from app.services.extraction import extract_text_with_meta

    allowed = ALLOWED_DOCS | ALLOWED_IMAGES
    path = await save_upload(file, allowed)

    try:
        result = await extract_text_with_meta(path)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ExtractionResponse(
        filename=file.filename or path.name,
        text=result.text,
        char_count=len(result.text),
        word_count=len(result.text.split()),
        extraction=ExtractionInfo(
            method=result.method,
            quality=round(result.quality, 3),
            pages=result.pages,
            warnings=result.warnings,
        ),
    )
