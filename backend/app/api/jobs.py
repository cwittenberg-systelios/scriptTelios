"""
GET  /api/jobs/{job_id}   – Job-Status abfragen
GET  /api/jobs            – Alle Jobs auflisten (optional)
"""
import logging
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, Depends
from typing import Annotated, Optional

from app.core.config import settings
from app.core.auth import get_current_user
from app.core.workflows import WorkflowLiteral
from app.services.generation_pipeline import (
    PipelineInput, UploadBundle, normalize_geschlecht, parse_dx_list, run_generation,
)
from app.services.job_queue import job_queue, JobStatus
from app.services.quality_check import sanitize_for_repair_prompt
from app.services.repair import (
    _resolve_parent_job, _build_repair_context, _run_repair_coroutine,
)

# v19.21 (S6c): Pipeline-, Stage-1-, Repair- und Prompt-Log-Logik leben jetzt in
# app/services/ (generation_pipeline, stage1, repair, prompt_log). Die
# folgenden Namen werden fuer Tests/Skripte, die sie von hier importieren
# (oder per patch("app.api.jobs.<n>") ersetzen), weiterhin re-exportiert.
from app.services.generation_pipeline import (  # noqa: F401
    _missing_source_error, _apply_input_budget_guard, _run_ism_generation,
    _resolve_transcript, _extract_sources, _resolve_style, _resolve_patient_and_gates,
    _build_prompts, _generate, _finalize,
)
from app.services.prompt_log import (  # noqa: F401
    _prompt_logger, _setup_prompt_logger, _therapeut_for_log, _log_prompt, _log_output,
)
from app.services.repair import (  # noqa: F401
    _resolve_repair_sources, _split_anamnese_concat, _text_similarity,
    _is_repair_noop, _repair_wants_addition,
)
from app.services.stage1 import (  # noqa: F401
    _STAGE1_WORKFLOWS, _STAGE1_MIN_WORDS, _TRANSCRIPT_STAGE1_WORKFLOWS,
    _stage1_audit_bundle, _run_verlauf_stage1, _run_transcript_stage1,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_setup_prompt_logger()




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
    # job.status ist bereits str (JobStatus(str,Enum) wird beim Set per .value
    # gespeichert, siehe job_queue.py:119 + alle Mutationen). Kein .value-Aufruf.
    return {
        "job_id":    job_id,
        "cancelled": cancelled,
        "status":    job.status,
    }


@router.delete("/jobs/{job_id}/permanent")
async def delete_job_permanent(
    job_id: str,
    current_user: str = Depends(get_current_user),
):
    """Sprint B (Multi-Job-Liste P1): endgueltiges Loeschen aus Cache + DB.

    Nur erlaubt fuer terminale Stati (done, error, cancelled). Pending/running
    Jobs muessen zuerst per DELETE /api/jobs/{id} abgebrochen werden.

    Antworten:
      200 + {"job_id", "deleted": True, "reason": None}  - Erfolg
      404                                                - Job nicht gefunden
      409                                                - Job laeuft noch
    """
    try:
        result = await job_queue.delete_job_permanent(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden") from None

    if not result["deleted"]:
        raise HTTPException(status_code=409, detail=result["reason"])

    return result


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
    # Transkript direkt aus DB laden (get_job_from_db liefert nur has_transcript)
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
    except Exception as e:
        raise HTTPException(status_code=404, detail="Transkript nicht verfuegbar") from e


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
async def list_jobs(
    current_user: str = Depends(get_current_user),
    workflow: Optional[WorkflowLiteral] = Query(
        None, description="Optional: nur Jobs eines Workflow-Typs zurueckgeben."
    ),
    limit: int = Query(
        50, ge=1, le=500,
        description="Maximale Anzahl zurueckgegebener Jobs (1-500, Default 50).",
    ),
):
    """Listet Jobs des authentifizierten Therapeuten auf, neueste zuerst.

    Sprint B (Multi-Job-Liste P1):
      - Quelle ist die DB (damit auch aus dem Cache evictete Jobs sichtbar
        bleiben, z.B. nach uvicorn-Restart oder bei >500 alten Jobs)
      - Filter therapeut_id implizit aus dem validierten Auth-Header
      - Filter workflow optional als Query-Param
      - Cache wird fuer Jobs die parallel laufen bevorzugt (Live-Progress)
    """
    return await job_queue.list_filtered(
        workflow=workflow,
        therapeut_id=current_user,
        limit=limit,
    )


# ── v19 Phase C: Therapeut-in-the-Loop Repair ──────────────────────────────────
#
# Zwei Endpoints + ein schlanker Coroutine-Builder:
#   POST /jobs/{id}/repair/preview - baut den final_prompt, gibt ihn ans UI
#   POST /jobs/{id}/repair         - startet den Repair-Job (Background-Task)
#   _run_repair_coroutine          - der eigentliche LLM-Call (eine Phase, kein
#                                    Transcribing, kein PDF-Extract, kein
#                                    Stage 1 - alle Inputs stehen schon im
#                                    final_prompt)
#
# Datenmodell-Recap (siehe app/models/db.py + scripts/schema.sql):
#   - Repair-Job ist ein vollwertiger Eintrag in der jobs-Tabelle.
#   - jobs.parent_job_id    -> direkter Vorgaenger (FK ohne CASCADE)
#   - jobs.repair_input_json -> Audit-Snapshot der Therapeut-Eingaben

from app.models.schemas import (
    QualityIssueResponse,
    RepairPreviewRequest,
    RepairPreviewResponse,
    RepairRequest,
    RepairResponse,
)
from app.services.quality_check import combined_result_text


@router.post("/jobs/{job_id}/repair/preview", response_model=RepairPreviewResponse)
async def repair_preview(
    job_id: str,
    req: RepairPreviewRequest,
    current_user: str = Depends(get_current_user),
):
    """Baut den final_prompt fuer den Repair-Job, sendet ihn ans UI.

    Kein Side-Effect: erzeugt keinen Job, schreibt nicht in DB. Reine
    Vorschau-Funktion, damit der Therapeut den Prompt vor Versand pruefen
    kann."""
    parent = await _resolve_parent_job(job_id)
    # v19.3: Kontext-Quellen laden (Verlauf + Transkript). Diese gehen NICHT
    # ueber die normale Job-API raus (Datenschutz), aber der Repair-Prompt
    # bekommt sie eingebaut.
    repair_sources = await job_queue.get_repair_context(job_id)
    workflow, _original, accepted_issues, final_prompt = _build_repair_context(
        parent, req.accepted_issue_codes, req.user_hint,
        repair_sources=repair_sources,
    )
    return RepairPreviewResponse(
        final_prompt=final_prompt,
        accepted_issues=[
            QualityIssueResponse(
                code=i.code, severity=i.severity, message=i.message,
                repair_hint=i.repair_hint, code_detail=i.code_detail or {},
            ) for i in accepted_issues
        ],
        user_hint_sanitized=sanitize_for_repair_prompt(req.user_hint),
    )


@router.post("/jobs/{job_id}/repair", response_model=RepairResponse)
async def repair_execute(
    job_id: str,
    req: RepairRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
):
    """Startet einen Repair-Job. Synchroner Response: Job-ID, dann normal
    via /jobs/{repair_id} pollen."""
    parent = await _resolve_parent_job(job_id)

    # v19.3: Repair-Kontext-Quellen laden. Beide Pfade (custom + nicht-custom)
    # nutzen den Kontext - bei custom_final_prompt zwar nicht fuer den Prompt
    # selbst (User hat eigenen geschrieben), aber wir validieren trotzdem dass
    # parent_job auflesbar ist.
    repair_sources = await job_queue.get_repair_context(job_id)

    # custom_final_prompt erlaubt dem UI, den Preview-Prompt zu editieren
    # bevor er ans Modell geht. Wenn gesetzt: damit den Re-Build ueberspringen
    # und exakt diesen Prompt verwenden.
    if req.custom_final_prompt:
        # accepted_issue_codes + user_hint trotzdem validieren (Audit),
        # aber den Prompt selbst nicht neu bauen.
        workflow = parent.get("workflow")
        if not workflow:
            raise HTTPException(400, "Parent-Job hat keinen Workflow")
        if parent.get("status") != "done":
            raise HTTPException(
                400, "Repair nur fuer abgeschlossene Jobs (status=done)",
            )
        final_prompt = req.custom_final_prompt
        custom_used = True
        _original = combined_result_text(
            workflow, parent.get("result_text") or "", parent.get("befund_text") or "",
        )
    else:
        workflow, _original, _accepted, final_prompt = _build_repair_context(
            parent, req.accepted_issue_codes, req.user_hint,
            repair_sources=repair_sources,
        )
        custom_used = False

    repair_input = {
        "accepted_issue_codes":     list(req.accepted_issue_codes),
        "user_hint":                sanitize_for_repair_prompt(req.user_hint or ""),
        "final_prompt":             final_prompt,
        "custom_final_prompt_used": custom_used,
    }

    # v19.8 (S6b): Identitaets-Kontext vom Parent auf den Repair-Job
    # uebernehmen. Ohne das lief der QualityCheck auf dem REPARIERTEN Text
    # mit patient_name=None - PATIENT_INITIAL_MISMATCH, GENDER_MISMATCH und
    # DATENSCHUTZ_NAME_LEAK waren auf Repair-Output stumm, man konnte also
    # nie verifizieren dass der Repair den Namen wirklich korrigiert hat.
    # Quelle 1: in-process Attribut auf dem gecachten Parent-JobState
    #   (gesetzt in create_generate_job, traegt gender/gender_source).
    # Quelle 2 (Pod-Neustart, Cache leer): Rekonstruktion aus dem
    #   persistierten jobs.patient_kuerzel-Display-String ("Frau K.") via
    #   parse_explicit_patient_name + Anrede->Gender-Mapping. Verliert
    #   gender_source="document"-Faelle ohne Kuerzel - dokumentierte Luecke,
    #   besser als gar kein Check.
    _cached_parent = job_queue.get_job(job_id)
    _parent_pn = getattr(_cached_parent, "patient_name", None) if _cached_parent else None
    _parent_fokus = getattr(_cached_parent, "fokus_themen", None) if _cached_parent else None
    if not _parent_pn:
        _pk = parent.get("patient_kuerzel")
        if _pk:
            from app.services.extraction import parse_explicit_patient_name
            _parent_pn = parse_explicit_patient_name(_pk)
            if _parent_pn:
                _g = {"Frau": "w", "Herr": "m"}.get(_parent_pn.get("anrede") or "")
                _parent_pn["gender"] = _g
                _parent_pn["gender_source"] = "explicit" if _g else None
                logger.info(
                    "Repair %s: patient_name aus patient_kuerzel rekonstruiert "
                    "(initial=%s gender=%s)", job_id[:8],
                    _parent_pn.get("initial"), _g,
                )
    # Audit-Snapshot: womit lief der QC des Repair-Jobs (persistiert in
    # jobs.repair_input_json).
    repair_input["patient_identity"] = _parent_pn

    job = job_queue.create_repair_job(
        parent_job_id=job_id,
        workflow=workflow,
        description=f"Repair von {job_id[:8]}",
        repair_input=repair_input,
    )
    # In-process Kontext fuer den QualityCheck in run_job (getattr-Lesepfad
    # in job_queue.py - identisch zum normalen Generate-Pfad).
    job.patient_name = _parent_pn
    job.fokus_themen = _parent_fokus

    # v19.14a: Quellentreue-Netz fuer den Repair-Output. Der Voll-Repair
    # schreibt den GESAMTEN Text neu, lief aber bis v19.13 OHNE
    # SOURCE_FIDELITY-Check (source_*-Felder auf Repair-Jobs = None).
    # Parent-Quellen als in-process Attribut mitgeben - bewusst NUR die
    # Roh-Versionen (result_transcript + source_*-Extrakte), KEINE
    # Stage-1-Synthesen (verlauf_summary/transcript_summary): Verdichtung
    # erzeugt Falsch-Positive im Quellentreue-Check (v19.5-Learning).
    # D1: source_prozessreflexion_text ist dabei - analog Generate-Pfad
    # (v19.13), sonst flaggt der Check den Reflexionsteil eines
    # P4-Repairs als unbelegt.
    # D3: gilt bewusst auch fuer custom_final_prompt-Repairs - der QC ist
    # beratend, nicht blockierend, und ein Hinweis auf unbelegten Inhalt
    # ist gerade bei freiem Prompt sinnvoll.
    # Nicht persistiert (PII nicht auf die Repair-Zeile duplizieren);
    # _qc_fidelity_source() in job_queue.py liest es per getattr.
    if repair_sources:
        job.qc_source_text = "\n\n".join(
            s for s in (
                repair_sources.get("result_transcript"),
                repair_sources.get("source_verlauf_text"),
                repair_sources.get("source_antragsvorlage_text"),
                repair_sources.get("source_vorantrag_text"),
                repair_sources.get("source_prozessreflexion_text"),
            ) if s and s.strip()
        ) or None

    # Modell: vererben aus Parent (Konsistenz: Repair laeuft mit demselben
    # Modell wie das Original). Kann durch Settings ueberschrieben werden.
    # v19.2.2: parent.model_used speichert den Display-String "ollama/qwen3:32b"
    # (siehe llm.py - bewusstes Format fuer UI/Audit, durch
    # test_generate_text_erfolgreich abgesichert). Beim Weiterreichen an
    # generate_text() muss das "ollama/"-Praefix gestrippt werden, sonst
    # antwortet Ollama mit 404 ("model 'ollama/qwen3:32b' not found").
    raw_parent_model = parent.get("model_used")
    if raw_parent_model and raw_parent_model.startswith("ollama/"):
        model_override = raw_parent_model[len("ollama/"):]
    else:
        model_override = raw_parent_model

    async def _coro():
        return await _run_repair_coroutine(
            job, workflow, final_prompt, model_override=model_override,
            original_text=_original,
            user_hint=req.user_hint or "",
            accepted_codes=list(req.accepted_issue_codes or []),
        )

    background_tasks.add_task(job_queue.run_job, job, _coro())

    return RepairResponse(
        repair_job_id=job.job_id,
        parent_job_id=job_id,
        workflow=workflow,
    )


# ── R2 (2026-07-01): Stage-1-Phasen aus _run() extrahiert ─────────────────────
#
# create_generate_job/_run war eine >1900-Zeilen-Closure. Die beiden Stage-1-
# Bloecke (Verlauf- und Transkript-Verdichtung) sind in sich geschlossen und
# leben jetzt als Modul-Funktionen mit expliziten Ein-/Ausgaben. Verhalten
# ist 1:1 identisch (gleiche Logs, gleiche Audit-Shapes, gleiche Fallbacks);
# das 6-fach duplizierte Audit-Dict baut _stage1_audit_bundle().


# ── v19.18 (PX): ISM-Fragebogen-Generierung ──────────────────────────────────


# ── Asynchrone Generierung ────────────────────────────────────────────────────


    # 3. Stilprofil
    # v13: derive_word_limits wird nicht mehr direkt aufgerufen; resolve_length_anchor
    # weiter unten kapselt das. Hier nur noch Stiltexte einsammeln.


    # v13: Längenanker via zentralen Dispatcher resolve_length_anchor.
    # Ersetzt den bisherigen Code-Pfad mit derive_word_limits + akutantrag-Cap +
    # manuelles Workflow-Default-Fallback. Die Funktion ist nun die EINZIGE Stelle,
    # die Längenanweisungen für den Prompt produziert.
    #
    # Resolution chain:
    #   1. ≥1 Stilvorlage mit ≥200w → derive aus Stilvorlagen (±30%, gefloort/gecapt)
    #   2. Stilvorlage(n) <200w (Stichworte) → Workflow-Default (Floor)
    #   3. Keine Stilvorlage → Workflow-Default
    #
    # Der akutantrag-Spezialfall (Volltext-Stilvorlagen) ist durch den
    # Ceiling-Multiplier (×1.4 statt ×1.87) automatisch abgedeckt.


    # v19.8: KLIENT-GESCHLECHT-Hinweis backend-seitig anhaengen, wenn das
    # UI-Feld gesetzt ist und das Frontend ihn NICHT schon eingebaut hat
    # (P1/P2 haengen ihn selbst an - Duplikat-Guard ueber den Marker-String;
    # P2b/P3/P3b/P4 taten das bisher nie -> genau deren Luecke schliesst das).
    # v19.8.1 FIX: NICHT `instructions` selbst zuweisen - die Zuweisung in
    # dieser Closure macht `instructions` fuer die GESAMTE _run()-Funktion
    # zu einer lokalen Variable (Python-Scoping), und jeder Lesezugriff
    # davor wirft UnboundLocalError ("cannot access local variable
    # 'instructions' where it is not associated with a value") - ALLE
    # Workflows waren betroffen. Lokaler Alias statt Shadowing.


    # v13: max_tokens kommt aus dem zentralen WORKFLOWS-Modul.
    # Werte pro Workflow in app/core/workflows.py editierbar.


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
    # v19.8 (Identitaets-Guard): strukturiertes Geschlecht aus dem UI-Toggle,
    # unabhaengig vom Kuerzel. Vorher lebte das Geschlecht nur implizit in der
    # Anrede des patientenname-Strings - war das Kuerzel leer, ging die
    # Information komplett verloren (patientName=null im Frontend-Gate).
    # Werte: "w" | "m" | "auto"/None (auto = aus Quellen ableiten, wie bisher).
    geschlecht:       Annotated[Optional[str], Form(description="Klient-Geschlecht aus dem UI: 'w'|'m'|'auto'. Unabhaengig vom Kuerzel; ueberstimmt die Anrede-Ableitung aus Dokumenten.")] = None,
    current_user:     str = Depends(get_current_user),
    diagnosen:        Annotated[Optional[str], Form()] = None,
    transcript:       Annotated[Optional[str], Form()] = None,
    p0_recording_id:  Annotated[Optional[str], Form(description="P0-Recording-ID: Transkript wird aus DB geholt, ggf. priorisiert transkribiert.")] = None,
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
    prozessreflexion: Optional[UploadFile] = File(None, description="P4: Abschlussreflexion des Klienten (.pdf/.docx, optional)"),
    style_file:       Optional[UploadFile] = File(None, description="Stilvorlage (Beispieltext)"),
    # v19.16 (G0): Das Frontend sendet .txt/.docx-Transkripte seit jeher als
    # 'transcript_file' - der Endpoint kannte das Feld nicht, FastAPI verwarf
    # es stillschweigend. P1/P2 mit Transkript-Datei liefen dadurch ohne
    # Quelle (Konfabulationspfad, siehe Job 58db7006 vom 2026-08-04).
    transcript_file:  Optional[UploadFile] = File(None, description="Transkript-Datei (.txt/.docx) als Gespraechsquelle fuer P1/P2"),
    # v19.18 (PX): gewuenschte Itemanzahl fuer den ISM-Fragebogen (4-12,
    # Default 6). Nur fuer workflow=ism_fragebogen relevant; andere
    # Workflows ignorieren das Feld.
    ism_n_items:      Annotated[Optional[int], Form(description="ISM-Fragebogen: Anzahl der Items (4-12, Default 6).")] = None,
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
                                + prozessreflexion (v19.13, optional)
    """
    # K1: Therapeut-ID aus validiertem Auth-Header
    therapeut_id = current_user

    # v18: Workflow-Anweisungen sind Pflicht. Backwards-Compat: alter Name 'prompt'.
    # Architekturvorgabe: Wenn das Frontend fuer einen Workflow KEINE
    # Anweisungen schickt, lehnt das Backend den Job ab. Es gibt KEINEN
    # impliziten Fallback auf den Default - sonst koennte ein verbuggtes
    # Frontend unbemerkt mit leerem Auftrag laufen.
    instructions = workflow_instructions or prompt

    # v19.8: geschlecht-Whitelist (normalize_geschlecht): alles ausser "w"/"m"
    # -> None -> Verhalten wie bisher (aus Quellen ableiten).
    geschlecht_norm = normalize_geschlecht(geschlecht)

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

    # Workflow-Modell-Routing (2026-07-03): Wenn das Frontend KEIN Modell
    # explizit waehlt, den workflow-spezifischen Default aus der Konfiguration
    # anwenden (gemma4:31b fuer Doku/Anamnese/EB, mistral-small3.2 fuer
    # Antraege). Explizite Frontend-Wahl hat Vorrang; ohne Map-Eintrag faellt
    # model_for_workflow() auf OLLAMA_MODEL zurueck. Das aufgeloeste Modell
    # fliesst ueber die bestehende model-Variable in die gesamte _run-Kette.
    #
    # v19.5.3: Das aufgeloeste Modell zusaetzlich gegen die real geladenen
    # Ollama-Modelle validieren. Ein vom Client geschicktes veraltetes/nicht
    # gepulltes Modell (z.B. stale localStorage 'systelios_model=qwen3:32b')
    # wurde bisher ungeprueft an den Haupt-Call gereicht -> Ollama-404 killte
    # den Job. ensure_generation_model faellt in dem Fall auf den Workflow-
    # Default (bzw. ein garantiert geladenes Modell) zurueck.
    from app.services.llm import ensure_generation_model
    model = await ensure_generation_model(model, workflow)

    # Dateien sofort einlesen (vor Background-Task, da UploadFile nicht
    # thread-safe) und alle Eingaben buendeln (v19.21 S6a: PipelineInput).
    uploads = await UploadBundle.read(
        audio=audio, selbstauskunft=selbstauskunft, vorbefunde=vorbefunde,
        verlaufsdoku=verlaufsdoku, antragsvorlage=antragsvorlage, vorantrag=vorantrag,
        prozessreflexion=prozessreflexion, style_file=style_file, transcript_file=transcript_file,
    )
    ctx = PipelineInput(
        workflow=workflow,
        instructions=instructions,
        model=model,
        therapeut_id=therapeut_id,
        befund_vorlage=befund_vorlage,
        patientenname=patientenname,
        geschlecht_norm=geschlecht_norm,
        transcript=transcript,
        p0_recording_id=p0_recording_id,
        bullets=bullets,
        style_text=style_text,
        dx_list=parse_dx_list(diagnosen),
        ism_n_items=ism_n_items,
        uploads=uploads,
    )

    # Job anlegen
    job = job_queue.create_job(
        workflow=workflow,
        description=f"Workflow: {workflow}" + (f" | Audio: {uploads.audio_name}" if uploads.audio_name else ""),
        therapeut_id=therapeut_id,
        patient_kuerzel=ctx.patient_kuerzel,
    )

    # Performance-Tracking: welche Inputs hat dieser Job?
    job.input_meta = ctx.input_meta()

    background_tasks.add_task(job_queue.run_job, job, run_generation(ctx, job))
    return {"job_id": job.job_id, "status": "pending"}
