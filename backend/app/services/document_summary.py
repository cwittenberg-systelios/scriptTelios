"""
Generischer quellentreuer Dokument-Verdichter (v19.4).

Zweck: Der kombinierte Input-Budget-Guard in jobs.py (siehe
_apply_input_budget_guard) braucht einen Verdichter fuer Quellen, fuer die
es bisher KEINE Stage-1-Pipeline gab — vor allem Selbstauskunft und
Vorbefunde im Anamnese-Workflow, sowie als letzte Reserve Antragsvorlagen
und Verlaeufe, deren Stage-1 zurueckgefallen ist.

Abgrenzung zu transcript_summary.py / verlauf_summary.py:
  - Jene erzwingen eine feste 3-Sektionen-Struktur (transkript- bzw.
    verlaufs-spezifisch). Dieser hier gibt KEINE Struktur vor — er verdichtet
    Fliesstext treu auf eine Zielwortzahl und behaelt alle klinisch
    relevanten Fakten (Symptome, Biographie, Diagnosen, Medikation, Daten).
  - Gleiches Anti-Think-Regime (force_hard_no_think) und gleiche
    Halluzinations-Detektion (detect_summary_hallucination_signals aus
    verlauf_summary) werden wiederverwendet.

Designprinzip wie ueberall: Verdichten, nicht erfinden. Bei Misserfolg wirft
die Funktion RuntimeError; der Aufrufer faellt dann auf den Roh-Text zurueck
(mit dem bekannten _sample_uniformly-Sampling-Risiko in llm.py).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from app.services.summary_runner import (
    anti_think_suffix, source_block, stage1_generate, wrap_no_think, word_count,
)

logger = logging.getLogger(__name__)


DOCUMENT_SUMMARY_SYSTEM_PROMPT = """Du bist ein klinisches Verdichtungssystem.

DEINE EINZIGE AUFGABE: Du erhaeltst ein klinisches Quelldokument
(Selbstauskunft, Vorbefund, Antragsvorlage o.ae.). Verdichte es treu auf die
vorgegebene Zielwortzahl. Du formulierst NICHT klinisch um, du wertest NICHT,
du diagnostizierst NICHT, du argumentierst NICHT — du fasst nur zusammen, was
im Dokument steht.

ABSOLUTE REGELN — JEDE VERLETZUNG IST EIN FEHLER:

1. QUELLENTREUE: Schreibe AUSSCHLIESSLICH, was woertlich oder sinngemaess im
   Quelltext steht. Nichts hinzufuegen, auch keine "naheliegenden"
   Schlussfolgerungen.

2. ALLE FAKTEN BEHALTEN: Symptome, biographische Angaben, Bezugspersonen,
   Diagnosen und ICD-Codes, Medikation, Vorbehandlungen, Daten/Zeitraeume und
   konkrete Schilderungen bleiben erhalten — nur kompakter formuliert.
   Streiche Redundanz und Floskeln, NICHT Inhalt.

3. KEINE INTERPRETATION / KEINE WERTUNG: Keine Adjektive wie "deutlich",
   "erheblich", "auffaellig", wenn sie nicht im Quelltext stehen. Bleib an den
   Quell-Formulierungen.

4. PATIENTENBEZEICHNUNG: Falls ein aktueller Patient angegeben ist, verwende
   genau diese Bezeichnung. Andernfalls neutral "die Patientin/der Patient" —
   ERFINDE KEINEN Namen und KEINE Initiale.

5. UNSICHERHEIT KENNZEICHNEN: Wenn etwas im Dokument unklar bleibt, schreibe
   "im Dokument unklar" oder "nicht weiter ausgefuehrt" — nicht raten.

6. KEINE STRUKTURVORGABE: Schreibe zusammenhaengenden Fliesstext oder knappe
   thematische Absaetze. KEINE erfundenen Ueberschriften, KEINE Tabellen,
   KEINE Vorbemerkungen oder Meta-Saetze ueber die Verdichtung selbst.
