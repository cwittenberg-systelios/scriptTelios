"""
Generierungspfad workflow="sns_verlauf" (v19.41).

Aufruf aus generation_pipeline.run_generation() (frueher Return wie ISM).
Schritte: Parsen -> (Zuordnung ohne XML, D10=B) -> deterministische Analyse
-> Grafiken -> Pseudonymisierung -> Stage A -> Flags -> Faktenblock -> Stage B
-> Phasennamen -> Ergebnis-JSON (result["text"]).

Das Ergebnis-JSON enthaelt alles, was Vorschau, QC und DOCX brauchen
(F1=B: Grafiken als base64-PNG im Ergebnis, keine Dateiablage).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.core.workflows import max_tokens_for, word_limit_for
from app.services import sns_llm, sns_plots, sns_verlauf
from app.services.llm import generate_text
from app.services.prompt_log import _log_output, _log_prompt

logger = logging.getLogger(__name__)

WORKFLOW = "sns_verlauf"
RESULT_VERSION = 1


def _decode(b: Optional[bytes]) -> Optional[str]:
    if not b:
        return None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def kuerzel_und_anrede(patientenname: Optional[str], geschlecht_norm: Optional[str]) -> tuple[str, str]:
    """'Frau K.' -> ('Frau K.', 'Klientin'); Anrede aus Geschlecht, sonst aus dem Kuerzel."""
    ref = (patientenname or "").strip()
    g = (geschlecht_norm or "").lower()
    if not g:
        g = "w" if ref.lower().startswith("frau") else ("m" if ref.lower().startswith("herr") else "")
    anrede = "Klient" if g == "m" else "Klientin"
    return ref or "Klient:in", anrede


async def run_sns_generation(*, job, ctx, generate=generate_text) -> dict:
    hsf_text = _decode(ctx.sns_hsf_bytes)
    if not hsf_text or not hsf_text.strip():
        raise RuntimeError("Kein HSF-Export hochgeladen - die SNS-Verlaufsauswertung braucht "
                           "mindestens die Zeitreihen-CSV des HSF-Basisbogens.")
    ind_text = _decode(ctx.sns_ind_bytes)
    xml_text = _decode(ctx.sns_xml_bytes)
    doc_text = _decode(ctx.sns_doc_bytes)
    kuerzel, anrede = kuerzel_und_anrede(ctx.patientenname, ctx.geschlecht_norm)
    model = ctx.model

    def _cancel():
        if getattr(job, "_cancel_requested", False):
            raise RuntimeError("__CANCELLED__")

    # ── 1. Parsen + Zuordnung ──────────────────────────────────────────────
    job.set_progress(5, "SNS-Daten", "Exporte lesen")
    try:
        hsf = sns_verlauf.parse_sns_csv(hsf_text)
        ind = sns_verlauf.parse_sns_csv(ind_text) if ind_text and ind_text.strip() else None
    except ValueError as e:
        raise RuntimeError(f"SNS-Export nicht lesbar: {e}") from e
    if hsf.questionnaire and sns_verlauf.HSF_QUESTIONNAIRE_NAME.lower() not in hsf.questionnaire.lower():
        logger.warning("sns_verlauf [%s]: HSF-CSV heisst '%s' (erwartet '%s')",
                       job.job_id, hsf.questionnaire, sns_verlauf.HSF_QUESTIONNAIRE_NAME)

    ind_fb = None
    zuordnung = None
    if ind is not None:
        if xml_text and xml_text.strip():
            try:
                ind_fb = sns_verlauf.parse_sns_questionnaire_xml(xml_text)
            except ValueError as e:
                raise RuntimeError(f"Fragebogen-XML nicht lesbar: {e}") from e
        else:
            _cancel()
            job.set_progress(8, "SNS-Daten", "Faktorzuordnung erschließen (kein XML)")
            sysp, userp = sns_llm.build_zuordnung_prompt(ind.items)
            _log_prompt(job.job_id, WORKFLOW, "sns_zuordnung", sysp, userp)
            zuordnung = await sns_llm.run_zuordnung(ind.items, generate=generate, model=model)
            _log_output(job.job_id, WORKFLOW, "sns_zuordnung", json.dumps(zuordnung, ensure_ascii=False), None)
            ind_fb = sns_verlauf.build_erschlossenen_fragebogen(ind.items, zuordnung)

    # ── 2. Analyse + Grafiken ──────────────────────────────────────────────
    job.set_progress(12, "Analyse", "Faktoren, Komplexität, Übergänge")
    a = sns_verlauf.analyse(hsf_text, ind_text, None, doc_text, ind_fb=ind_fb)
    entries_raw = sns_verlauf.diary_entries(a)
    entries, namen = sns_llm.pseudonymisiere(entries_raw, vorname=ctx.sns_vorname, kuerzel=kuerzel)

    # ── 3. Stage A ─────────────────────────────────────────────────────────
    _cancel()
    ism_items = a.fakten["ism"]["items"] if a.fakten.get("ism") else None
    besetzt = {f["id"] for f in a.ism_faktoren if f["besetzt"]}
    n_batches = max(1, (len(entries) + sns_llm.STAGE_A_BATCH - 1) // sns_llm.STAGE_A_BATCH)
    job.set_progress(18, "Tagebuch", f"Stage A: {len(entries)} Einträge in {n_batches} Paketen")
    _log_prompt(job.job_id, WORKFLOW, "sns_stage_a", sns_llm.build_stage_a_system_prompt(ism_items),
                sns_llm.build_stage_a_user(entries[:sns_llm.STAGE_A_BATCH]))

    def _on_batch(k, n):
        _cancel()
        job.set_progress(18 + int(42 * k / n), "Tagebuch", f"Stage A: Paket {k}/{n}")

    stage_a = await sns_llm.run_stage_a(entries, ism_items, besetzt, generate=generate, model=model,
                                        on_batch=_on_batch) if entries else []
    _log_output(job.job_id, WORKFLOW, "sns_stage_a", json.dumps(stage_a, ensure_ascii=False)[:20000], None)
    events = sns_llm.stage_a_events(stage_a)

    job.set_progress(62, "Grafiken", "6 Abbildungen rendern")
    grafiken = sns_plots.render_all(a, events)

    # ── 4. Flags + Faktenblock + Stage B ───────────────────────────────────
    flags = sns_llm.flags_nach_stage_a(a, stage_a, entries)
    faktenblock = sns_llm.build_faktenblock(a.fakten, stage_a, flags, anrede, kuerzel)
    limits = word_limit_for(WORKFLOW, fallback=(1000, 1800))
    max_tok = max_tokens_for(WORKFLOW, fallback=6000)
    system = sns_llm.build_stage_b_system_prompt(ctx.instructions, limits)
    user = sns_llm.build_stage_b_user(faktenblock)

    def _on_tok(n):
        job.set_progress(65 + int(30 * min(1.0, n / max(1, limits[1]))), "Bericht", f"Stage B: {n} Wörter")

    _cancel()
    job.set_progress(65, "Bericht", "Stage B: Interpretation")
    _log_prompt(job.job_id, WORKFLOW, "sns_stage_b", system, user)
    res = await generate(system, user, max_tokens=max_tok, model=model, workflow=WORKFLOW,
                         on_progress=_on_tok, max_words=limits[1])
    text = (res.get("text") or "").strip()
    _log_output(job.job_id, WORKFLOW, "sns_stage_b", text, res.get("telemetry"))
    if not text:
        raise RuntimeError("Stage B lieferte keinen Text.")
    text = re.sub(r"\bhypnosystemisch(e[nrs]?)?\b", "systemisch\\1", text, flags=re.I)  # v19.30-Regel
    phasen = sns_llm.phasen_namen_anwenden(text, a.fakten["phasen"])

    result = {
        "version": RESULT_VERSION,
        "kuerzel": kuerzel,
        "anrede": anrede,
        "text": text,
        "faktenblock": faktenblock,
        "fakten": a.fakten,
        "phasen": phasen,
        "stage_a": stage_a,
        "flags": flags["flags"],
        "flag_details": flags["details"],
        "grafiken": grafiken,
        "namen": namen,
        "quelle": entries,
        "zuordnung": zuordnung,
        "hinweise": a.hinweise,
    }
    result_json = json.dumps(result, ensure_ascii=False)
    job.set_progress(97, "Fertigstellen", "Ergebnis speichern")

    tel = res.get("telemetry") or {}
    return {
        "text": result_json,
        "befund_text": None,
        "akut_text": None,
        "transcript": None,
        "model_used": res.get("model_used"),
        "style_info": None,
        "ocr_warnings": None,
        "generation_telemetry": {
            **tel,
            "retry_used": res.get("retry_used", False),
            "degraded": res.get("degraded", False),
            "degraded_reason": res.get("degraded_reason"),
            "sns_messtage": a.fakten["zeitraum"]["messtage_hsf"],
            "sns_stage_a_batches": n_batches,
            "sns_stage_a_events": len(events),
            "sns_ind_bogen": ind is not None,
            "sns_zuordnung_quelle": (a.fakten.get("ism") or {}).get("quelle") if ind is not None else None,
            "sns_grafiken": len(grafiken),
            "sns_result_bytes": len(result_json),
        },
    }
