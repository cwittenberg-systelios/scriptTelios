"""
GET  /api/jobs/{job_id}   – Job-Status abfragen
GET  /api/jobs            – Alle Jobs auflisten (optional)
"""
import logging
import time as _t
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, Depends
from typing import Annotated, Optional

from app.core.workflows import word_limit_for

# statt _wl_defaults-Dict:
_fb_min, _fb_max = word_limit_for(workflow, fallback=(200, 800))

from app.core.config import settings
from app.core.auth import get_current_user
from app.core.files import size_class as _size_class
from app.services.job_queue import job_queue, JobStatus
from app.services.embeddings import retrieve_style_examples
from app.services.extraction import extract_text, extract_style_context
from app.services.llm import (
    generate_text,
    clean_verlauf_text,
    truncate_style_context,
    deduplicate_paragraphs,
    substitute_patient_placeholders,
)
from app.services.prompts import build_system_prompt, build_user_content
import app.services.transcription as _transcription

router = APIRouter()
logger = logging.getLogger(__name__)

# Separater Prompt-Logger – schreibt vollständige System/User-Prompts in prompts.log
# Zweck: manuelle Inspektion und Prompt-Debugging ohne den Haupt-Log zu fluten.
_prompt_logger = logging.getLogger("systelios.prompts")


def _setup_prompt_logger() -> None:
    """Richtet den Prompt-Logger ein (einmalig beim Import)."""
    if _prompt_logger.handlers:
        return
    _prompt_logger.setLevel(logging.DEBUG)
    _prompt_logger.propagate = False
    import os
    from pathlib import Path as _Path2
    log_dir = _Path2(os.environ.get("LOG_FILE", "/workspace/systelios.log")).parent
    prompt_file = log_dir / "prompts.log"
    try:
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(prompt_file), encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        _prompt_logger.addHandler(handler)
    except Exception as e:
        logger.warning("Prompt-Logger konnte nicht eingerichtet werden: %s", e)


_setup_prompt_logger()


def _log_prompt(job_id: str, workflow: str, call_label: str,
                system: str, user: str) -> None:
    """
    Schreibt System- und User-Prompt eines LLM-Calls in prompts.log.

    call_label: z.B. 'anamnese', 'befund', 'verlaengerung' – unterscheidet
                bei Anamnese den ersten vom zweiten LLM-Call.
    """
    sep = "=" * 80
    _prompt_logger.debug(
        "\n%s\nJOB: %s  |  WORKFLOW: %s  |  CALL: %s\n%s\n"
        "--- SYSTEM ---\n%s\n"
        "--- USER ---\n%s\n%s\n",
        sep, job_id, workflow, call_label, sep,
        system, user, sep,
    )


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
async def get_job(job_id: str, current_user: str = Depends(get_current_user)):
    """Gibt den aktuellen Status eines Jobs zurueck (ohne Transkript)."""
    job = job_queue.get_job(job_id)
    if job:
        return job.to_dict()
    # Fallback: DB-Lookup (Job aus anderem Worker oder nach Restart)
    db_result = await job_queue.get_job_from_db(job_id)
    if db_result:
        return db_result
    raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str, current_user: str = Depends(get_current_user)):
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
    if job:
        if job.result_transcript is None:
            raise HTTPException(status_code=404, detail="Kein Transkript fuer diesen Job vorhanden")
        return {
            "job_id":     job_id,
            "transcript": job.result_transcript,
            "word_count": len(job.result_transcript.split()),
        }
    # Fallback: DB
    db_result = await job_queue.get_job_from_db(job_id)
    if not db_result:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")
    transcript = db_result.get("result_text", "")  # DB hat kein separates transcript-Feld im dict
    # Transkript direkt aus DB laden
    try:
        from app.core.database import async_session_factory
        from app.models.db import Job as JobModel
        from sqlalchemy import select
        async with async_session_factory() as db:
            result = await db.execute(select(JobModel.result_transcript).where(JobModel.id == job_id))
            row = result.scalar_one_or_none()
            if not row:
                raise HTTPException(status_code=404, detail="Kein Transkript fuer diesen Job vorhanden")
            return {"job_id": job_id, "transcript": row, "word_count": len(row.split())}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Transkript nicht verfuegbar")



