"""
repair.py - Repair-Flow (v19): Parent-Job aufloesen, Quellen fuer den
Repair-Prompt zusammenstellen, No-Op-Erkennung, Repair-Coroutine.
v19.21 (S6c): aus app/api/jobs.py verschoben; die Routen bleiben in jobs.py.
"""
import logging
import re
from typing import Optional

from fastapi import HTTPException

from app.core.workflows import max_tokens_for
from app.services.job_queue import job_queue
from app.services.llm import generate_text
from app.services.prompt_log import _log_output, _log_prompt
from app.services.prompts import REPAIR_SYSTEM_PROMPT
from app.services.quality_check import build_repair_prompt, combined_result_text, deserialize_issues

logger = logging.getLogger(__name__)


# v19.19 (R1): No-op-Kalibrierung an den 8 Log-Repairs (13.08.-09.09.):
#   1.00/1.00/1.00 (byte-identisch)         -> No-op
#   0.98 (+3 % Woerter), 0.97 (+7 % Woerter) -> kleine, echte Ergaenzung
# Deshalb: sim >= 0.995 ODER (sim >= 0.97 UND Wortzahl-Delta <= 1 %).
_REPAIR_NOOP_SIM_HARD = 0.995


_REPAIR_NOOP_SIM_SOFT = 0.97


_REPAIR_NOOP_WORD_DELTA = 0.01


_REPAIR_ADD_HINT_RE = re.compile(
    r"ergänz|ergaenz|hinzufüg|hinzufueg|aufnehm|mitaufnehm|mit aufnehm|"
    r"erweiter|ausführlicher|ausfuehrlicher|mehr (?:aus|zu|über|ueber)|noch mehr|"
    r"zusätzlich|zusaetzlich|einbauen|integrier",
    re.IGNORECASE,
)


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
        # v19.19 (R3): Kontexte kappen, damit der Repair-Prompt nicht selbst
        # in den Budget-Guard laeuft (aaf4366f: 112k Zeichen -> uniform
        # gesampelt, das Modell sah die Quelle nicht, die es ergaenzen sollte).
        # Head/Tail-bewahrend; Original + Hinweis bleiben immer vollstaendig.
        from app.services.llm import _sample_head_tail
        _caps = {"verlauf": 30_000, "transcript": 30_000, "patientendaten": 15_000}
        if len(verlauf_context) > _caps["verlauf"]:
            verlauf_context = _sample_head_tail(verlauf_context, _caps["verlauf"])
        if len(transcript_context) > _caps["transcript"]:
            transcript_context = _sample_head_tail(transcript_context, _caps["transcript"])
        if len(patientendaten_context) > _caps["patientendaten"]:
            patientendaten_context = _sample_head_tail(patientendaten_context, _caps["patientendaten"])
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


def _text_similarity(a: str, b: str) -> float:
    """v19.19 (R1): Aehnlichkeit zweier Texte (0..1) auf normalisierter
    Wortfolge - robust gegen Whitespace-/Zeilenumbruch-Unterschiede."""
    import difflib
    na = " ".join((a or "").split())
    nb = " ".join((b or "").split())
    if not na and not nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def _is_repair_noop(original: str, new: str) -> tuple:
    """(no_op, similarity) - siehe Kalibrierung oben."""
    sim = _text_similarity(original, new)
    ow, nw = len((original or "").split()), len((new or "").split())
    delta = abs(nw - ow) / ow if ow else 0.0
    noop = sim >= _REPAIR_NOOP_SIM_HARD or (
        sim >= _REPAIR_NOOP_SIM_SOFT and delta <= _REPAIR_NOOP_WORD_DELTA
    )
    return noop, sim


def _repair_wants_addition(user_hint: str, accepted_codes: list) -> bool:
    """v19.19 (R2): Verlangt der Auftrag eine Ergaenzung (statt Kuerzung/Stil)?"""
    if user_hint and _REPAIR_ADD_HINT_RE.search(user_hint):
        return True
    return any(str(c).startswith(("MISSING_", "MODALITY_NOT_COVERED", "PROZESSREFLEXION"))
               for c in (accepted_codes or []))


