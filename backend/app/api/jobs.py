"""
GET  /api/jobs/{job_id}   – Job-Status abfragen
GET  /api/jobs            – Alle Jobs auflisten (optional)
"""
import logging
import time as _t
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, Depends
from typing import Annotated, Optional

from app.core.config import settings
from app.core.auth import get_current_user
from app.core.files import size_class as _size_class
# WorkflowLiteral wird fuer die Form-Validierung des workflow-Parameters
# gebraucht (s.u. Zeile ~266). Kommt zentral aus app.core.workflows;
# schemas.py re-exportiert ihn, aber wir importieren direkt aus core damit
# der Import-Pfad eindeutig zur Quelle der Wahrheit zeigt.
from app.core.workflows import WorkflowLiteral
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
from app.services.verlauf_summary import summarize_verlauf
from app.services.transcript_summary import summarize_transcript
from app.services.document_summary import summarize_document
from app.services.prompts import build_system_prompt, build_user_content, split_style_examples
from app.services.quality_check import (
    QualityIssue,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    build_repair_prompt,
    deserialize_issues,
    sanitize_for_repair_prompt,
)
from app.services.staging import (
    STAGE1_VERLAUF_WORKFLOWS,
    STAGE1_VERLAUF_MIN_WORDS,
    STAGE1_TRANSCRIPT_WORKFLOWS,
    should_run_verlauf_stage1,
    should_run_transcript_stage1,
    verlauf_stage1_skip_reason,
    transcript_stage1_skip_reason,
    compute_input_word_budget,
    plan_source_compression,
)
import app.services.transcription as _transcription

router = APIRouter()
logger = logging.getLogger(__name__)

# v19.2 / v19.3: Stage-1-Pipeline-Konfiguration kommt jetzt aus
# app.services.staging - einzige Quelle der Wahrheit fuer Workflow-Whitelist
# und Min-Wortzahl. Test: tests/unit/test_staging.py.
# Backwards-kompatibel re-exportiert:
_STAGE1_WORKFLOWS = STAGE1_VERLAUF_WORKFLOWS
_STAGE1_MIN_WORDS = STAGE1_VERLAUF_MIN_WORDS
_TRANSCRIPT_STAGE1_WORKFLOWS = STAGE1_TRANSCRIPT_WORKFLOWS
# _TRANSCRIPT_STAGE1_MIN_WORDS entfernt (DRY-Fix 2026-07-01):
# settings.TRANSCRIPT_STAGE1_MIN_WORDS ist die einzige Quelle.

# Separater LLM-IO-Logger – schreibt vollständige System/User-Prompts UND die
# erzeugten Outputs in prompts.log. Zweck: manuelle Qualitätsinspektion
# (Prompt -> Output paarweise) ohne den Haupt-Log zu fluten.
# v19.4: täglich rotierend (TimedRotatingFileHandler, Mitternacht), damit die
# Datei bei viel Text nicht unbegrenzt waechst.
_prompt_logger = logging.getLogger("systelios.prompts")

# Wieviele Tages-Archive von prompts.log aufbewahrt werden (prompts.log.YYYY-MM-DD).
_PROMPT_LOG_BACKUP_DAYS = 14


def _setup_prompt_logger() -> None:
    """Richtet den LLM-IO-Logger ein (einmalig beim Import). Tagesrotation."""
    if _prompt_logger.handlers:
        return
    _prompt_logger.setLevel(logging.DEBUG)
    _prompt_logger.propagate = False
    import os
    from pathlib import Path as _Path2
    from logging.handlers import TimedRotatingFileHandler
    log_dir = _Path2(os.environ.get("LOG_FILE", "/workspace/systelios.log")).parent
    prompt_file = log_dir / "prompts.log"
    try:
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            str(prompt_file),
            when="midnight",
            interval=1,
            backupCount=_PROMPT_LOG_BACKUP_DAYS,
            encoding="utf-8",
            utc=False,
        )
        # Rotierte Dateien als prompts.log.YYYY-MM-DD ablegen.
        handler.suffix = "%Y-%m-%d"
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        _prompt_logger.addHandler(handler)
    except Exception as e:
        logger.warning("LLM-IO-Logger konnte nicht eingerichtet werden: %s", e)


_setup_prompt_logger()


def _missing_source_error(
    workflow: str,
    *,
    transkript_text: str = "",
    selbstauskunft_text: str = "",
    vorbefunde_text: str = "",
    bullets: str = "",
    transcript_failure_reason: "str | None" = None,
) -> "str | None":
    """v19.16 (G1): Quellen-Gate gegen Konfabulation.

    Gibt eine nutzerverstaendliche Fehlermeldung zurueck, wenn fuer den
    Workflow KEINE inhaltliche Quelle vorliegt - sonst None.

    Hintergrund (Job 58db7006, 2026-08-04): Ein P1-Job lief nach
    wait_for_transcript-Timeout mit leerem Transkript weiter; der Prompt
    enthielt keinen Quellblock und das Modell erfand ein vollstaendiges,
    klinisch plausibel klingendes Dokument aus dem Stil-Referenzwissen.
    Konfabulierte klinische Inhalte sind nie verwertbar - der Job wird
    abgebrochen, bevor ein solches Dokument ueberhaupt entsteht (D1).

    Nur P1 (dokumentation) und P2 (anamnese) haben das Transkript bzw.
    Klienten-Dokumente als inhaltliche Primaerquellen; P2b/P3/P3b/P4 haben
    eigene Pflichtquellen mit eigener Validierung.
    """
    def _has(t: str) -> bool:
        return bool(t and t.strip())

    reason_suffix = f" {transcript_failure_reason}" if transcript_failure_reason else ""

    if workflow == "dokumentation":
        if not _has(transkript_text) and not _has(bullets):
            return (
                "Kein Gespraechsinhalt verfuegbar - die Dokumentation wurde "
                "NICHT erstellt, um ein erfundenes Dokument zu verhindern."
                + reason_suffix
            )
        return None

    if workflow == "anamnese":
        if not (_has(transkript_text) or _has(selbstauskunft_text)
                or _has(vorbefunde_text)):
            return (
                "Keine verwertbare Quelle (Selbstauskunft, Vorbefunde oder "
                "Aufnahmegespraech) verfuegbar - die Anamnese wurde NICHT "
                "erstellt, um ein erfundenes Dokument zu verhindern."
                + reason_suffix
            )
        return None

    return None


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


def _log_output(job_id: str, workflow: str, call_label: str,
                output: str, telemetry: Optional[dict] = None) -> None:
    """
    Schreibt den ERZEUGTEN Output eines LLM-Calls in prompts.log — gepaart mit
    dem zugehoerigen _log_prompt-Eintrag (gleiche job_id + call_label).

    Damit steht im rotierenden Log Prompt UND Output zusammen, was die
    manuelle Qualitaetspruefung erlaubt (v19.4).
    """
    sep = "=" * 80
    out = output or ""
    tele = ""
    if telemetry:
        try:
            tele = (
                f"  [words={len(out.split())} "
                f"think_ratio={telemetry.get('think_ratio')} "
                f"tokens_hit_cap={telemetry.get('tokens_hit_cap')} "
                f"degraded={telemetry.get('degraded')} "
                f"input_truncated={telemetry.get('input_truncated')} "
                f"output_budget_reduced={telemetry.get('output_budget_reduced')}]"
            )
        except Exception:
            tele = ""
    _prompt_logger.debug(
        "\n%s\nJOB: %s  |  WORKFLOW: %s  |  CALL: %s  (OUTPUT)%s\n%s\n%s\n%s\n",
        sep, job_id, workflow, call_label, tele, sep, out, sep,
    )


# Anzeige-Labels fuer die Quellen im Budget-Guard (gehen in den Verdichter-Prompt).
_SOURCE_LABELS: dict[str, str] = {
    "transkript":     "Sitzungstranskript",
    "verlaufsdoku":   "Verlaufsdokumentation",
    "selbstauskunft": "Selbstauskunft des Patienten",
    "vorbefunde":     "Vorbefunde",
    "antragsvorlage": "Antragsvorlage",
    "vorantrag":      "Vorheriger Antrag",
    "prozessreflexion": "Prozessreflexion des Klienten",
}