# ── Server-Sent Events: Live-Progress-Stream ─────────────────────────────────

@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    """
    SSE-Endpoint fuer Live-Progress-Updates.

    Frontend kann statt Polling einen EventSource oeffnen:
      const es = new EventSource('/api/jobs/{id}/stream');
      es.onmessage = (e) => { const data = JSON.parse(e.data); ... };

    Events:
      - {type: "progress", progress: 42, phase: "Transkription", detail: "Chunk 2/4"}
      - {type: "done", result_text: "...", befund_text: "...", ...}
      - {type: "error", error_msg: "..."}
      - {type: "cancelled"}
    """
    from starlette.responses import StreamingResponse
    import asyncio, json

    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")

    async def event_generator():
        last_progress = -1
        last_phase = ""
        while True:
            j = job_queue.get_job(job_id)
            if not j:
                yield f"data: {json.dumps({'type': 'error', 'error_msg': 'Job nicht gefunden'})}\n\n"
                break

            # Progress-Update senden wenn sich was geaendert hat
            if j.progress != last_progress or j.progress_phase != last_phase:
                last_progress = j.progress
                last_phase = j.progress_phase
                yield f"data: {json.dumps({'type': 'progress', 'progress': j.progress, 'phase': j.progress_phase, 'detail': j.progress_detail})}\n\n"

            # Terminal-States
            if j.status == JobStatus.DONE.value:
                yield f"data: {json.dumps({'type': 'done', 'result_text': j.result_text or '', 'befund_text': j.result_befund or '', 'akut_text': j.result_akut or '', 'has_transcript': j.result_transcript is not None, 'job_id': job_id, 'model_used': j.model_used})}\n\n"
                break
            elif j.status == JobStatus.ERROR.value:
                yield f"data: {json.dumps({'type': 'error', 'error_msg': j.error_msg or 'Unbekannter Fehler'})}\n\n"
                break
            elif j.status == JobStatus.CANCELLED.value:
                yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                break

            # Adaptives Intervall: schnell (1s) waehrend LLM-Generierung
            # (Token-Zaehler aendert sich oft), langsam (5s) sonst
            is_llm_phase = j.progress_phase in ("KI-Generierung", "Fertig")
            await asyncio.sleep(1 if is_llm_phase else 10)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx/CF: nicht buffern
        },
    )


@router.get("/jobs")
async def list_jobs():
    """Listet alle Jobs auf (neueste zuerst)."""
    return [j.to_dict() for j in job_queue.get_all_jobs()[:50]]


# ── Asynchrone Generierung ────────────────────────────────────────────────────