async def _run_repair_coroutine(
    job,            # JobState - Forward-Reference (kein circular import)
    workflow: str,
    final_prompt: str,
    model_override: Optional[str] = None,
    original_text: str = "",
    user_hint: str = "",
    accepted_codes: Optional[list] = None,
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

    # ── v19.19 (R1): No-op-Detektor + verschaerfter zweiter Versuch ──
    # Log-Analyse 13.08.-09.09.: 3 von 8 Repairs byte-identisch (auch bei
    # klarem Hinweis wie 'Paargespraech mitaufnehmen'). Entscheid F5: einmal
    # mit haerterer Anweisung wiederholen; bleibt es identisch -> Original
    # zurueckgeben + CRITICAL REPAIR_NO_CHANGE (QC).
    repair_flags: dict = {"attempts": 1}
    has_instructions = bool((user_hint or "").strip()) or bool(accepted_codes)
    noop, sim = _is_repair_noop(original_text, raw) if original_text else (False, 0.0)
    repair_flags["similarity"] = round(sim, 3)
    if original_text and has_instructions and noop:
        logger.warning(
            "Repair %s: Output ~identisch mit Original (sim=%.3f) - verschaerfter "
            "zweiter Versuch", job.job_id[:8], sim,
        )
        job.set_progress(55, "Repair", "Zweiter Versuch (keine Aenderung erkannt)")
        _hard = (
            final_prompt
            + "\n\n>>>ZWEITER VERSUCH<<<\n"
            "Dein erster Versuch hat den Text NICHT veraendert - er war mit dem "
            "Original identisch. Das ist KEIN akzeptables Ergebnis. Die "
            "UEBERARBEITUNGS-HINWEISE bzw. der NUTZERHINWEIS oben MUESSEN jetzt "
            "sichtbar umgesetzt werden: Betroffene Absaetze umschreiben, verlangte "
            "Inhalte aus den QUELLE-Bloecken ergaenzen, verlangte Formulierungen "
            "aendern. Gib den vollstaendigen ueberarbeiteten Text aus.\n"
            ">>>/ZWEITER VERSUCH<<<"
        )
        _log_prompt(job.job_id, workflow, "repair_retry", REPAIR_SYSTEM_PROMPT, _hard)
        result2 = await generate_text(
            REPAIR_SYSTEM_PROMPT, _hard, max_tokens=max_tok, model=model_override,
            workflow=workflow, on_progress=_on_tok, force_hard_no_think=True,
            temperature_override=0.35,
        )
        raw2 = (result2.get("text") or "").strip()
        _log_output(job.job_id, workflow, "repair_retry", raw2, result2.get("telemetry"))
        repair_flags["attempts"] = 2
        noop2, sim2 = _is_repair_noop(original_text, raw2)
        repair_flags["similarity"] = round(sim2, 3)
        if raw2 and not noop2:
            raw, result = raw2, result2
        else:
            logger.error(
                "Repair %s: auch zweiter Versuch ohne Aenderung (sim=%.3f) - "
                "Original wird zurueckgegeben", job.job_id[:8], sim2,
            )
            repair_flags["no_change"] = True
            raw = original_text

    # ── v19.19 (R2): Schrumpf-Check bei Ergaenzungs-Auftrag ──
    if original_text and not repair_flags.get("no_change") \
            and _repair_wants_addition(user_hint, accepted_codes or []):
        ow, nw = len(original_text.split()), len(raw.split())
        if ow and nw < ow * 0.9:
            repair_flags.update({"shrunk": True, "orig_words": ow, "new_words": nw})
            logger.warning(
                "Repair %s: Ergaenzung verlangt, Output aber kuerzer (%d -> %d Woerter)",
                job.job_id[:8], ow, nw,
            )

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
            "repair_flags":    repair_flags,  # v19.19 (R1/R2) -> QC
        },
        # Stage 1 laeuft beim Repair definitiv nicht:
        "verlauf_summary_text":  None,
        "verlauf_summary_audit": None,
    }