async def _apply_input_budget_guard(
    *,
    workflow: str,
    system_prompt: str,
    patient_initial: Optional[str],
    sources: dict[str, dict],
) -> tuple[dict[str, str], dict]:
    """
    v19.4: Kombinierter Input-Budget-Guard.

    Prueft die SUMME aller Quellen gegen ein Wort-Budget (Output zuerst
    reserviert via max_tokens_for, exakte System-Prompt-Groesse abgezogen) und
    verdichtet die groessten noch-rohen Quellen quellentreu nach, bis der
    kombinierte Input passt — statt _sample_uniformly in llm.py das gesamte
    User-Content verlustbehaftet zerhacken zu lassen.

    sources: {label: {"text": str, "compressed": bool}}
    Returns: (updated_texts: {label: neuer_text}, audit: dict)
    """
    if not getattr(settings, "SOURCE_COMPRESSION_ENABLED", True):
        return {}, {"applied": False, "reason": "disabled"}

    budget_words = compute_input_word_budget(
        workflow, system_prompt_chars=len(system_prompt or "")
    )

    plan_input: list[dict] = []
    for label, meta in sources.items():
        text = (meta or {}).get("text") or ""
        if not text.strip():
            continue
        plan_input.append({
            "label":        label,
            "words":        len(text.split()),
            "compressed":   bool(meta.get("compressed")),
            "compressible": True,
        })

    total_before = sum(s["words"] for s in plan_input)
    plan = plan_source_compression(plan_input, budget_words)

    if not plan:
        return {}, {
            "applied":       False,
            "reason":        "within_budget",
            "budget_words":  budget_words,
            "total_words":   total_before,
        }

    logger.info(
        "Input-Budget-Guard: Summe %dw > Budget %dw -> Nachverdichtung: %s",
        total_before, budget_words, plan,
    )

    updated: dict[str, str] = {}
    compressions: list[dict] = []
    for label, target in plan.items():
        text = sources[label]["text"]
        try:
            res = await summarize_document(
                text,
                target_words=target,
                doc_label=_SOURCE_LABELS.get(label, label),
                workflow=workflow,
                patient_initial=patient_initial,
            )
            updated[label] = res["summary"]
            compressions.append({
                "label":         label,
                "raw_words":     res["raw_word_count"],
                "summary_words": res["summary_word_count"],
                "target_words":  target,
                "degraded":      res.get("degraded", False),
                "ok":            True,
            })
        except Exception as e:
            # Roh-Text bleibt; _sample_uniformly in llm.py greift als Notbremse.
            logger.warning(
                "Input-Budget-Guard: Nachverdichtung von '%s' fehlgeschlagen "
                "(%s) - Roh-Text bleibt", label, e,
            )
            compressions.append({"label": label, "ok": False, "error": str(e)[:200]})

    total_after = total_before
    for c in compressions:
        if c.get("ok"):
            total_after -= (c["raw_words"] - c["summary_words"])

    audit = {
        "applied":            True,
        "budget_words":       budget_words,
        "total_words_before": total_before,
        "total_words_after":  total_after,
        "compressions":       compressions,
    }
    return updated, audit


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
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")

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
from app.services.prompts import REPAIR_SYSTEM_PROMPT
from app.core.workflows import max_tokens_for


def _split_anamnese_concat(workflow: str, full_text: str) -> tuple[str, Optional[str]]:
    """Spiegelbild zu combined_result_text: trennt einen verketteten Anamnese-
    Output am ###BEFUND###-Marker. Fuer andere Workflows: kein Splitting.

    Returns (anamnese_or_full_text, befund_text_or_None).
    """
    if workflow != "anamnese" or "###BEFUND###" not in (full_text or ""):
        return (full_text or "", None)
    parts = full_text.split("###BEFUND###", 1)
    anamnese_part = parts[0].strip()
    befund_part = parts[1].strip() if len(parts) > 1 else ""
    return (anamnese_part, befund_part or None)


async def _resolve_parent_job(job_id: str) -> dict:
    """Laedt einen Job aus Cache oder DB. Wirft 404 bei Fehlen.

    Bewusst NICHT current_user-scoped - der Memory-Store ist global, und das
    Backend hat keinen multi-tenant-Filter. Das passt zum bisherigen Verhalten
    von get_job/cancel_job. Falls Multi-Tenancy spaeter kommt, hier ist die
    Stelle wo der Check rein muss."""
    cached = job_queue.get_job(job_id)
    if cached:
        return cached.to_dict()
    from_db = await job_queue.get_job_from_db(job_id)
    if from_db:
        return from_db
    raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden")


def _resolve_repair_sources(parent_context: dict) -> tuple[str, str, str]:
    """v19.3: Loest aus dem Parent-Job-Kontext die drei Quell-Texte
    fuer den Repair-Prompt auf.

    Hierarchie:
      Verlauf:        verlauf_summary_text (Synthese) → source_verlauf_text (Roh)
      Transkript:     transcript_summary_text (Synthese) → result_transcript (Roh)
      Patientendaten: source_antragsvorlage_text + source_vorantrag_text (beide
                      wenn vorhanden - relevant bei Folgeverlaengerung; sonst nur
                      eines davon).

    Synthese wird bevorzugt weil:
      - klein (~800-2500w), kontextfreundlich
      - schon verdichtet, optimal als Faktengrundlage
    Roh-Version greift wenn keine Synthese vorhanden — was bedeutet, dass
    Stage 1 nicht lief, was bedeutet dass die Roh-Version unter dem
    Threshold liegt (<1500w fuer Verlauf, <3500w fuer Transkript) und
    damit auch klein genug fuer direkten Kontext-Einbau ist.

    Antragsvorlage UND Vorantrag werden zusammengefuegt wenn beide da sind
    (relevant bei Folgeverlaengerung). Bei Akutantrag/Verlaengerung/
    Entlassbericht typisch nur die Antragsvorlage. Bei Anamnese/
    Dokumentation typisch keines.

    Returns: (verlauf_context, transcript_context, patientendaten_context).
    Alle drei Strings, ggf. "".
    """
    verlauf_context = (
        parent_context.get("verlauf_summary_text")
        or parent_context.get("source_verlauf_text")
        or ""
    )
    transcript_context = (
        parent_context.get("transcript_summary_text")
        or parent_context.get("result_transcript")
        or ""
    )
    # Patientendaten: ggf. beide Quellen zusammenfuegen mit klarem Separator
    antragsvorlage = (parent_context.get("source_antragsvorlage_text") or "").strip()
    vorantrag      = (parent_context.get("source_vorantrag_text") or "").strip()
    # v19.13: Prozessreflexion (P4) als Fidelity-Quelle im Repair-Kontext -
    # sonst wuerde ein Repair korrekt eingebaute Reflexionsinhalte als
    # unbelegt behandeln und ggf. entfernen.
    prozessreflexion = (parent_context.get("source_prozessreflexion_text") or "").strip()
    parts = []
    if antragsvorlage:
        parts.append("ANTRAGSVORLAGE (Anamnese, Diagnosen, Status):\n" + antragsvorlage)
    if vorantrag:
        parts.append("VORANTRAG (vorheriger Bericht mit Verlauf, Diagnosen):\n" + vorantrag)
    if prozessreflexion:
        parts.append("PROZESSREFLEXION DES KLIENTEN (Abschlussreflexion):\n" + prozessreflexion)
    patientendaten_context = "\n\n".join(parts)

    return verlauf_context, transcript_context, patientendaten_context


def _build_repair_context(
    parent: dict,
    req_codes: list[str],
    req_hint: str,
    *,
    repair_sources: Optional[dict] = None,
):
    """Geteilte Logik von preview + execute:
      - validiert dass parent.status=done und result_text vorhanden
      - liest QC, filtert akzeptierte Issues, validiert dass alle Codes existieren
      - baut den verketteten Original-Text (Anamnese Two-Stage-Case)
      - v19.3: extrahiert Verlauf/Transkript-Kontext aus repair_sources
      - baut den final_prompt
    Returns: (workflow, original_text, accepted_issues, final_prompt)
    Wirft HTTPException(400|422) bei Validierungsfehlern.

    v19.3 Argument:
      repair_sources: Output von job_queue.get_repair_context(). Enthaelt
                      verlauf_summary_text, source_verlauf_text,
                      transcript_summary_text, result_transcript. Wenn None,
                      laeuft Repair ohne Kontext (Backwards-Compat fuer alte
                      Tests oder Edge-Cases). Caller sollte das normalerweise
                      mitliefern.
    """
    if parent.get("status") != "done":
        raise HTTPException(
            status_code=400,
            detail="Repair nur fuer abgeschlossene Jobs (status=done)",
        )
    workflow = parent.get("workflow")
    result_text = parent.get("result_text") or ""
    befund_text = parent.get("befund_text") or ""
    if not result_text.strip():
        raise HTTPException(
            status_code=400,
            detail="Original-Job hat keinen result_text - Repair nicht moeglich",
        )

    qc_data = parent.get("quality_check") or {}
    all_issues = deserialize_issues(qc_data)
    known_codes = {i.code for i in all_issues}

    accepted_codes_set = set(req_codes)
    unknown = sorted(accepted_codes_set - known_codes)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "msg": "Unbekannte Issue-Codes (nicht im QC des Parent-Jobs)",
                "unknown_codes": unknown,
                "known_codes": sorted(known_codes),
            },
        )

    accepted_issues = [i for i in all_issues if i.code in accepted_codes_set]
    original_for_prompt = combined_result_text(workflow, result_text, befund_text)

    # v19.3: Quellen aus repair_sources extrahieren (Synthese bevorzugt,
    # Roh-Version als Fallback). Bei repair_sources=None laeuft der Prompt
    # ohne Kontext - dann verhaelt sich Repair wie pre-v19.3.
    if repair_sources:
        verlauf_context, transcript_context, patientendaten_context = _resolve_repair_sources(repair_sources)
        logger.info(
            "Repair-Kontext: Verlauf=%dw, Transkript=%dw, Patientendaten=%dw",
            len(verlauf_context.split()) if verlauf_context else 0,
            len(transcript_context.split()) if transcript_context else 0,
            len(patientendaten_context.split()) if patientendaten_context else 0,
        )
    else:
        verlauf_context = ""
        transcript_context = ""
        patientendaten_context = ""

    final_prompt = build_repair_prompt(
        workflow, original_for_prompt, accepted_issues, req_hint or "",
        verlauf_context=verlauf_context,
        transcript_context=transcript_context,
        patientendaten_context=patientendaten_context,
    )
    return workflow, original_for_prompt, accepted_issues, final_prompt


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


