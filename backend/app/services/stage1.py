"""
stage1.py - Stage-1-Verdichtung (Verlauf, Transkript) als Pipeline-Phasen
inkl. Audit-Bundle fuer den QualityCheck. Konfiguration (Whitelists,
Min-Wortzahl) kommt aus app.services.staging.
v19.21 (S6c): aus app/api/jobs.py verschoben.
"""
import logging
from typing import Optional

from app.core.config import settings
from app.services.prompt_log import _log_output, _log_prompt
from app.services.staging import STAGE1_TRANSCRIPT_WORKFLOWS, STAGE1_VERLAUF_MIN_WORDS, STAGE1_VERLAUF_WORKFLOWS, should_run_transcript_stage1, should_run_verlauf_stage1, transcript_stage1_skip_reason, verlauf_stage1_skip_reason
from app.services.transcript_summary import summarize_transcript
from app.services.verlauf_summary import summarize_verlauf

logger = logging.getLogger(__name__)


# v19.2 / v19.3: Stage-1-Pipeline-Konfiguration kommt jetzt aus
# app.services.staging - einzige Quelle der Wahrheit fuer Workflow-Whitelist
# und Min-Wortzahl. Test: tests/unit/test_staging.py.
# Backwards-kompatibel re-exportiert:
_STAGE1_WORKFLOWS = STAGE1_VERLAUF_WORKFLOWS


_STAGE1_MIN_WORDS = STAGE1_VERLAUF_MIN_WORDS


_TRANSCRIPT_STAGE1_WORKFLOWS = STAGE1_TRANSCRIPT_WORKFLOWS


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
            # v19.19 (S3): Fehlschlag sichtbar im prompts.log (C2 loggte nur
            # Erfolge -> die Stage-1-Ausfaelle der groessten EB-Jobs waren
            # unsichtbar).
            try:
                _log_output(job.job_id, workflow, "stage1_verlauf",
                            f"[STAGE 1 FEHLGESCHLAGEN - Fallback Roh-Verlauf] "
                            f"{type(e).__name__}: {str(e)[:300]}", None)
            except Exception:
                pass
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
        # v19.19 (S3): auch Skips ins prompts.log - nur wenn ueberhaupt ein
        # Verlauf vorlag (sonst Log-Rauschen bei P1/P2).
        if verlaufsdoku_text and verlaufsdoku_text.strip():
            try:
                _log_output(job.job_id, workflow, "stage1_verlauf",
                            f"[STAGE 1 UEBERSPRUNGEN] {reason} "
                            f"(Verlauf: {len(verlaufsdoku_text.split())} Woerter)", None)
            except Exception:
                pass
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
            # v19.19 (S3): Transcript-Stage-1 bisher gar nicht im prompts.log.
            try:
                _log_output(job.job_id, workflow, "stage1_transcript",
                            tr_result.get("summary", ""), tr_result.get("telemetry"))
            except Exception:
                pass

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
            # v19.19 (S3): Fehlschlag sichtbar im prompts.log.
            try:
                _log_output(job.job_id, workflow, "stage1_transcript",
                            f"[TRANSCRIPT-STAGE 1 FEHLGESCHLAGEN - Fallback Roh-Transkript] "
                            f"{type(e).__name__}: {str(e)[:300]}", None)
            except Exception:
                pass
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
