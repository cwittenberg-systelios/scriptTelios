"""
GET  /api/jobs/{job_id}   – Job-Status abfragen
GET  /api/jobs            – Alle Jobs auflisten (optional)
"""
import logging
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from typing import Annotated, Literal, Optional

from app.core.config import settings
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


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    """
    Bricht einen laufenden Job ab.
    Setzt das Cancel-Flag – run_job() stoppt nach dem aktuellen Schritt.
    Ollama-Requests können nicht mittendrin unterbrochen werden,
    aber das Ergebnis wird nach Fertigstellung verworfen.
    """
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")
    cancelled = job_queue.cancel_job(job_id)
    return {
        "job_id":    job_id,
        "cancelled": cancelled,
        "status":    job.status.value,
    }


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
    workflow:         Annotated[Literal["dokumentation", "anamnese", "verlaengerung", "folgeverlaengerung", "entlassbericht"], Form()],
    prompt:           Annotated[str,  Form()],
    therapeut_id:     Annotated[Optional[str], Form()] = None,
    diagnosen:        Annotated[Optional[str], Form()] = None,
    transcript:       Annotated[Optional[str], Form()] = None,
    bullets:          Annotated[Optional[str], Form(description="Stichpunkte (P1) oder Fokus-Themen (P3/P4)")] = None,
    style_text:       Annotated[Optional[str], Form()] = None,
    model:            Annotated[Optional[str], Form()] = None,
    # ── Datei-Uploads (jedes Feld hat genau EINE Bedeutung) ──────────
    audio:            Optional[UploadFile] = File(None, description="Audioaufnahme eines Gesprächs (.mp3/.m4a/.wav)"),
    selbstauskunft:   Optional[UploadFile] = File(None, description="P2: Selbstauskunft des Klienten (.pdf)"),
    vorbefunde:       Optional[UploadFile] = File(None, description="P2: Berichte früherer Therapeuten/Kliniken (.pdf)"),
    verlaufsdoku:     Optional[UploadFile] = File(None, description="P3/P4: Verlaufsdokumentation der aktuellen Behandlung (.pdf)"),
    antragsvorlage:   Optional[UploadFile] = File(None, description="P3/P4: Aktueller Bericht (EB/VA) ohne Verlaufsabschnitt (.docx/.pdf)"),
    vorantrag:        Optional[UploadFile] = File(None, description="Folgeverlängerung: Vorheriger Bericht mit Verlauf/Anamnese/Diagnosen (.docx/.pdf)"),
    style_file:       Optional[UploadFile] = File(None, description="Stilvorlage (Beispieltext)"),
):
    """
    Startet einen asynchronen Generierungs-Job.
    Gibt sofort {job_id, status: "pending"} zurück.
    Frontend pollt GET /api/jobs/{job_id} bis status="done".

    Input-Zuordnung pro Workflow:
      P1 (dokumentation):       audio + transcript + bullets
      P2 (anamnese):            selbstauskunft + vorbefunde + audio + diagnosen
      P3 (verlaengerung):       verlaufsdoku + antragsvorlage + bullets (Fokus-Themen)
      P3b (folgeverlaengerung): verlaufsdoku + antragsvorlage + vorantrag + bullets
      P4 (entlassbericht):      verlaufsdoku + antragsvorlage + bullets (Fokus-Themen)
    """
    # Dateien sofort einlesen (vor Background-Task, da UploadFile nicht thread-safe)
    audio_bytes            = await audio.read()          if audio          and audio.filename          else None
    audio_name             = audio.filename               if audio          and audio.filename          else None
    selbstauskunft_bytes   = await selbstauskunft.read()  if selbstauskunft  and selbstauskunft.filename else None
    selbstauskunft_name    = selbstauskunft.filename      if selbstauskunft  and selbstauskunft.filename else None
    vorbefunde_bytes       = await vorbefunde.read()      if vorbefunde     and vorbefunde.filename     else None
    vorbefunde_name        = vorbefunde.filename          if vorbefunde     and vorbefunde.filename     else None
    verlaufsdoku_bytes     = await verlaufsdoku.read()    if verlaufsdoku   and verlaufsdoku.filename   else None
    verlaufsdoku_name      = verlaufsdoku.filename        if verlaufsdoku   and verlaufsdoku.filename   else None
    antragsvorlage_bytes   = await antragsvorlage.read()  if antragsvorlage and antragsvorlage.filename else None
    antragsvorlage_name    = antragsvorlage.filename      if antragsvorlage and antragsvorlage.filename else None
    vorantrag_bytes        = await vorantrag.read()       if vorantrag      and vorantrag.filename      else None
    vorantrag_name         = vorantrag.filename           if vorantrag      and vorantrag.filename      else None
    style_bytes            = await style_file.read()      if style_file     and style_file.filename     else None
    style_name             = style_file.filename          if style_file     and style_file.filename     else None

    dx_list = [d.strip() for d in diagnosen.split(",") if d.strip()] if diagnosen else []

    # Job anlegen
    job = job_queue.create_job(
        workflow=workflow,
        description=f"Workflow: {workflow}" + (f" | Audio: {audio_name}" if audio_name else ""),
    )

    # Performance-Tracking: welche Inputs hat dieser Job?
    job.input_meta = {
        "has_audio":          bool(audio_bytes),
        "audio_mb":           round(len(audio_bytes) / 1e6, 1) if audio_bytes else 0,
        "has_selbstauskunft": bool(selbstauskunft_bytes),
        "has_vorbefunde":     bool(vorbefunde_bytes),
        "has_verlaufsdoku":   bool(verlaufsdoku_bytes),
        "has_antragsvorlage": bool(antragsvorlage_bytes),
        "has_vorantrag":      bool(vorantrag_bytes),
        "has_style":          bool(style_bytes) or bool(style_text and style_text.strip()),
        "has_transcript":     bool(transcript and transcript.strip()),
        "has_fokus_themen":   bool(bullets and bullets.strip()),
        "diagnosen":          dx_list,
        "model_requested":    model or "default",
    }

    async def _run():
        import uuid as _uuid
        from pathlib import Path as _Path
        from app.core.files import upload_dir

        # ── 1. Audio transkribieren ──────────────────────────────────
        transkript_text = transcript or ""
        if audio_bytes and audio_name:
            suffix = _Path(audio_name).suffix.lower()
            audio_path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            audio_path.write_bytes(audio_bytes)
            tr = await _transcription.transcribe_audio(audio_path)
            transkript_text = tr["transcript"]

        # ── 2. Dokumente extrahieren (jedes Feld → eigene Variable) ──

        # P2: Selbstauskunft des Klienten
        selbstauskunft_text = ""
        if selbstauskunft_bytes and selbstauskunft_name:
            suffix = _Path(selbstauskunft_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(selbstauskunft_bytes)
            try:
                selbstauskunft_text = await extract_text(path)
            except Exception as e:
                logger.warning("Selbstauskunft-Extraktion fehlgeschlagen: %s", e)

        # P2: Vorbefunde (Berichte früherer Therapeuten/Kliniken)
        vorbefunde_text = ""
        if vorbefunde_bytes and vorbefunde_name:
            suffix = _Path(vorbefunde_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(vorbefunde_bytes)
            try:
                vorbefunde_text = await extract_text(path)
            except Exception as e:
                logger.warning("Vorbefunde-Extraktion fehlgeschlagen: %s", e)

        # P3/P4: Verlaufsdokumentation der aktuellen Behandlung
        verlaufsdoku_text = ""
        if verlaufsdoku_bytes and verlaufsdoku_name:
            suffix = _Path(verlaufsdoku_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(verlaufsdoku_bytes)
            try:
                verlaufsdoku_text = await extract_text(path)
                from app.services.llm import clean_verlauf_text
                verlaufsdoku_text = clean_verlauf_text(verlaufsdoku_text)
            except Exception as e:
                logger.warning("Verlaufsdoku-Extraktion fehlgeschlagen: %s", e)

        # P3/P4: Antragsvorlage (EB/VA mit Diagnosen, Anamnese, ohne Verlauf)
        antragsvorlage_text = ""
        if antragsvorlage_bytes and antragsvorlage_name:
            suffix = _Path(antragsvorlage_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(antragsvorlage_bytes)
            try:
                antragsvorlage_text = await extract_text(path)
            except Exception as e:
                logger.warning("Antragsvorlage-Extraktion fehlgeschlagen: %s", e)

        # Folgeverlängerung: Vorheriger Bericht (Verlauf + Anamnese + Diagnosen)
        vorantrag_text = ""
        if vorantrag_bytes and vorantrag_name:
            suffix = _Path(vorantrag_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(vorantrag_bytes)
            try:
                vorantrag_text = await extract_text(path)
                logger.info("Vorantrag extrahiert: %d Zeichen", len(vorantrag_text))
            except Exception as e:
                logger.warning("Vorantrag-Extraktion fehlgeschlagen: %s", e)

        # 3. Stilprofil
        from app.services.llm import truncate_style_context
        style_context = ""
        style_is_example = False
        style_info = None   # Metadaten: source, chars – wird im Job gespeichert
        if style_text and style_text.strip():
            from app.services.llm import deduplicate_paragraphs
            cleaned = deduplicate_paragraphs(style_text.strip())
            style_context = truncate_style_context(cleaned)
            style_is_example = True
            style_info = {"source": "text_input", "chars": len(style_context), "words": len(style_context.split())}
            logger.info("Stilvorlage via Text-Input (%d Zeichen nach Bereinigung)", len(style_context))
        elif style_bytes and style_name:
            suffix = _Path(style_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(style_bytes)
            try:
                style_context = await extract_style_context(path, generate_text)
                style_context = truncate_style_context(style_context)
                style_info = {"source": "file_upload", "filename": style_name, "chars": len(style_context)}
            except Exception as e:
                logger.warning("Stilprofil-Extraktion fehlgeschlagen: %s", e)
        elif therapeut_id and therapeut_id.strip():
            # Eigene Session im Background-Task öffnen und explizit schließen.
            # NICHT die Request-Session nutzen – die ist nach Request-Ende geschlossen.
            from app.core.database import async_session_factory
            async with async_session_factory() as db:
                query_text = transkript_text or transcript or bullets or ""
                style_context = await retrieve_style_examples(
                    db, therapeut_id.strip(), workflow, query_text
                )
            if style_context:
                style_info = {"source": "style_library", "therapeut_id": therapeut_id.strip(), "chars": len(style_context)}

        # 4. Generieren – jede Variable hat genau eine Bedeutung
        system = build_system_prompt(
            workflow=workflow,
            custom_prompt=prompt,
            style_context=style_context,
            style_is_example=style_is_example,
            diagnosen=dx_list,
        )
        user = build_user_content(
            workflow=workflow,
            transcript=transkript_text,
            fokus_themen=bullets,
            selbstauskunft_text=selbstauskunft_text,
            vorbefunde_text=vorbefunde_text,
            verlaufsdoku_text=verlaufsdoku_text,
            antragsvorlage_text=antragsvorlage_text,
            vorantrag_text=vorantrag_text,
            diagnosen=dx_list,
            custom_prompt=prompt if prompt and prompt.strip() else None,
        )
        # Workflow-spezifische max_tokens:
        # Entlassbericht/Verlängerung: langer Fliesstext, mind. 800 Wörter → 4000 Tokens
        # Anamnese: zwei Teile (Anamnese + Befund) → 3000 Tokens
        # Dokumentation: kompakter → 2048 Tokens (Default)
        max_tokens_map = {
            "entlassbericht":       4000,
            "verlaengerung":        3000,
            "folgeverlaengerung":   3000,
            "anamnese":             3000,
            "dokumentation":        2048,
        }
        max_tok = max_tokens_map.get(workflow, 2048)
        result = await generate_text(system, user, max_tokens=max_tok, model=model, workflow=workflow)
        raw = result["text"] or ""

        # Anamnese: Ergebnis bei ###BEFUND### und optional ###AKUT### aufteilen
        akut_part = None
        if workflow == "anamnese" and "###BEFUND###" in raw:
            parts_split = raw.split("###BEFUND###", 1)
            anamnese_part = parts_split[0].strip()
            rest = parts_split[1].strip()
            if "###AKUT###" in rest:
                akut_split = rest.split("###AKUT###", 1)
                befund_part = akut_split[0].strip()
                akut_part   = akut_split[1].strip()
            else:
                befund_part = rest
        else:
            anamnese_part = raw
            befund_part   = None

        return {
            "text":        anamnese_part,
            "befund_text": befund_part,
            "akut_text":   akut_part,
            "transcript":  transkript_text or None,
            "model_used":  result["model_used"],
            "style_info":  style_info,
        }

    background_tasks.add_task(job_queue.run_job, job, _run())
    return {"job_id": job.job_id, "status": "pending"}