async def _run_repair_coroutine(
    job,            # JobState - Forward-Reference (kein circular import)
    workflow: str,
    final_prompt: str,
    model_override: Optional[str] = None,
) -> dict:
    """Schlanker Repair-Run: ein einziger LLM-Call, kein Transcribing,
    kein PDF-Extract, kein Stage 1.

    Returns ein dict mit den gleichen Keys wie der normale _run-Output:
      text, befund_text (None ausser anamnese), model_used,
      generation_telemetry, verlauf_summary_audit (immer None).
    """
    job.set_progress(15, "Repair", "Modell denkt")
    max_tok = max_tokens_for(workflow)

    def _on_tok(p):
        try:
            ratio = float(p.get("ratio") or 0)
            job.set_progress(
                min(95, int(15 + ratio * 80)),
                "Repair",
                f"{p.get('count', 0)} Tokens",
            )
        except Exception:
            pass

    _log_prompt(job.job_id, workflow, "repair", REPAIR_SYSTEM_PROMPT, final_prompt)
    result = await generate_text(
        REPAIR_SYSTEM_PROMPT,
        final_prompt,
        max_tokens=max_tok,
        model=model_override,
        workflow=workflow,
        on_progress=_on_tok,
        force_hard_no_think=True,
        # O5 (2026-07-01): Ueberarbeitung soll deterministisch sein, nicht
        # kreativ - kalte Temperatur statt Profil-Default (~0.4) bzw. dem
        # +0.2-Nudge des harten Anti-Think-Pfads.
        temperature_override=0.15,
    )

    raw = (result.get("text") or "").strip()
    _log_output(job.job_id, workflow, "repair", raw, result.get("telemetry"))

    # Wenn Anamnese-Workflow und der Output enthaelt ###BEFUND###:
    # in zwei Felder splitten (analog zur normalen Pipeline). Frontend zeigt
    # dann Tabs Anamnese/Befund - genauso wie beim originalen Job.
    anamnese_part, befund_part = _split_anamnese_concat(workflow, raw)

    tel = result.get("telemetry") or {}
    return {
        "text":        anamnese_part,
        "befund_text": befund_part,
        "akut_text":   None,
        "model_used":  result.get("model_used"),
        "generation_telemetry": {
            **tel,
            "retry_used":      result.get("retry_used", False),
            "degraded":        result.get("degraded", False),
            "degraded_reason": result.get("degraded_reason"),
            "repair_run":      True,  # Marker fuer perf_log
        },
        # Stage 1 laeuft beim Repair definitiv nicht:
        "verlauf_summary_text":  None,
        "verlauf_summary_audit": None,
    }


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


def _stage1_audit_bundle(
    *,
    applied: bool,
    raw_word_count: int,
    summary_word_count: Optional[int] = None,
    compression_ratio: Optional[float] = None,
    duration_s: Optional[float] = None,
    telemetry: Optional[dict] = None,
    retry_used: bool = False,
    retry_telemetry: Optional[dict] = None,
    degraded: bool = False,
    issues: Optional[list] = None,
    target_words: Optional[int] = None,
    fallback_reason: Optional[str] = None,
) -> dict:
    """Einheitliche Audit-Struktur fuer beide Stage-1-Pipelines."""
    return {
        "applied":              applied,
        "raw_word_count":       raw_word_count,
        "summary_word_count":   summary_word_count,
        "compression_ratio":    compression_ratio,
        "duration_s":           duration_s,
        "telemetry":            telemetry or {},
        "retry_used":           retry_used,
        "retry_telemetry":      retry_telemetry or {},
        "degraded":             degraded,
        "issues":               issues or [],
        "target_words":         target_words,
        "fallback_reason":      fallback_reason,
    }


async def _run_verlauf_stage1(
    *,
    workflow: str,
    verlaufsdoku_text: str,
    patient_initial: Optional[str],
    job,
    bands: dict,
) -> tuple[str, Optional[dict]]:
    """v19.2 Schritt 5: Stage-1-Pipeline (Verlauf-Verdichtung).

    Aktivierung an drei Bedingungen geknuepft:
      1. Feature-Flag STAGE1_ENABLED ist gesetzt (default: True ab v19.2)
      2. Workflow gehoert zur Whitelist (Verlaengerung, Folgeverl., EB)
      3. Bereinigte Verlaufsdoku hat substanzielle Laenge (>=1500 Woerter)

    Returns (ggf. ersetzter verlaufsdoku_text, audit_bundle_oder_None).
    Bei Erfolg: verlaufsdoku_text wird durch die Stage-1-Summary ersetzt.
    Bei Fehler (Exception, leerer/zu kurzer Output): Fallback auf das
    Original — Job laeuft weiter, aber mit erhoehtem VRAM-Risiko in Stage 2.
    """
    _stage1_enabled = getattr(settings, "STAGE1_ENABLED", True)

    if should_run_verlauf_stage1(
        workflow,
        verlaufsdoku_text,
        flag_enabled=_stage1_enabled,
    ):
        verlaufsdoku_raw_text = verlaufsdoku_text
        raw_words = len(verlaufsdoku_raw_text.split())
        logger.info(
            "Stage 1 aktiviert fuer Job %s (%s): Verlauf hat %d Woerter",
            job.job_id, workflow, raw_words,
        )

        # Optional: Sub-Progress innerhalb der Extraktions-Phase
        try:
            if "extraction" in bands:
                eb = bands["extraction"]
                # 70% des Extraktions-Bandes ist die Stage-1-Phase
                stage1_progress = eb[0] + int((eb[1] - eb[0]) * 0.7)
                job.set_progress(stage1_progress, "Sammeln und Zusammenfassen von Informationen.")
        except Exception:
            pass

        # v19.2.1: STAGE1_TARGET_WORDS nur nutzen wenn explizit gesetzt.
        # Andernfalls den proportionalen Default von summarize_verlauf greifen lassen
        # (target = max(800, raw_words * 0.12)). Hintergrund: fixe 4000w war zu hoch,
        # fuehrte zu 95% Failure-Rate weil Qwen3 konsistent ~500-1500w produziert.
        target_words_override = getattr(settings, "STAGE1_TARGET_WORDS", None)
        try:
            summarize_kwargs = {
                "verlauf_text":     verlaufsdoku_raw_text,
                "workflow":         workflow,
                "patient_initial":  patient_initial,
            }
            if target_words_override is not None:
                summarize_kwargs["target_words"] = target_words_override
            stage1_result = await summarize_verlauf(**summarize_kwargs)
            # v19.15 (C2): Stage-1-Call in prompts.log aufnehmen - gepaart
            # wie alle anderen LLM-Calls (CALL: stage1_verlauf).
            try:
                _log_prompt(
                    job.job_id, workflow, "stage1_verlauf",
                    stage1_result.get("system_prompt", ""),
                    stage1_result.get("user_content", ""),
                )
                _log_output(
                    job.job_id, workflow, "stage1_verlauf",
                    stage1_result.get("summary", ""),
                    stage1_result.get("telemetry"),
                )
            except Exception:
                pass  # Logging darf den Job nie gefaehrden
            # Erfolg -> ersetzen, Audit-Bundle aufbauen
            # target_words kommt jetzt aus stage1_result (echter Wert),
            # nicht aus der lokalen Variable
            effective_target = stage1_result.get("target_words", target_words_override)
            audit = _stage1_audit_bundle(
                applied=True,
                raw_word_count=stage1_result["raw_word_count"],
                summary_word_count=stage1_result["summary_word_count"],
                compression_ratio=stage1_result["compression_ratio"],
                duration_s=stage1_result["duration_s"],
                telemetry=stage1_result.get("telemetry", {}),
                retry_used=stage1_result.get("retry_used", False),
                retry_telemetry=stage1_result.get("retry_telemetry", {}),
                degraded=stage1_result.get("degraded", False),
                issues=stage1_result.get("issues", []),
                target_words=effective_target,
            )
            logger.info(
                "Stage 1 erfolgreich: %d -> %d Woerter (Kompression %.0f%%), "
                "retry=%s, degraded=%s, issues=%d",
                stage1_result["raw_word_count"],
                stage1_result["summary_word_count"],
                (1 - stage1_result["compression_ratio"]) * 100,
                stage1_result["retry_used"],
                stage1_result["degraded"],
                len(stage1_result.get("issues", [])),
            )
            return stage1_result["summary"], audit
        except Exception as e:
            # Fallback: Original-Verlauf behalten, Audit-Eintrag mit Begruendung
            logger.warning(
                "Stage 1 fehlgeschlagen (%s), Fallback auf Roh-Verlauf",
                e,
            )
            audit = _stage1_audit_bundle(
                applied=False,
                raw_word_count=raw_words,
                target_words=target_words_override,
                fallback_reason=f"exception: {type(e).__name__}: {str(e)[:200]}",
            )
            # verlaufsdoku_text bleibt unveraendert (das Original)
            return verlaufsdoku_text, audit
    elif workflow in STAGE1_VERLAUF_WORKFLOWS and verlaufsdoku_text:
        # Workflow waere passend, aber Verlauf zu kurz oder Flag aus.
        # Trotzdem einen Mini-Audit-Eintrag, damit man im Performance-Log
        # sehen kann _warum_ Stage 1 nicht lief.
        reason = verlauf_stage1_skip_reason(
            workflow,
            verlaufsdoku_text,
            flag_enabled=_stage1_enabled,
        )
        # v19.15 (C3): Skip-Grund auf INFO heben. Hintergrund Job 3d6d3708
        # (2026-07-31): 63k-Zeichen-Verlauf lief OHNE Stage 1 durch
        # (Budget-Guard-Trunkierung, 327-Woerter-Output) und der Grund war
        # nur muehsam aus dem Audit-Bundle rekonstruierbar.
        logger.info(
            "Stage 1 uebersprungen fuer Job %s (%s): %s (Verlauf: %d Woerter)",
            job.job_id, workflow, reason, len(verlaufsdoku_text.split()),
        )
        audit = _stage1_audit_bundle(
            applied=False,
            raw_word_count=len(verlaufsdoku_text.split()),
            target_words=getattr(settings, "STAGE1_TARGET_WORDS", None),
            fallback_reason=reason,
        )
        return verlaufsdoku_text, audit

    return verlaufsdoku_text, None