@router.post("/jobs/generate")
async def create_generate_job(
    background_tasks: BackgroundTasks,
    workflow:         Annotated[WorkflowLiteral, Form()],
    # v18: 'prompt' wurde umbenannt zu 'workflow_instructions' fuer Klarheit.
    # Frontend-Default fuer dieses Feld kommt aus WORKFLOW_INSTRUCTIONS_DEFAULT[workflow].
    # Leere oder fehlende Werte werden mit 422 abgelehnt (s. unten).
    # Backwards-Compat: 'prompt' wird weiterhin akzeptiert.
    workflow_instructions: Annotated[Optional[str], Form(description="Editierbare inhaltliche Workflow-Anweisungen (war frueher 'prompt'). Pflichtfeld - leer = 422.")] = None,
    prompt:           Annotated[Optional[str], Form(description="DEPRECATED: alter Parametername fuer workflow_instructions.")] = None,
    befund_vorlage:   Annotated[Optional[str], Form(description="P2 (Anamnese): editierbare AMDP-Vorlage fuer den Befund-Call. Default = BEFUND_VORLAGE.")] = None,
    therapeut_id:     Annotated[Optional[str], Form()] = None,
    patientenname:    Annotated[Optional[str], Form(description="Explizit uebergebener Patientenname (vor allem bei P1 Gespraechszusammenfassung). Format: 'Vorname Nachname' oder 'Herr/Frau Nachname'. Wird in Initiale umgewandelt fuer den Output.")] = None,
    current_user:     str = Depends(get_current_user),
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
      P2 (anamnese):            selbstauskunft + vorbefunde + audio + diagnosen + befund_vorlage
      P3 (verlaengerung):       verlaufsdoku + antragsvorlage + bullets (Fokus-Themen)
      P3b (folgeverlaengerung): verlaufsdoku + antragsvorlage + vorantrag + bullets
      P3c (akutantrag):          antragsvorlage (Anamnese/Befund/Diagnosen) + verlaufsdoku (opt)
      P4 (entlassbericht):      verlaufsdoku + antragsvorlage + bullets (Fokus-Themen)
    """
    # K1: Therapeut-ID aus validiertem Auth-Header
    therapeut_id = current_user

    # v18: Workflow-Anweisungen sind Pflicht. Backwards-Compat: alter Name 'prompt'.
    # Architekturvorgabe: Wenn das Frontend fuer einen Workflow KEINE
    # Anweisungen schickt, lehnt das Backend den Job ab. Es gibt KEINEN
    # impliziten Fallback auf den Default - sonst koennte ein verbuggtes
    # Frontend unbemerkt mit leerem Auftrag laufen.
    instructions = workflow_instructions or prompt
    if not instructions or not instructions.strip():
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail=(
                "Pflichtfeld 'workflow_instructions' (alias 'prompt') ist leer. "
                "Das Frontend muss die Workflow-Anweisungen mitsenden. "
                "Default-Texte stehen im Frontend zur Verfuegung und werden "
                "in WORKFLOW_INSTRUCTIONS_DEFAULT[workflow] vorgehalten."
            ),
        )

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
        from app.services.progress_bands import compute_bands

        # Bands + Timing frühzeitig initialisieren (werden in allen Phasen gebraucht)
        _has_audio = bool(audio_bytes) if 'audio_bytes' in dir() else bool(audio_bytes)
        _has_docs = bool(verlaufsdoku_bytes or antragsvorlage_bytes or selbstauskunft_bytes)
        bands = compute_bands(workflow, has_audio=_has_audio, has_docs=_has_docs)
        phase_times = {}

        # ── 1. Audio transkribieren ──────────────────────────────────
        transkript_text = transcript or ""
        if audio_bytes and audio_name:
            suffix = _Path(audio_name).suffix.lower()
            audio_path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            audio_path.write_bytes(audio_bytes)
            if "transcription" in bands:
                job.set_progress(bands["transcription"][0], "Audio-Transkription")
            _t0 = _t.time()
            tr = await _transcription.transcribe_audio(audio_path)
            phase_times["transcription"] = _t.time() - _t0
            if "transcription" in bands:
                job.set_progress(bands["transcription"][1])
            transkript_text = tr["transcript"]

        # Cancel-Check nach Transkription (teuerster Schritt)
        if job._cancel_requested:
            raise RuntimeError("__CANCELLED__")

        # ── 2. Dokumente extrahieren (jedes Feld → eigene Variable) ──

        # P2: Selbstauskunft des Klienten
        if "extraction" in bands:
            job.set_progress(bands["extraction"][0], "Dokument-Extraktion")
        _ex_t0 = _t.time()

        # P2: Helper fuer OCR-Mülldaten-Validierung. Loggt Auffaelligkeiten und
        # gibt im Job einen Warn-Hinweis zurueck. Verworfen wird nichts hart -
        # die Klinik soll trotzdem ein Ergebnis bekommen, aber im UI sehen
        # dass die Quelle problematisch war.
        from app.services.extraction import detect_extraction_garbage
        _ocr_warnings: list[str] = []

        def _check_ocr_garbage(text: str, label: str, file_name: str = "") -> str:
            """Prueft Text auf OCR-Mülldaten und sammelt Warnungen.
            Gibt den Text unveraendert zurueck (kein Hard-Filter), damit der
            Job weiterlaeuft. Eskalation auf Reject-Schwelle: wenn drei oder
            mehr Probleme auf einer Quelle erkannt werden, wird der Text
            geleert um Halluzinationen zu verhindern."""
            if not text or len(text.strip()) < 50:
                return text
            problems = detect_extraction_garbage(text, file_name)
            if problems:
                msg = f"{label} ({file_name or '?'}): " + "; ".join(problems)
                logger.warning("[OCR-Validator] %s", msg)
                _ocr_warnings.append(msg)
                # Hard-Reject ab 3+ Problemen: Quelle ist offensichtlich Müll,
                # wir leeren sie um zu verhindern dass das LLM aus Halluzinationen
                # einen plausibel klingenden Bericht baut.
                if len(problems) >= 3:
                    logger.error(
                        "[OCR-Validator] %s als unbrauchbar verworfen (%d Probleme)",
                        label, len(problems)
                    )
                    return ""
            return text

        selbstauskunft_text = ""
        if selbstauskunft_bytes and selbstauskunft_name:
            suffix = _Path(selbstauskunft_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(selbstauskunft_bytes)
            try:
                selbstauskunft_text = await extract_text(path)
                selbstauskunft_text = _check_ocr_garbage(
                    selbstauskunft_text, "Selbstauskunft", selbstauskunft_name)
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
                vorbefunde_text = _check_ocr_garbage(
                    vorbefunde_text, "Vorbefunde", vorbefunde_name)
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
                verlaufsdoku_text = clean_verlauf_text(verlaufsdoku_text)
                verlaufsdoku_text = _check_ocr_garbage(
                    verlaufsdoku_text, "Verlaufsdokumentation", verlaufsdoku_name)
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
                antragsvorlage_text = _check_ocr_garbage(
                    antragsvorlage_text, "Antragsvorlage", antragsvorlage_name)
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
                vorantrag_text = _check_ocr_garbage(
                    vorantrag_text, "Vorantrag", vorantrag_name)
                logger.info("Vorantrag extrahiert: %s", _size_class(len(vorantrag_text)))
            except Exception as e:
                logger.warning("Vorantrag-Extraktion fehlgeschlagen: %s", e)

        # P2: Wenn ALLE Quell-Texte als unbrauchbar gefiltert wurden, ist die
        # Datenlage zu duenn fuer eine sinnvolle Generierung - frueh abbrechen.
        _all_sources = [selbstauskunft_text, vorbefunde_text, verlaufsdoku_text,
                        antragsvorlage_text, vorantrag_text]
        if _ocr_warnings and not any(s and len(s.strip()) > 100 for s in _all_sources):
            # Wir haben kein Transkript noch keinen Text - Hard-Stop.
            if not (transkript_text or transcript or bullets):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Quelldokumente konnten nicht zuverlaessig gelesen werden. "
                        "Probleme: " + "; ".join(_ocr_warnings[:3])
                    ),
                )

        # 3. Stilprofil
        from app.services.prompts import derive_word_limits
        style_context = ""
        style_is_example = False
        style_info = None   # Metadaten: source, chars – wird im Job gespeichert
        # Roh-Texte fuer Wortlimit-Berechnung (vor Destillation/Truncation gesammelt)
        _style_raw_texts: list[str] = []

        if style_text and style_text.strip():
            cleaned = deduplicate_paragraphs(style_text.strip())
            _style_raw_texts.append(cleaned)          # Roh-Text vor Truncation
            style_context = truncate_style_context(cleaned)
            style_is_example = True
            style_info = {"source": "text_input", "chars": len(style_context), "words": len(style_context.split())}
            logger.info("Stilvorlage via Text-Input: %s", _size_class(len(style_context)))
        elif style_bytes and style_name:
            suffix = _Path(style_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(style_bytes)
            try:
                # Roh-Text für Wortlimit abgreifen bevor extract_style_context ihn destilliert
                from app.services.extraction import extract_text as _extract_raw
                from app.services.extraction import extract_docx_section as _extract_section
                try:
                    if suffix in (".docx", ".doc") and workflow:
                        _raw = _extract_section(path, workflow)
                    else:
                        _raw = await _extract_raw(path)
                    if _raw and len(_raw.split()) >= 50:
                        _style_raw_texts.append(_raw)
                except Exception:
                    pass  # Wortlimit-Berechnung faellt auf Defaults zurueck
                style_context = await extract_style_context(path, generate_text, workflow=workflow)
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
                _style_raw_texts.append(style_context)  # pgvector gibt bereits Rohtext
                style_info = {"source": "style_library", "therapeut_id": therapeut_id.strip(), "chars": len(style_context)}

        # Wortlimit aus Roh-Texten ableiten (vor Destillation, fuer alle Quellen konsistent)
        # v13: Workflow-spezifische Defaults aus dem zentralen WORKFLOWS-Modul.
        # Wenn ein Workflow umkalibriert werden soll, in app/core/workflows.py
        # editieren, NICHT hier hardcoden.
        from app.core.workflows import word_limit_for
        _fb_min, _fb_max = word_limit_for(workflow, fallback=(200, 800))
        if _style_raw_texts:
            word_limits = derive_word_limits(_style_raw_texts, _fb_min, _fb_max)
            _wl_source = f"Stilvorlage(n) (n={len(_style_raw_texts)})"
        else:
            # P1: Auch ohne Stilvorlage hartes TEXTLIMIT setzen.
            # Frueher (bis v18) war word_limits=None wenn keine Stilvorlage vorlag.
            # Folge: Anamnese und Doku produzierten 1000+ Woerter Outputs (siehe
            # Eval-Report v12: an-02=1181w/Cap 418w, dok-02=443w/Cap 143w), weil
            # nur der weiche "Richtwert ca. 450-700 Wörter" im BASE_PROMPT stand.
            # Jetzt: Workflow-Default als hartes TEXTLIMIT durchreichen.
            word_limits = (_fb_min, _fb_max)
            _wl_source = "Workflow-Default (keine Stilvorlage)"
        # Bug-Fix #2: Akutantrag-Stilvorlagen enthalten oft den Volltext (Anamnese,
        # Befund, Familienanamnese), wodurch derive_word_limits faelschlich auf
        # 400-800 Woerter aufschwingt. Die echte "Begruendung fuer Akutaufnahme"
        # ist 100-350 Woerter. Wir cappen daher auf den Workflow-Default-Bereich
        # wenn das berechnete Limit ueber dem max-Default liegt.
        if workflow == "akutantrag" and word_limits:
            _orig_min, _orig_max = word_limits
            if _orig_max > _fb_max or _orig_min > _fb_max:
                # Berechnetes Limit klar ausserhalb -> auf Defaults zuruecksetzen
                word_limits = (_fb_min, _fb_max)
                logger.info(
                    "Akutantrag-Cap: Wortlimit %d-%d -> %d-%d (Default, Stilvorlage zu lang)",
                    _orig_min, _orig_max, _fb_min, _fb_max,
                )
        logger.info("Wortlimit fuer %s: %d–%d Wörter (%s)",
                    workflow, word_limits[0], word_limits[1], _wl_source)

        # 4. Patientennamen ermitteln — Reihenfolge:
        #    a) Explizit uebergeben (vor allem P1 Gespraechszusammenfassung)
        #    b) Aus antragsvorlage/vorantrag (Briefkopf "Wir berichten ueber ...")
        #    c) Aus selbstauskunft (Seite 1, "Nachname: ...")
        #    d) Fallback: verlaufsdoku / vorbefunde
        from app.services.extraction import extract_patient_name, parse_explicit_patient_name
        patient_name = None

        # a) Explizit uebergeben
        if patientenname and patientenname.strip():
            patient_name = parse_explicit_patient_name(patientenname.strip())
            if patient_name:
                logger.info("Patientenname explizit uebergeben: %s %s.",
                            patient_name["anrede"], patient_name["initial"])

        # b-d) Fallback: aus Dokumenten extrahieren
        if not patient_name:
            for src_text in (antragsvorlage_text, vorantrag_text, selbstauskunft_text, verlaufsdoku_text, vorbefunde_text):
                if src_text:
                    patient_name = extract_patient_name(src_text)
                    if patient_name:
                        logger.info("Patientenname aus Unterlagen erkannt: %s %s.",
                                    patient_name["anrede"], patient_name["initial"])
                        break

        if not patient_name:
            # v16 Audit: Frueher (sog. Bug-Fix #4) wurde hier ein Fallback gesetzt:
            #   patient_name = {"initial": "die Klientin/der Klient", ...}
            # Genau dieser String war die URSACHE der "die Klientin/der Klient"-
            # Kontamination im Output - er wurde via Replace-Logik in
            # build_system_prompt durch ALLE [Patient/in]-Platzhalter im Glossar
            # und in Few-Shots ersetzt. Loesung in v16: KEIN Fallback mehr.
            #
            # Statt dessen: patient_name bleibt None. Dann:
            #   - Replace-Logik in prompts.py ueberspringt die Substitution
            #   - [Patient/in]-Platzhalter in den Few-Shots bleiben stehen
            #   - Das Modell ersetzt sie kontextuell richtig (mit dem Namen
            #     den es aus dem Transkript ableitet, oder mit "Frau X."/
            #     einer plausiblen Bezeichnung).
            # Eval-Verifikation: dok-01 v15 zeigt korrekten Output mit
            # "Herr M." obwohl kein expliziter Patientenname gesetzt war.
            logger.warning(
                "Kein Patientenname ermittelbar (Workflow=%s) - Modell muss "
                "aus den Quellen ableiten, [Patient/in]-Platzhalter bleiben stehen",
                workflow,
            )

        # 5. Generieren – jede Variable hat genau eine Bedeutung
        # v18: prompt-Feld → workflow_instructions, neuer Parameter befund_vorlage.
        # `instructions` wurde oben aus workflow_instructions/prompt geholt und validiert.
        system = build_system_prompt(
            workflow=workflow,
            workflow_instructions=instructions,
            style_context=style_context,
            style_is_example=style_is_example,
            diagnosen=dx_list,
            patient_name=patient_name,
            word_limits=word_limits,
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
            # v18: custom_prompt wird in build_user_content nicht mehr verwendet
            # (Workflow-Anweisungen leben jetzt im System-Prompt). Parameter
            # bleibt fuer Backwards-Compat in der Signatur.
            patient_name=patient_name,
        )
        # v13: max_tokens kommt aus dem zentralen WORKFLOWS-Modul.
        # Werte pro Workflow in app/core/workflows.py editierbar.
        from app.core.workflows import max_tokens_for
        max_tok = max_tokens_for(workflow, fallback=2048)
        from app.services.job_queue import perf_logger

        phase_times["extraction"] = _t.time() - _ex_t0
        if "extraction" in bands:
            job.set_progress(bands["extraction"][1], "Dokument-Extraktion")

        # Cancel-Check nach Dokument-Extraktion
        if job._cancel_requested:
            raise RuntimeError("__CANCELLED__")

        lb = bands["llm"]
        # v13: erwartete Output-Tokens kommen aus dem zentralen WORKFLOWS-Modul.
        from app.core.workflows import expected_tokens_for
        expected_tok = expected_tokens_for(workflow, fallback=1500)
        def _on_tok(n):
            pct = lb[0] + (lb[1] - lb[0]) * min(1.0, n / expected_tok)
            job.set_progress(int(pct), "KI-Generierung", f"{n} Wörter")

        # Cancel-Check vor LLM-Generierung (zweitteuerster Schritt)
        if job._cancel_requested:
            raise RuntimeError("__CANCELLED__")

        job.set_progress(lb[0], "KI-Generierung")
        _t0 = _t.time()

        # ── v16: Postprocessor-Parameter berechnen ──────────────────────────
        # max_words: harte Obergrenze fuer den Output (style-derived).
        # Wenn das Modell das Limit ueberschreitet, schneidet
        # postprocess_output an einer Satzgrenze ab.
        # Verwendet das schon weiter oben berechnete word_limits[1] - kein
        # zweiter Aufruf von derive_word_limits noetig.
        v16_max_words = word_limits[1] if word_limits else None

        # expected_keywords: wichtige Source-Begriffe (Trennung, ADS, F33.1 etc.)
        # die im Output vorhanden sein muessten. Wenn nicht: Warning im Log.
        v16_expected_keywords: list[str] = []
        try:
            from app.services.postprocessing import extract_likely_keywords
            _src_combined = " ".join(filter(None, [
                transkript_text or "",
                selbstauskunft_text or "",
                vorbefunde_text or "",
                verlaufsdoku_text or "",
                antragsvorlage_text or "",
                vorantrag_text or "",
            ]))
            v16_expected_keywords = extract_likely_keywords(_src_combined)
            if v16_expected_keywords:
                logger.info(
                    "v16 Keyword-Check: erwarte %d Source-Keywords im Output: %s",
                    len(v16_expected_keywords), v16_expected_keywords,
                )
        except ImportError:
            pass
        except Exception as _e:
            logger.warning("Keyword-Extraktion fehlgeschlagen: %s", _e)

        # Anamnese: ZWEI sequenzielle LLM-Calls (Anamnese + Befund separat)
        # Vorteil: zuverlaessige Trennung ohne Marker, fokussierte Prompts
        if workflow == "anamnese":
            # Call 1: Anamnese-Fließtext (max ~60% des Token-Budgets)
            anamnese_max_tok = int(max_tok * 0.6)
            def _on_tok_a(n):
                # Erste Haelfte des LLM-Bands fuer Anamnese
                mid = lb[0] + (lb[1] - lb[0]) * 0.5
                pct = lb[0] + (mid - lb[0]) * min(1.0, n / (expected_tok * 0.6))
                job.set_progress(int(pct), "KI-Generierung", f"Anamnese: {n} Wörter")
            _log_prompt(job.job_id, workflow, "anamnese", system, user)
            result_a = await generate_text(system, user, max_tokens=anamnese_max_tok,
                                            model=model, workflow=workflow, on_progress=_on_tok_a,
                                            max_words=v16_max_words,
                                            expected_keywords=v16_expected_keywords)
            anamnese_text = (result_a.get("text") or "").strip()

            # Cancel-Check zwischen den Calls
            if job._cancel_requested:
                raise RuntimeError("__CANCELLED__")

            # Call 2: Befund (mit eigenem Prompt + Anamnese als zusaetzlichem Kontext)
            # v18: build_system_prompt uebernimmt die Vorlage-Substitution.
            # befund_vorlage kommt aus dem Frontend (Form-Feld), Default = BEFUND_VORLAGE.
            befund_system = build_system_prompt(
                workflow="befund",
                workflow_instructions="",  # Befund hat keinen Frontend-Auftragsteil
                diagnosen=dx_list,
                befund_vorlage=befund_vorlage,
            )
            # User-Content fuer Befund: Selbstauskunft + Vorbefunde + die generierte Anamnese
            befund_user_parts = []
            if selbstauskunft_text:
                befund_user_parts.append(f"SELBSTAUSKUNFT DES PATIENTEN:\n{selbstauskunft_text}")
            if vorbefunde_text:
                befund_user_parts.append(f"VORBEFUNDE:\n{vorbefunde_text}")
            if transkript_text:
                befund_user_parts.append(f"AUFNAHMEGESPRÄCH (Transkript):\n{transkript_text}")
            befund_user_parts.append(f"BEREITS GENERIERTE ANAMNESE (als Kontext):\n{anamnese_text}")
            befund_user_parts.append("\nErstelle nun den psychopathologischen Befund.")
            befund_user = "\n\n".join(befund_user_parts)

            befund_max_tok = int(max_tok * 0.5)
            def _on_tok_b(n):
                # Zweite Haelfte des LLM-Bands fuer Befund
                mid = lb[0] + (lb[1] - lb[0]) * 0.5
                pct = mid + (lb[1] - mid) * min(1.0, n / (expected_tok * 0.4))
                job.set_progress(int(pct), "KI-Generierung", f"Befund: {n} Wörter")
            _log_prompt(job.job_id, workflow, "befund", befund_system, befund_user)
            # Befund: keine max_words-Cap (Format ist fix), aber Keyword-Check sinnvoll
            result_b = await generate_text(befund_system, befund_user, max_tokens=befund_max_tok,
                                            model=model, workflow="befund", on_progress=_on_tok_b,
                                            expected_keywords=v16_expected_keywords)
            befund_text_generated = (result_b.get("text") or "").strip()

            # Direkt zwei separate Felder zurueckgeben — keine Marker noetig
            result = {
                "text": anamnese_text,
                "befund_text": befund_text_generated,
                "transcript": result_a.get("transcript") or result_b.get("transcript"),
                "model_used": result_a.get("model_used"),
            }
        else:
            _log_prompt(job.job_id, workflow, workflow, system, user)
            result = await generate_text(system, user, max_tokens=max_tok, model=model,
                                          workflow=workflow, on_progress=_on_tok,
                                          max_words=v16_max_words,
                                          expected_keywords=v16_expected_keywords)

        phase_times["llm"] = _t.time() - _t0

        try:
            perf_logger.info(_j.dumps({
                "workflow": workflow,
                "phases": phase_times,
                "has_audio": _has_audio,
                "has_docs": _has_docs,
            }))
        except Exception:
            pass
        raw = result["text"] or ""

        # Platzhalter-Substitution: "[Patient/in]" etc. durch echten Namen ersetzen.
        # Das Modell kopiert Platzhalter aus Few-Shot-Beispielen manchmal in den Output,
        # obwohl der Name im System-Prompt schon substituiert wurde.
        if patient_name:
            raw = substitute_patient_placeholders(raw, patient_name)
            if result.get("befund_text"):
                result["befund_text"] = substitute_patient_placeholders(
                    result["befund_text"], patient_name
                )
            if result.get("akut_text"):
                result["akut_text"] = substitute_patient_placeholders(
                    result["akut_text"], patient_name
                )

        # Anamnese-Workflow: Befund kommt bereits separat aus dem zweiten LLM-Call.
        # Fuer alle anderen Workflows: kein Befund/Akut-Splitting noetig.
        if workflow == "anamnese":
            anamnese_part = raw
            befund_part = result.get("befund_text") or None
            akut_part = result.get("akut_text") or None
        else:
            anamnese_part = raw
            befund_part = None
            akut_part = None

        logger.info(
            "Job %s _run result: raw=%d anamnese=%d befund=%s akut=%s",
            job.job_id, len(raw),
            len(anamnese_part) if anamnese_part else 0,
            len(befund_part) if befund_part else 0,
            len(akut_part) if akut_part else 0,
        )

        return {
            "text":        anamnese_part,
            "befund_text": befund_part,
            "akut_text":   akut_part,
            "transcript":  transkript_text or None,
            "model_used":  result["model_used"],
            "style_info":  style_info,
            # P2: OCR-Validator-Warnungen ans Frontend durchreichen.
            # UI kann eine Warnbanner anzeigen wenn diese Liste nicht leer ist.
            "ocr_warnings": _ocr_warnings if _ocr_warnings else None,
        }

    background_tasks.add_task(job_queue.run_job, job, _run())
    return {"job_id": job.job_id, "status": "pending"}
