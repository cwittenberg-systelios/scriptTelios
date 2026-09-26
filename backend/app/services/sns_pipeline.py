"""
Generierungspfad workflow="sns_verlauf" (v19.41).

Aufruf aus generation_pipeline.run_generation() (frueher Return wie ISM).
Schritte: Userexport + XML parsen -> deterministische Analyse
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
    if not ctx.sns_export_bytes:
        raise RuntimeError("Kein SNS-Userexport hochgeladen (.xlsx).")
    xml_text = _decode(ctx.sns_xml_bytes)
    if not xml_text or not xml_text.strip():
        raise RuntimeError("Kein Fragebogen-XML des individuellen Bogens hochgeladen.")
    kuerzel, anrede = kuerzel_und_anrede(ctx.patientenname, ctx.geschlecht_norm)
    model = ctx.model

    def _cancel():
        if getattr(job, "_cancel_requested", False):
            raise RuntimeError("__CANCELLED__")

    # ── 1. Parsen + 2. Analyse ─────────────────────────────────────────────
    job.set_progress(5, "SNS-Daten", "Userexport und Fragebogen-XML lesen")
    try:
        a = sns_verlauf.analyse_userexport(ctx.sns_export_bytes, xml_text)
    except ValueError as e:
        raise RuntimeError(f"SNS-Daten nicht auswertbar: {e}") from e
    job.set_progress(12, "Analyse", "Faktoren, Komplexität, Übergänge")
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

    personen: dict[str, str] = {}
    stage_a = await sns_llm.run_stage_a(entries, ism_items, besetzt, generate=generate, model=model,
                                        on_batch=_on_batch, personen=personen) if entries else []
    # Namen ohne Anrede (Mitklient:innen, Angehoerige) aus Stage A -> Rolle, in Quelle und Ereignissen
    entries, stage_a = sns_llm.namen_ersetzen(entries, stage_a, personen)
    namen = sorted(set(namen) | set(personen))
    _log_output(job.job_id, WORKFLOW, "sns_stage_a", json.dumps(stage_a, ensure_ascii=False)[:20000], None)
    events = sns_llm.stage_a_events(stage_a)

    job.set_progress(62, "Grafiken", "6 Abbildungen rendern")
    grafiken = sns_plots.render_all(a, events)

    # ── 4. Flags + Faktenblock + Stage B ───────────────────────────────────
    flags = sns_llm.flags_nach_stage_a(a, stage_a, entries)
    faktenblock = sns_llm.build_faktenblock(a.fakten, stage_a, flags, anrede, kuerzel)
    limits = word_limit_for(WORKFLOW, fallback=(1600, 2600))
    max_tok = max_tokens_for(WORKFLOW, fallback=9000)
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
    text = re.sub(r"\bklient(in)?\b", lambda m: "Klient" + (m.group(1) or ""), text)  # Anrede gross
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
            "sns_polung_korrigiert": sum(1 for c in a.fakten["polung_check"] if c.get("korrigiert")),
            "sns_grafiken": len(grafiken),
            "sns_result_bytes": len(result_json),
        },
    }