async def _run_transcript_stage1(
    *,
    workflow: str,
    transkript_text: str,
    patient_initial: Optional[str],
    job,
    bands: dict,
) -> tuple[str, Optional[str], Optional[dict]]:
    """v19.3 Transkript-Stage-1 (Transkript-Verdichtung).

    Verdichtet Sitzungs-Transkripte auf eine 3-Sektionen-Synthese BEVOR
    sie in den Hauptcall gehen. Wirkt nur fuer Workflows
    {dokumentation, anamnese}.

    Returns (ggf. ersetzter transkript_text, summary_text_oder_None,
    audit_bundle_oder_None). Bei Erfolg wird transkript_text durch die
    Synthese ersetzt und die Synthese zusaetzlich fuer den Repair-Kontext
    zurueckgegeben (v19.3-Persistierung). Bei Fehler: Fallback auf
    Roh-Transkript (_sample_uniformly-Risiko bleibt).
    """
    _tr_stage1_enabled = getattr(settings, "TRANSCRIPT_STAGE1_ENABLED", True)
    # DRY-Fix 2026-07-01: settings ist die einzige Quelle (staging
    # re-exportiert denselben Wert) - kein getattr-Fallback mehr noetig.
    _tr_stage1_min_words = settings.TRANSCRIPT_STAGE1_MIN_WORDS

    if should_run_transcript_stage1(
        workflow,
        transkript_text,
        flag_enabled=_tr_stage1_enabled,
        min_words=_tr_stage1_min_words,
    ):
        transkript_raw_text = transkript_text
        tr_raw_words = len(transkript_raw_text.split())
        logger.info(
            "Transcript-Stage 1 aktiviert fuer Job %s (%s): Transkript hat %d Woerter",
            job.job_id, workflow, tr_raw_words,
        )

        try:
            if "extraction" in bands:
                eb = bands["extraction"]
                # 90% des Extraktions-Bandes ist die Transkript-Stage-1-Phase
                # (Verlauf-Stage-1 nutzt 70%; Transkript-Stage-1 kommt danach)
                tr_stage1_progress = eb[0] + int((eb[1] - eb[0]) * 0.9)
                job.set_progress(tr_stage1_progress, "Transkript-Verdichtung (Stage 1)")
        except Exception:
            pass

        tr_target_override = getattr(settings, "TRANSCRIPT_STAGE1_TARGET_WORDS", None)
        try:
            tr_kwargs = {
                "transcript_text": transkript_raw_text,
                "workflow":        workflow,
                "patient_initial": patient_initial,
            }
            if tr_target_override is not None:
                tr_kwargs["target_words"] = tr_target_override
            tr_result = await summarize_transcript(**tr_kwargs)

            effective_target = tr_result.get("target_words", tr_target_override)
            audit = _stage1_audit_bundle(
                applied=True,
                raw_word_count=tr_result["raw_word_count"],
                summary_word_count=tr_result["summary_word_count"],
                compression_ratio=tr_result["compression_ratio"],
                duration_s=tr_result["duration_s"],
                telemetry=tr_result.get("telemetry", {}),
                retry_used=tr_result.get("retry_used", False),
                retry_telemetry=tr_result.get("retry_telemetry", {}),
                degraded=tr_result.get("degraded", False),
                issues=tr_result.get("issues", []),
                target_words=effective_target,
            )
            logger.info(
                "Transcript-Stage 1 erfolgreich: %d -> %d Woerter "
                "(Kompression %.0f%%), retry=%s",
                tr_result["raw_word_count"],
                tr_result["summary_word_count"],
                (1 - tr_result["compression_ratio"]) * 100,
                tr_result["retry_used"],
            )
            # ÜBERSCHREIBEN + v19.3: fuer Repair-Kontext persistieren
            return tr_result["summary"], tr_result["summary"], audit
        except Exception as e:
            # Fallback: Roh-Transkript behalten, Audit mit Begruendung.
            # _sample_uniformly in llm.py wird dann vermutlich greifen.
            logger.warning(
                "Transcript-Stage 1 fehlgeschlagen (%s), Fallback auf "
                "Roh-Transkript (_sample_uniformly wird vermutlich greifen)",
                e,
            )
            audit = _stage1_audit_bundle(
                applied=False,
                raw_word_count=tr_raw_words,
                target_words=tr_target_override,
                fallback_reason=f"exception: {type(e).__name__}: {str(e)[:200]}",
            )
            # transkript_text bleibt das Original (mit Sampling-Risiko)
            return transkript_text, None, audit
    elif workflow in STAGE1_TRANSCRIPT_WORKFLOWS and transkript_text:
        # Workflow waere passend, aber Transkript zu kurz oder Flag aus.
        # Mini-Audit-Eintrag damit man im Performance-Log sieht _warum_.
        _tr_actual_words = len(transkript_text.split())
        reason = transcript_stage1_skip_reason(
            workflow,
            transkript_text,
            flag_enabled=_tr_stage1_enabled,
            min_words=_tr_stage1_min_words,
        )
        logger.info(
            "Transcript-Stage 1 NICHT aktiviert fuer Job %s (%s): %s",
            job.job_id, workflow, reason,
        )
        audit = _stage1_audit_bundle(
            applied=False,
            raw_word_count=_tr_actual_words,
            target_words=getattr(settings, "TRANSCRIPT_STAGE1_TARGET_WORDS", None),
            fallback_reason=reason,
        )
        return transkript_text, None, audit

    return transkript_text, None, None


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

    # v19.8: geschlecht-Whitelist. Alles ausser "w"/"m" (auch "auto", leer,
    # Muell) wird auf None normalisiert -> Verhalten wie bisher (ableiten).
    geschlecht_norm: Optional[str] = None
    if geschlecht and geschlecht.strip().lower() in ("w", "m"):
        geschlecht_norm = geschlecht.strip().lower()

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
    prozessreflexion_bytes = await prozessreflexion.read() if prozessreflexion and prozessreflexion.filename else None
    prozessreflexion_name  = prozessreflexion.filename    if prozessreflexion and prozessreflexion.filename else None
    style_bytes            = await style_file.read()      if style_file     and style_file.filename     else None
    style_name             = style_file.filename          if style_file     and style_file.filename     else None
    transcript_file_bytes  = await transcript_file.read() if transcript_file and transcript_file.filename else None
    transcript_file_name   = transcript_file.filename     if transcript_file and transcript_file.filename else None

    dx_list = [d.strip() for d in diagnosen.split(",") if d.strip()] if diagnosen else []

    # Sprint B (Multi-Job-Liste P1): kompakte Patientenkennung fuer die Liste.
    # Der String kommt vom Frontend so wie er angezeigt werden soll ("Frau M.",
    # "Herr S." oder nur "M." ohne Anrede). Die LLM-Pipeline parst die Form
    # spaeter separat via parse_explicit_patient_name().
    patient_kuerzel = patientenname.strip() if patientenname and patientenname.strip() else None

    # Job anlegen
    job = job_queue.create_job(
        workflow=workflow,
        description=f"Workflow: {workflow}" + (f" | Audio: {audio_name}" if audio_name else ""),
        therapeut_id=therapeut_id,
        patient_kuerzel=patient_kuerzel,
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
        "has_prozessreflexion": bool(prozessreflexion_bytes),
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
        _has_audio = bool(audio_bytes)
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

        # ── 1a2. Transkript-Datei (.txt/.docx) einlesen (v19.16 G0) ──
        # Direkte Quelle wie Audio; greift nur wenn noch kein Transkript da ist.
        if not transkript_text and transcript_file_bytes and transcript_file_name:
            _tf_suffix = _Path(transcript_file_name).suffix.lower()
            if _tf_suffix in (".txt", ".text", ".md"):
                for _enc in ("utf-8", "cp1252", "latin-1"):
                    try:
                        transkript_text = transcript_file_bytes.decode(_enc)
                        break
                    except UnicodeDecodeError:
                        continue
            else:
                _tf_path = upload_dir() / f"{_uuid.uuid4().hex}{_tf_suffix}"
                _tf_path.write_bytes(transcript_file_bytes)
                try:
                    transkript_text = await extract_text(_tf_path)
                except Exception as e:
                    logger.warning("Transkript-Datei-Extraktion fehlgeschlagen: %s", e)
            if transkript_text:
                logger.info("Transkript aus Datei '%s' (%d Wörter)",
                            transcript_file_name, len(transkript_text.split()))

        # ── 1b. P0-Recording: Transkript aus DB holen (ggf. warten) ──
        # Wenn kein Transkript direkt mitgegeben wurde aber eine Recording-ID,
        # holt jobs.py das Transkript selbst. Ist die Aufnahme noch nicht
        # fertig transkribiert, wird sie in der P0-Queue priorisiert und
        # der Job wartet bis sie bereit ist (max. 10 Min).
        # v19.16 (G2): Grund fuer ein fehlendes Recording-Transkript - fliesst
        # in die Gate-Fehlermeldung (G1) ein, damit der Therapeut versteht
        # was passiert ist und was zu tun ist.
        transcript_failure_reason = None
        if not transkript_text and p0_recording_id:
            rec_id_int = None
            try:
                rec_id_int = int(p0_recording_id)
            except (ValueError, TypeError):
                logger.warning("Ungültige p0_recording_id: %r", p0_recording_id)
                transcript_failure_reason = (
                    "Die Aufnahme-Referenz war ungueltig."
                )

            if rec_id_int is not None:
                from app.core.database import async_session_factory as _asf
                from app.models.db import Recording as _Recording
                from sqlalchemy import select as _sel
                from app.core.files import recordings_dir

                async with _asf() as _db:
                    _res = await _db.execute(
                        _sel(_Recording).where(_Recording.id == rec_id_int)
                    )
                    _rec = _res.scalar_one_or_none()

                if _rec and _rec.transcript:
                    # Bereits fertig — direkt verwenden
                    transkript_text = _rec.transcript
                    logger.info("P0-Recording %d: Transkript aus DB (%d Wörter)",
                                rec_id_int, len(transkript_text.split()))
                elif _rec and _rec.status in ("uploading", "transcribing"):
                    # Noch nicht fertig → priorisieren + warten.
                    # v19.16 (G3): Timeout skaliert mit der Aufnahmelaenge -
                    # fix 600 s reichte fuer eine 62-min-Aufnahme nicht
                    # (Fall 58db7006). Formel: max(600, 2x Audiodauer + 120),
                    # gedeckelt bei 1800 s. Ohne bekannte Dauer: 900 s.
                    from app.api.recordings import reprioritize_recording, wait_for_transcript
                    _rec_dur = float(_rec.duration_s or 0.0)
                    if _rec_dur > 0:
                        _wait_timeout = int(max(600, min(_rec_dur * 2 + 120, 1800)))
                    else:
                        _wait_timeout = 900
                    audio_path_rec = recordings_dir() / _rec.filename
                    if audio_path_rec.exists():
                        await reprioritize_recording(rec_id_int, audio_path_rec)
                    job.set_progress(5, "Warte auf Transkription",
                                     "Aufnahme wird priorisiert transkribiert…")
                    transkript_text = await wait_for_transcript(rec_id_int, timeout_s=_wait_timeout) or ""
                    if transkript_text:
                        logger.info("P0-Recording %d: Transkript nach Wartezeit (%d Wörter)",
                                    rec_id_int, len(transkript_text.split()))
                    else:
                        logger.warning("P0-Recording %d: Transkript nach Timeout (%ds) nicht verfügbar",
                                       rec_id_int, _wait_timeout)
                        transcript_failure_reason = (
                            f"Die Aufnahme war nach {_wait_timeout // 60} Minuten "
                            "Wartezeit noch nicht fertig transkribiert. Bitte warten "
                            "bis die Aufnahme in P0 'Bereit' zeigt und den Auftrag "
                            "erneut starten."
                        )
                elif _rec and _rec.status == "error":
                    logger.warning("P0-Recording %d: Status=error, kein Transkript verfügbar", rec_id_int)
                    transcript_failure_reason = (
                        "Die Transkription dieser Aufnahme ist fehlgeschlagen "
                        f"({(_rec.error_msg or 'unbekannter Fehler')[:160]}). In P0 kann die "
                        "Transkription erneut gestartet werden."
                    )
                else:
                    logger.warning("P0-Recording %d: nicht gefunden oder unbekannter Status", rec_id_int)
                    transcript_failure_reason = (
                        "Die gewaehlte Aufnahme wurde nicht gefunden - "
                        "moeglicherweise wurde sie geloescht."
                    )

                # v19.16 (T4/D3): Coverage-Luecke des Recordings an den Job
                # heften - der QualityCheck macht daraus ein CRITICAL-Issue.
                if _rec is not None and getattr(_rec, "coverage_gap_s", None):
                    job.transcript_coverage_gap_s = float(_rec.coverage_gap_s)

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
        from app.services.extraction import validate_or_reject
        _ocr_warnings: list[str] = []

        def _check_ocr_garbage(text: str, label: str, file_name: str = "") -> str:
            """Duenner Wrapper um extraction.validate_or_reject, der den
            Warn-Text in die outer-scope-Liste _ocr_warnings legt und das
            Logging zentral erledigt. Die eigentliche Logik
            (Garbage-Detection + Hard-Reject ab 3+ Problemen) sitzt jetzt
            getestet in extraction.py."""
            text_out, warning = validate_or_reject(text, label, file_name)
            if warning is None:
                return text_out
            logger.warning("[OCR-Validator] %s", warning)
            _ocr_warnings.append(warning)
            if text_out == "" and text:
                logger.error(
                    "[OCR-Validator] %s als unbrauchbar verworfen (Hard-Reject)",
                    label,
                )
            return text_out

        selbstauskunft_text = ""
        selbstauskunft_empty = False   # v19.7: leeres/unextrahierbares Formular?
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
                selbstauskunft_empty = True
            else:
                from app.services.extraction import source_extraction_is_empty
                selbstauskunft_empty = source_extraction_is_empty(path, selbstauskunft_text)
            if selbstauskunft_empty:
                logger.warning(
                    "[QC] Selbstauskunft '%s' ohne verwertbaren Inhalt "
                    "(leeres/unausgefuelltes Formular?) - QC-Warnung gesetzt.",
                    selbstauskunft_name)

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
        verlaufsdoku_raw_text = ""  # v19.2: Roh-Verlauf vor Stage 1 (fuer Audit)
        if verlaufsdoku_bytes and verlaufsdoku_name:
            suffix = _Path(verlaufsdoku_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(verlaufsdoku_bytes)
            try:
                verlaufsdoku_text = await extract_text(path)
                verlaufsdoku_text = clean_verlauf_text(verlaufsdoku_text)
                verlaufsdoku_text = _check_ocr_garbage(
                    verlaufsdoku_text, "Verlaufsdokumentation", verlaufsdoku_name)
                # v19.3: Roh-Verlauf SOFORT snapshotten, unabhaengig von Stage-1.
                # Damit ist der Repair-Kontext auch bei kurzen Verlaeufen
                # (< STAGE1_VERLAUF_MIN_WORDS) verfuegbar.
                verlaufsdoku_raw_text = verlaufsdoku_text
            except Exception as e:
                logger.warning("Verlaufsdoku-Extraktion fehlgeschlagen: %s", e)

        # ── v19.2 Schritt 5: Stage-1-Pipeline (Verlauf-Verdichtung) ────────
        # R2 (2026-07-01): Logik extrahiert nach _run_verlauf_stage1()
        # (Modul-Level, oberhalb von create_generate_job).
        #
        # v19.4 / B-Fix "Frau S.": Den EXPLIZIT uebergebenen Patientennamen schon
        # VOR Stage 1 aufloesen und an die Verdichter durchreichen. Sonst lief
        # summarize_transcript mit patient_initial=None und nahm den Beispielnamen
        # ("Frau S.") aus dem Prompt woertlich — der dann durch die Synthese in
        # den Hauptcall leakte (Substitution dort faengt nur [Patient/in]/Frau X.).
        # Die vollstaendige Namensaufloesung aus Dokumenten passiert weiterhin
        # erst in Schritt 4 (nach Stage 1) — fuer Stage 1 reicht der explizite Name;
        # fehlt er, nutzen die Verdichter neutral "die Patientin/der Patient".
        _patient_initial_early: Optional[str] = (
            patientenname.strip() if patientenname and patientenname.strip() else None
        )

        verlaufsdoku_text, _stage1_audit = await _run_verlauf_stage1(
            workflow=workflow,
            verlaufsdoku_text=verlaufsdoku_text,
            patient_initial=_patient_initial_early,
            job=job,
            bands=bands,
        )

        # ── v19.3 Transkript-Stage-1 (Transkript-Verdichtung) ──────────────
        # R2 (2026-07-01): Logik extrahiert nach _run_transcript_stage1()
        # (Modul-Level). WICHTIG: Wir snapshotten das Roh-Transkript VOR dem
        # Stage-1-Lauf — die Job-API liefert weiterhin das ROH-Transkript ans
        # Frontend (Therapeut*innen wollen Whisper-Output zum Download),
        # waehrend die LLM-Pipeline (build_user_content, P2-Befund) mit der
        # Verdichtung weiterarbeitet.
        _transkript_raw_for_result = transkript_text
        (
            transkript_text,
            _transcript_summary_text,   # v19.3: fuer Repair-Kontext persistieren
            _transcript_stage1_audit,
        ) = await _run_transcript_stage1(
            workflow=workflow,
            transkript_text=transkript_text,
            patient_initial=_patient_initial_early,
            job=job,
            bands=bands,
        )

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

        # v19.13 P4: Prozessreflexion (Abschlussreflexion des Klienten, optional).
        # Kein Stage-1-Verdichter (typisch 2-5 Seiten, weit unter
        # STAGE1_VERLAUF_MIN_WORDS) - laeuft aber unten durch den
        # Input-Budget-Guard mit. Bewusst NICHT als Namens-/Geschlechtsquelle
        # registriert: Kandidaten-Konsens bleibt Antragsvorlage + Verlaufskopf.
        prozessreflexion_text = ""
        if prozessreflexion_bytes and prozessreflexion_name:
            suffix = _Path(prozessreflexion_name).suffix.lower()
            path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
            path.write_bytes(prozessreflexion_bytes)
            try:
                prozessreflexion_text = await extract_text(path)
                prozessreflexion_text = _check_ocr_garbage(
                    prozessreflexion_text, "Prozessreflexion", prozessreflexion_name)
            except Exception as e:
                logger.warning("Prozessreflexion-Extraktion fehlgeschlagen: %s", e)

        # P2: Wenn ALLE Quell-Texte als unbrauchbar gefiltert wurden, ist die
        # Datenlage zu duenn fuer eine sinnvolle Generierung - frueh abbrechen.
        _all_sources = [selbstauskunft_text, vorbefunde_text, verlaufsdoku_text,
                        antragsvorlage_text, vorantrag_text, prozessreflexion_text]
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
        # v13: derive_word_limits wird nicht mehr direkt aufgerufen; resolve_length_anchor
        # weiter unten kapselt das. Hier nur noch Stiltexte einsammeln.
        style_context = ""
        style_is_example = False
        style_info = None   # Metadaten: source, chars – wird im Job gespeichert
        # Roh-Texte fuer Wortlimit-Berechnung (vor Destillation/Truncation gesammelt)
        _style_raw_texts: list[str] = []

        if style_text and style_text.strip():
            cleaned = deduplicate_paragraphs(style_text.strip())
            # v13 Strategie 3: Splitter erkennt "--- Beispiel N ---"-Marker und
            # liefert Einzeltexte zurück. Bei Single-Style-Input ist das eine
            # 1-Element-Liste (Backwards-Compat). Bei Eval-Tests mit
            # vorlage.txt + vorlage2.txt sind es zwei Elemente, was
            # resolve_length_anchor präzisere Wortzahl-Schätzung erlaubt.
            _style_raw_texts.extend(split_style_examples(cleaned))
            style_context = truncate_style_context(cleaned)
            style_is_example = True
            _n_examples = len(_style_raw_texts)
            style_info = {
                "source": "text_input",
                "chars": len(style_context),
                "words": len(style_context.split()),
                "n_examples": _n_examples,
            }
            logger.info(
                "Stilvorlage via Text-Input: %s (%d Beispiel%s)",
                _size_class(len(style_context)),
                _n_examples,
                "e" if _n_examples != 1 else "",
            )
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
                        # v13 Strategie 3: Splitter ist bei Single-File no-op
                        # (eine Datei = ein Beispiel ohne Marker), aber konsistent
                        # mit anderen Kanälen. Falls jemals Multi-Upload kommt,
                        # ist hier nichts zu ändern.
                        _style_raw_texts.extend(split_style_examples(_raw))
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
                # v13 Strategie 3: pgvector liefert verkettete Beispiele mit
                # "--- Beispiel N ---"-Markern. Vorher wurde die ganze
                # Konkatenation als EIN Eintrag in _style_raw_texts gespeichert
                # → derive_word_limits berechnete die Statistik auf dem
                # konkatenierten Block (z.B. 5 × 400w = 2000w) und das
                # Längen-Limit landete weit über dem Workflow-Default.
                # Jetzt: aufgesplittet, derive_word_limits mittelt sauber
                # über die Einzelvorlagen.
                _style_raw_texts.extend(split_style_examples(style_context))
                _n_examples = len(_style_raw_texts)
                style_info = {
                    "source": "style_library",
                    "therapeut_id": therapeut_id.strip(),
                    "chars": len(style_context),
                    "n_examples": _n_examples,
                }
                logger.info(
                    "Stilvorlagen aus pgvector: %d Beispiel%s für %s",
                    _n_examples,
                    "e" if _n_examples != 1 else "",
                    therapeut_id.strip(),
                )

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
        from app.services.prompts import resolve_length_anchor
        from app.core.workflows import word_limit_for

        _anchor = resolve_length_anchor(
            workflow=workflow,
            style_raw_texts=_style_raw_texts if _style_raw_texts else None,
            workflow_default=word_limit_for(workflow, fallback=(200, 800)),
        )
        word_limits = (_anchor["min"], _anchor["max"])
        logger.info(
            "Längenanker für %s: %d–%d Wörter (target=%d, source=%s, n_substantial=%d)",
            workflow, _anchor["min"], _anchor["max"], _anchor["target"],
            _anchor["source"], _anchor["n_substantial"],
        )

        # 4. Patientennamen ermitteln — Reihenfolge:
        #    a) Explizit uebergeben (vor allem P1 Gespraechszusammenfassung)
        #    b) Aus antragsvorlage/vorantrag (Briefkopf "Wir berichten ueber ...")
        #    c) Aus selbstauskunft (Seite 1, "Nachname: ...")
        #    d) Fallback: verlaufsdoku / vorbefunde
        from app.services.extraction import extract_patient_name, parse_explicit_patient_name
        patient_name = None
        # v19.8: Herkunft der Anrede mitfuehren -> gender_source im Dict.
        _pn_source: Optional[str] = None

        # a) Explizit uebergeben
        if patientenname and patientenname.strip():
            patient_name = parse_explicit_patient_name(patientenname.strip())
            if patient_name:
                _pn_source = "explicit"
                logger.info("Patientenname explizit uebergeben: %s %s.",
                            patient_name["anrede"], patient_name["initial"])

        # b-d) Fallback: aus Dokumenten extrahieren
        # v19.12: extract_patient_name arbeitet mit Kandidaten-Konsens und
        # liefert quelle/anker als Telemetrie mit. Dissens innerhalb eines
        # Dokuments -> None (Warnung kommt aus der Extraktion selbst).
        if not patient_name:
            for src_text in (antragsvorlage_text, vorantrag_text, selbstauskunft_text, verlaufsdoku_text, vorbefunde_text):
                if src_text:
                    patient_name = extract_patient_name(src_text)
                    if patient_name:
                        _pn_source = "document"
                        logger.info(
                            "Patientenname aus Unterlagen erkannt: %s %s. "
                            "[v19.12-Telemetrie: quelle=%s anker=%s]",
                            patient_name["anrede"], patient_name["initial"],
                            patient_name.get("quelle"), patient_name.get("anker"),
                        )
                        break

        # v19.12 Kreuzcheck: Kopfzeile der Verlaufsdoku ("Nachname, Vorname
        # (Aufnahmenr)") gegen den erkannten Namen. Die Verlaufsdoku ist
        # handschriftlich (OCR/Vision) und traegt KEINE Anrede - sie ist nie
        # Geschlechtsquelle, aber ein unabhaengiges Namenssignal. Bei
        # Abweichung wird die Erkennung verworfen: still falsch ist
        # schlechter als offen.
        if patient_name and _pn_source == "document" and verlaufsdoku_text:
            from app.services.extraction import extract_verlaufskopf_name
            _vk = extract_verlaufskopf_name(verlaufsdoku_text)
            if _vk:
                _stamm_doc = (patient_name.get("nachname_stamm")
                              or patient_name.get("nachname") or "").lower()
                _stamm_vk = (_vk.get("nachname_stamm") or "").lower()
                if _stamm_doc and _stamm_vk and _stamm_doc != _stamm_vk:
                    logger.warning(
                        "v19.12 Kreuzcheck: Antragsvorlage (%s) vs. "
                        "Verlaufsdoku-Kopf (%s) nennen verschiedene Nachnamen "
                        "- Erkennung verworfen, Geschlecht bleibt offen.",
                        _stamm_doc, _stamm_vk,
                    )
                    patient_name = None
                    _pn_source = None
                elif _stamm_doc and _stamm_vk:
                    logger.info(
                        "v19.12 Kreuzcheck: Verlaufsdoku-Kopf bestaetigt "
                        "Nachnamen (%s, Aufnahmenr %s).",
                        _stamm_vk, _vk.get("aufnahmenummer"),
                    )

        # v19.8 (Identitaets-Guard, Schritt S2): strukturiertes Geschlecht aus
        # dem UI ueberstimmt jede abgeleitete Anrede. Ist NUR das Geschlecht
        # gesetzt (Kuerzel leer, nichts extrahiert), entsteht ein Dict OHNE
        # initial - alle Konsumenten (substitute_patient_placeholders,
        # build_system_prompt, _check_forbidden_names) pruefen .get("initial")
        # bzw. nachname/vorname und bleiben dann no-op; der QualityCheck
        # (GENDER_MISMATCH, Patch B) nutzt das gender-Feld trotzdem.
        if geschlecht_norm and not patient_name:
            patient_name = {"anrede": "", "vorname": "", "nachname": "", "initial": None}
            _pn_source = None
        if patient_name:
            if geschlecht_norm:
                patient_name["anrede"] = "Frau" if geschlecht_norm == "w" else "Herr"
                patient_name["gender"] = geschlecht_norm
                patient_name["gender_source"] = "explicit"
            else:
                _g = {"Frau": "w", "Herr": "m"}.get(patient_name.get("anrede") or "")
                patient_name["gender"] = _g
                patient_name["gender_source"] = _pn_source if _g else None
            logger.info(
                "Patient-Identitaet aufgeloest: initial=%s gender=%s source=%s",
                patient_name.get("initial"), patient_name.get("gender"),
                patient_name.get("gender_source"),
            )

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

        # v19.6: Kontext fuer den QualityCheck (laeuft in run_job nach DONE) auf
        # dem Job hinterlegen - in-process, wird dort per getattr gelesen.
        job.patient_name = patient_name   # Datenschutz-Namensleck-Check (Punkt 1)
        job.fokus_themen = bullets        # Stichpunkt/Fokus-Themen-Check (Punkt 6)
        job.selbstauskunft_empty = selbstauskunft_empty  # v19.7: leere Selbstauskunft (P2)
        # v19.13: Reflexions-Referenz-Check (P4) - nur wenn Reflexion vorhanden.
        job.prozessreflexion_present = bool(prozessreflexion_text and prozessreflexion_text.strip())
        # v19.15 (B3): Antragsvorlagen-Text fuer den Platzhalter-Check
        # (TEMPLATE_PLACEHOLDER_DETECTED) - erkennt Muster-/Stilvorlagen im
        # Antragsvorlage-Slot ("Herr X", "N.N.", ...).
        job.antragsvorlage_qc_text = antragsvorlage_text or None
        # v19.15 (C1): Trunkierungs-Heuristik ueber die dokumentartigen
        # Quellen (Selbstauskunft bewusst ausgenommen - Formulare enden
        # regulaer auf Labels/Kurztokens und wuerden Fehlalarme erzeugen).
        # Rohtexte VOR dem Budget-Guard, d.h. wie extrahiert.
        from app.services.extraction import looks_truncated, truncation_tail
        _trunc: list[dict] = []
        for _src_name, _src_text in (
            ("Verlaufsdokumentation", verlaufsdoku_text),
            ("Antragsvorlage",        antragsvorlage_text),
            ("Vorheriger Antrag",     vorantrag_text),
            ("Prozessreflexion",      prozessreflexion_text),
        ):
            if _src_text and looks_truncated(_src_text):
                _trunc.append({
                    "source": _src_name,
                    "tail":   truncation_tail(_src_text),
                })
                logger.warning(
                    "Quelle '%s' endet vermutlich unvollstaendig "
                    "(Job %s): ...%s",
                    _src_name, job.job_id, truncation_tail(_src_text),
                )
        job.truncated_sources = _trunc or None

        # v19.16 (G1): Quellen-Gate gegen Konfabulation - VOR jeder weiteren
        # (teuren) Verarbeitung. Wirft mit nutzerverstaendlicher Meldung;
        # job_queue uebernimmt str(e) als error_msg.
        _gate_msg = _missing_source_error(
            workflow,
            transkript_text=transkript_text,
            selbstauskunft_text=selbstauskunft_text,
            vorbefunde_text=vorbefunde_text,
            bullets=bullets or "",
            transcript_failure_reason=transcript_failure_reason,
        )
        if _gate_msg:
            logger.error("Quellen-Gate (%s): %s", workflow, _gate_msg)
            raise RuntimeError(_gate_msg)

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
        effective_instructions = instructions
        if geschlecht_norm and "KLIENT-GESCHLECHT" not in effective_instructions:
            _g_anrede = "Frau" if geschlecht_norm == "w" else "Herr"
            _g_wort = "weiblich" if geschlecht_norm == "w" else "männlich"
            _g_adj = "weibliche" if geschlecht_norm == "w" else "männliche"
            _g_hint = (
                f"\n\nKLIENT-GESCHLECHT: {_g_wort} – verwende konsequent "
                f"{_g_adj} Pronomen und Endungen."
            )
            if patient_name and patient_name.get("initial"):
                _g_hint += (
                    f' Verwende als Namenskürzel durchgehend '
                    f'"{patient_name["initial"]}" (z.B. "{_g_anrede} {patient_name["initial"]}").'
                )
            effective_instructions = effective_instructions + _g_hint

        # 5. Generieren – jede Variable hat genau eine Bedeutung
        # v18: prompt-Feld → workflow_instructions, neuer Parameter befund_vorlage.
        # `instructions` wurde oben aus workflow_instructions/prompt geholt und validiert.
        # Issue-2: Quellen fuer die Glossar-Konditionalitaet zusammenfuehren.
        # Bewusst die ROH-Quellen VOR dem Budget-Guard (der verdichtet nur -
        # Erkennung auf den volleren Texten ist die konservative Richtung).
        _glossar_source = "\n".join(t for t in (
            transkript_text, verlaufsdoku_text, selbstauskunft_text,
            vorbefunde_text, antragsvorlage_text, vorantrag_text,
            prozessreflexion_text,
        ) if t)
        system = build_system_prompt(
            workflow=workflow,
            workflow_instructions=effective_instructions,
            style_context=style_context,
            style_is_example=style_is_example,
            diagnosen=dx_list,
            patient_name=patient_name,
            word_limits=word_limits,
            source_text=_glossar_source,
        )
        # ── v19.4: Kombinierter Input-Budget-Guard ───────────────────────────
        # Nach allen isolierten Stage-1-Verdichtungen: prueft die SUMME aller
        # Quellen gegen das Wort-Budget und verdichtet die groessten noch-rohen
        # Quellen (v.a. Selbstauskunft/Vorbefunde) quellentreu nach. Laeuft NACH
        # build_system_prompt (exakte System-Groesse) und VOR build_user_content
        # (das die ggf. verdichteten Quellen konsumiert). Der System-Prompt
        # haengt seit Issue-2 nur SCHWACH von den Quellen ab (Glossar-Wahl auf den
        # ROH-Quellen); der Guard verdichtet quellentreu, die Wahl bleibt gueltig.
        _budget_updated, _budget_audit = await _apply_input_budget_guard(
            workflow=workflow,
            system_prompt=system,
            patient_initial=_patient_initial_early,
            sources={
                "transkript":     {"text": transkript_text,
                                   "compressed": bool(_transcript_stage1_audit
                                                      and _transcript_stage1_audit.get("applied"))},
                "verlaufsdoku":   {"text": verlaufsdoku_text,
                                   "compressed": bool(_stage1_audit
                                                      and _stage1_audit.get("applied"))},
                "selbstauskunft": {"text": selbstauskunft_text, "compressed": False},
                "vorbefunde":     {"text": vorbefunde_text,     "compressed": False},
                "antragsvorlage": {"text": antragsvorlage_text, "compressed": False},
                "vorantrag":      {"text": vorantrag_text,      "compressed": False},
                "prozessreflexion": {"text": prozessreflexion_text, "compressed": False},
            },
        )
        if "transkript" in _budget_updated:     transkript_text     = _budget_updated["transkript"]
        if "verlaufsdoku" in _budget_updated:   verlaufsdoku_text   = _budget_updated["verlaufsdoku"]
        if "selbstauskunft" in _budget_updated: selbstauskunft_text = _budget_updated["selbstauskunft"]
        if "vorbefunde" in _budget_updated:     vorbefunde_text     = _budget_updated["vorbefunde"]
        if "antragsvorlage" in _budget_updated: antragsvorlage_text = _budget_updated["antragsvorlage"]
        if "vorantrag" in _budget_updated:      vorantrag_text      = _budget_updated["vorantrag"]
        if "prozessreflexion" in _budget_updated: prozessreflexion_text = _budget_updated["prozessreflexion"]
        if _budget_audit.get("applied"):
            logger.info("Input-Budget-Guard Audit: %s", _budget_audit)

        user = build_user_content(
            workflow=workflow,
            transcript=transkript_text,
            fokus_themen=bullets,
            selbstauskunft_text=selbstauskunft_text,
            vorbefunde_text=vorbefunde_text,
            verlaufsdoku_text=verlaufsdoku_text,
            antragsvorlage_text=antragsvorlage_text,
            vorantrag_text=vorantrag_text,
            prozessreflexion_text=prozessreflexion_text,
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
                prozessreflexion_text or "",
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
            _log_output(job.job_id, workflow, "anamnese", anamnese_text,
                        result_a.get("telemetry"))

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
                source_text=_glossar_source,
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
            # ── v19.7 S2: Structured-Output-Pfad (Ollama format=JSON-Schema) ──
            # Das Modell liefert NUR die Slot-Werte; die (editierbare) Vorlage
            # wird deterministisch im Backend gefuellt -> Fixtext garantiert
            # 100% wortidentisch, kein Markdown-/Praeambel-/Klebebug-Risiko im
            # Vorlagentext. Fallback-Kette (jeweils mit lautem Log):
            #   1. Vorlage ohne {Slots}          -> direkt Freitext-Pfad
            #   2. JSON-Parse-Fehler / leeres Obj -> Freitext-Pfad
            #   3. Unerwartete Exception          -> Freitext-Pfad
            # Der Freitext-Pfad ist der bisherige Call und bleibt unveraendert.
            befund_text_generated = None
            result_b = None
            _structured_used = False
            try:
                from app.services.prompts import (
                    BEFUND_VORLAGE as _BEFUND_VORLAGE_DEFAULT,
                    build_befund_structured_prompt,
                    fill_befund_vorlage,
                )
                _bv = (
                    befund_vorlage
                    if (befund_vorlage and befund_vorlage.strip())
                    else _BEFUND_VORLAGE_DEFAULT
                )
                _sys_struct, _slots, _schema = build_befund_structured_prompt(
                    diagnosen=dx_list,
                    befund_vorlage=_bv,
                    source_text=_glossar_source,
                )
                if not _slots:
                    logger.info(
                        "Befund structured: Vorlage enthaelt keine {Slots} "
                        "- nutze direkt den Freitext-Pfad."
                    )
                else:
                    _log_prompt(job.job_id, workflow, "befund_structured",
                                _sys_struct, befund_user)
                    result_b = await generate_text(
                        _sys_struct, befund_user, max_tokens=befund_max_tok,
                        model=model, workflow="befund", on_progress=_on_tok_b,
                        response_format=_schema,
                    )
                    _sd = result_b.get("structured_data")
                    if (not result_b.get("structured_parse_error")
                            and isinstance(_sd, dict) and _sd):
                        befund_text_generated = fill_befund_vorlage(_bv, _sd).strip()
                        _structured_used = True
                        _empty_slots = [
                            s for s in _slots if not str(_sd.get(s) or "").strip()
                        ]
                        if _empty_slots:
                            logger.warning(
                                "Befund structured: %d leere Slot-Werte "
                                "(-> 'nicht erhoben' eingesetzt): %s",
                                len(_empty_slots), _empty_slots,
                            )
                    else:
                        logger.warning(
                            "Befund structured: JSON unbrauchbar "
                            "(parse_error=%s, type=%s) -> Freitext-Fallback.",
                            result_b.get("structured_parse_error"),
                            type(_sd).__name__,
                        )
            except Exception as _e:
                logger.warning(
                    "Befund structured: Pfad fehlgeschlagen (%s: %s) "
                    "-> Freitext-Fallback.", type(_e).__name__, _e,
                )

            if not _structured_used:
                _log_prompt(job.job_id, workflow, "befund", befund_system, befund_user)
                # Befund: keine max_words-Cap (Format ist fix), aber Keyword-Check sinnvoll
                result_b = await generate_text(befund_system, befund_user, max_tokens=befund_max_tok,
                                                model=model, workflow="befund", on_progress=_on_tok_b,
                                                expected_keywords=v16_expected_keywords)
                befund_text_generated = (result_b.get("text") or "").strip()
            _log_output(job.job_id, workflow, "befund", befund_text_generated,
                        result_b.get("telemetry"))

            # v19.1: Telemetrie beider Anamnese-Calls aggregieren.
            # Worst-case-Logik: wenn EINER der beiden Calls degradiert ist,
            # ist auch der Gesamt-Job degraded markiert.
            tel_a = result_a.get("telemetry") or {}
            tel_b = result_b.get("telemetry") or {}
            agg_telemetry = {
                "anamnese": {
                    **tel_a,
                    "retry_used":      result_a.get("retry_used", False),
                    "degraded":        result_a.get("degraded", False),
                    "degraded_reason": result_a.get("degraded_reason"),
                },
                "befund": {
                    **tel_b,
                    "retry_used":      result_b.get("retry_used", False),
                    "degraded":        result_b.get("degraded", False),
                    "degraded_reason": result_b.get("degraded_reason"),
                    # v19.7 S2: True = Vorlage deterministisch aus
                    # Slot-Werten gefuellt; False = Freitext-Pfad (Fallback
                    # oder Vorlage ohne Slots).
                    "structured_output": _structured_used,
                },
                # Top-Level-Aggregate fuer das Performance-Log
                "retry_used": result_a.get("retry_used", False) or result_b.get("retry_used", False),
                "degraded":   result_a.get("degraded", False)   or result_b.get("degraded", False),
                "degraded_reason": (
                    result_a.get("degraded_reason")
                    or result_b.get("degraded_reason")
                ),
                "think_ratio": max(
                    tel_a.get("think_ratio", 0) or 0,
                    tel_b.get("think_ratio", 0) or 0,
                ),
                "tokens_hit_cap": bool(
                    tel_a.get("tokens_hit_cap") or tel_b.get("tokens_hit_cap")
                ),
                "used_thinking_fallback": bool(
                    tel_a.get("used_thinking_fallback") or tel_b.get("used_thinking_fallback")
                ),
            }

            # Direkt zwei separate Felder zurueckgeben — keine Marker noetig
            result = {
                "text": anamnese_text,
                "befund_text": befund_text_generated,
                "transcript": result_a.get("transcript") or result_b.get("transcript"),
                "model_used": result_a.get("model_used"),
                "generation_telemetry": agg_telemetry,
            }
        else:
            _log_prompt(job.job_id, workflow, workflow, system, user)
            result = await generate_text(system, user, max_tokens=max_tok, model=model,
                                          workflow=workflow, on_progress=_on_tok,
                                          max_words=v16_max_words,
                                          expected_keywords=v16_expected_keywords)
            _log_output(job.job_id, workflow, workflow,
                        result.get("text") or "", result.get("telemetry"))
            # v19.1: Telemetrie aus generate_text in result["generation_telemetry"]
            # konsolidieren (im Anamnese-Pfad oben schon explizit gesetzt).
            tel = result.get("telemetry") or {}
            result["generation_telemetry"] = {
                **tel,
                "retry_used":      result.get("retry_used", False),
                "degraded":        result.get("degraded", False),
                "degraded_reason": result.get("degraded_reason"),
            }

        phase_times["llm"] = _t.time() - _t0

        try:
            import json as _j
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
            # v19.3: ROH-Transkript an's Frontend (Therapeut*in soll den
            # ungekuerzten Whisper-Output sehen/herunterladen koennen).
            # transkript_text selbst kann durch Stage-1 mit der Verdichtung
            # ueberschrieben sein; _transkript_raw_for_result haelt das
            # Original (wird unbedingt vor dem Stage-1-Block gesetzt, siehe
            # weiter oben in _run).
            "transcript":  _transkript_raw_for_result or None,
            "model_used":  result["model_used"],
            "style_info":  style_info,
            # P2: OCR-Validator-Warnungen ans Frontend durchreichen.
            # UI kann eine Warnbanner anzeigen wenn diese Liste nicht leer ist.
            "ocr_warnings": _ocr_warnings if _ocr_warnings else None,
            # v19.1: Think-Block-Telemetrie aus llm.generate_text() an
            # job_queue durchreichen (landet in jobs.generation_telemetry).
            "generation_telemetry": result.get("generation_telemetry"),
            # v19.2 Schritt 5: Stage-1-Audit (None wenn Stage 1 nicht relevant).
            # Wird in run_job in JobState.verlauf_summary_audit gespiegelt und
            # mit dem persist-Schritt in die DB-Spalte verlauf_summary_audit
            # geschrieben.
            "verlauf_summary_audit": _stage1_audit,
            # v19.2 Schritt 7: Der tatsaechliche Stage-1-Text (falls Stage 1
            # erfolgreich war). Wird ebenfalls in JobState gespiegelt und in
            # die DB-Spalte verlauf_summary_text geschrieben. None wenn
            # Stage 1 nicht lief oder fehlgeschlagen ist (in dem Fall steht
            # in verlaufsdoku_text noch das Original).
            "verlauf_summary_text": (
                verlaufsdoku_text
                if (_stage1_audit and _stage1_audit.get("applied"))
                else None
            ),
            # v19.3: Repair-Kontext-Quellen.
            # source_verlauf_text:         Roh-Verlauf nach clean_verlauf_text,
            #                              UNABHAENGIG von Stage 1.
            # transcript_summary_text:     Synthese des Transkripts wenn
            #                              Transcript-Stage-1 lief.
            # source_antragsvorlage_text:  Antragsvorlage nach extract_text
            #                              (Akutantrag/Verlaengerung/Entlassb.).
            # source_vorantrag_text:       Vorantrag bei Folgeverlaengerung.
            "source_verlauf_text":        verlaufsdoku_raw_text or None,
            "transcript_summary_text":    _transcript_summary_text,
            "source_antragsvorlage_text": antragsvorlage_text or None,
            "source_vorantrag_text":      vorantrag_text or None,
            # v19.13: Prozessreflexion fuer Repair-Fidelity-Kontext.
            "source_prozessreflexion_text": prozessreflexion_text or None,
            # v19.3: Transkript-Stage-1-Audit (None wenn nicht relevant oder
            # Workflow nicht in _TRANSCRIPT_STAGE1_WORKFLOWS).
            "transcript_summary_audit": _transcript_stage1_audit,
        }

    background_tasks.add_task(job_queue.run_job, job, _run())
    return {"job_id": job.job_id, "status": "pending"}
