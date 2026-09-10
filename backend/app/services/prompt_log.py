"""
prompt_log.py - LLM-IO-Logger: vollstaendige System/User-Prompts und Outputs
in prompts.log (taeglich rotierend) fuer die manuelle Qualitaetsinspektion.
v19.21 (S6c): aus app/api/jobs.py verschoben.
"""
import logging
from typing import Optional

from app.services.job_queue import job_queue

logger = logging.getLogger(__name__)


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


def _therapeut_for_log(job_id: str) -> str:
    """v19.20: Therapeuten-Kennung fuer den prompts.log-Header. Lookup ueber
    den Job-Cache der Queue; '-' wenn unbekannt (z.B. nach Neustart). Die
    Log-Auswertung (misc/eval_*_from_log.py, feedback._collect_prompt_log_blocks)
    matcht den Header mit '(.*)' hinter CALL und bleibt kompatibel."""
    try:
        job = job_queue.get_job(job_id)
        return (getattr(job, "therapeut_id", None) or "-") if job else "-"
    except Exception:
        return "-"


def _log_prompt(job_id: str, workflow: str, call_label: str,
                system: str, user: str) -> None:
    """
    Schreibt System- und User-Prompt eines LLM-Calls in prompts.log.

    call_label: z.B. 'anamnese', 'befund', 'verlaengerung' – unterscheidet
                bei Anamnese den ersten vom zweiten LLM-Call.
    v19.20: Header traegt zusaetzlich THERAPEUT: <id> - vorher war die
    Nutzung pro Therapeut aus dem prompts.log nicht auswertbar.
    """
    sep = "=" * 80
    _prompt_logger.debug(
        "\n%s\nJOB: %s  |  WORKFLOW: %s  |  CALL: %s  |  THERAPEUT: %s\n%s\n"
        "--- SYSTEM ---\n%s\n"
        "--- USER ---\n%s\n%s\n",
        sep, job_id, workflow, call_label, _therapeut_for_log(job_id), sep,
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
        "\n%s\nJOB: %s  |  WORKFLOW: %s  |  CALL: %s  |  THERAPEUT: %s  (OUTPUT)%s\n%s\n%s\n%s\n",
        sep, job_id, workflow, call_label, _therapeut_for_log(job_id), tele, sep, out, sep,
    )