"""


# Dokument-Verdichtung: 0.3 (etwas deterministischer als Verlauf/Transkript).
_TEMPERATURE = 0.3
_ANTI_THINK = anti_think_suffix("Verdichtung")


async def summarize_document(
    text: str,
    *,
    target_words: int,
    doc_label: str = "Dokument",
    workflow: Optional[str] = None,
    patient_initial: Optional[str] = None,
) -> dict:
    """
    Verdichtet ein beliebiges klinisches Quelldokument treu auf ~target_words.

    Args:
        text:            Roh-Text der Quelle.
        target_words:    Zielwortzahl der Verdichtung.
        doc_label:       Anzeige-Label fuer den Prompt (z.B. "Selbstauskunft").
        workflow:        Optionaler Workflow-Kontext (nur informativ im Prompt).
        patient_initial: Optionale Patientenbezeichnung (z.B. "Frau M.").

    Returns:
        {
          "summary":            str,
          "raw_word_count":     int,
          "summary_word_count": int,
          "compression_ratio":  float,
          "duration_s":         float,
          "retry_used":         bool,
          "degraded":           bool,
          "issues":             list,
          "target_words":       int,
        }

    Raises:
        RuntimeError wenn die Verdichtung leer ist oder auch nach Retry
        implausibel kurz bleibt.
    """
    from app.services.verlauf_summary import detect_summary_hallucination_signals

    if not text or not text.strip():
        raise RuntimeError(f"Dokument-Verdichtung ({doc_label}): leerer Input")

    # v19.5.2: dediziertes, garantiert geladenes Verdichtungsmodell (nicht mehr
    # der stille OLLAMA_MODEL-Default -> kein Ollama-404 durch stale Config).

    raw_words = word_count(text)
    target_words = max(200, int(target_words))
    min_acceptable = max(150, int(target_words * 0.35))
    max_acceptable = int(target_words * 2.5)

    system_prompt = DOCUMENT_SUMMARY_SYSTEM_PROMPT + _ANTI_THINK

    def _build_user(extra_hint: str = "") -> str:
        return wrap_no_think(
            source_block(label=doc_label, tag="DOKUMENT", text=text,
                         patient_initial=patient_initial, workflow=workflow)
            + f"Verdichte dieses Dokument jetzt treu auf ca. {target_words} Woerter "
            + f"(akzeptiert: {min_acceptable}-{max_acceptable}). "
            + "Behalte alle klinisch relevanten Fakten."
            + (f" {extra_hint}" if extra_hint else "")
        )

    t0 = time.time()
    result = await stage1_generate(
        system_prompt, _build_user(),
        max_tokens=max(2500, int(target_words * 2.0)), temperature=_TEMPERATURE,
    )

    summary = (result.get("text") or "").strip()
    summary_words = len(summary.split()) if summary else 0
    retry_used = False
    degraded = False

    if not summary:
        raise RuntimeError(f"Dokument-Verdichtung ({doc_label}): leeres Ergebnis")

    issues = detect_summary_hallucination_signals(summary, text)
    critical = [i for i in issues if i.get("severity") == "critical"]
    too_short = summary_words < min_acceptable

    if too_short or critical:
        retry_used = True
        hint = (
            f"Der vorherige Versuch war zu kurz ({summary_words} Woerter). "
            f"Schreibe diesmal MINDESTENS {target_words} Woerter."
            if too_short else
            "Vermeide diesmal jede Aussage, die nicht im Dokument steht."
        )
        try:
            retry_result = await stage1_generate(
                system_prompt, _build_user(hint),
                max_tokens=max(3000, int(target_words * 2.2)), temperature=_TEMPERATURE,
            )
        except Exception as e:
            raise RuntimeError(
                f"Dokument-Verdichtung ({doc_label}): Retry-Call fehlgeschlagen: {e}"
            ) from e

        retry_text = (retry_result.get("text") or "").strip()
        retry_words = len(retry_text.split()) if retry_text else 0

        if not retry_text:
            if too_short:
                raise RuntimeError(
                    f"Dokument-Verdichtung ({doc_label}): Retry leer und Original "
                    f"zu kurz ({summary_words}w < {min_acceptable}w)"
                )
            degraded = True  # Hallu-only: Original behalten
        elif retry_words < min_acceptable:
            raise RuntimeError(
                f"Dokument-Verdichtung ({doc_label}): implausibel kurz nach Retry "
                f"({retry_words}w < {min_acceptable}w, target={target_words}w)"
            )
        else:
            summary = retry_text
            summary_words = retry_words
            issues = detect_summary_hallucination_signals(retry_text, text)
            if any(i.get("severity") == "critical" for i in issues):
                degraded = True

    duration_s = round(time.time() - t0, 1)
    logger.info(
        "Dokument-Verdichtung (%s): %dw -> %dw (Kompression %.0f%%), retry=%s, degraded=%s",
        doc_label, raw_words, summary_words,
        (1 - (summary_words / raw_words)) * 100 if raw_words else 0.0,
        retry_used, degraded,
    )

    return {
        "summary":            summary,
        "raw_word_count":     raw_words,
        "summary_word_count": summary_words,
        "compression_ratio":  round(summary_words / raw_words, 3) if raw_words else 0.0,
        "duration_s":         duration_s,
        "retry_used":         retry_used,
        "degraded":           degraded,
        "issues":             issues,
        "target_words":       target_words,
    }
