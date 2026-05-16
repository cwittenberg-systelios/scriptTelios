"""
Stage 1 fuer Sitzungs-Transkripte (v19.3).

Verdichtet ein Whisper-Transkript auf ~20% Laenge mit 3-Sektionen-Struktur,
damit es nicht von _sample_uniformly in llm.py willkuerlich gekuerzt wird
(_sample_uniformly schneidet in 10 Fenstern mit Luecken — sichtbar im Eval
durch fehlende Keywords aus Transkript-Ende, z.B. "Vorstellungsanlass",
"Ressourcen" bei an-02-schulangst).

Designprinzipien (analog verlauf_summary.py v19.2.2):
  - Quellentreue: Verdichten, nicht erfinden.
  - Kein BASE_PROMPT, kein Workflow-spezifisches Stilbeispiel.
  - Anti-Think-Schutz: System-Prompt-Anhang + /no_think doppelt im User-Content.
  - Temperatur 0.4 (bricht deterministische Reasoning-Loops bei Qwen3).
  - Halluzinations-Detektion (ICD-, Verfahren-, Zitat-Wortlaut-, Sitzungs-
    anzahl-Erfindung) wird aus verlauf_summary wiederverwendet — die Quelle
    (Transkript) ist anders strukturiert als eine Verlaufsdoku, aber die
    Halluzinations-Muster sind dieselben.
  - Max EIN Retry, der entweder Laengen- ODER Halluzinations-Problem
    addressiert (oder beides gleichzeitig).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


# Wir-Stil fuer Antrags-Workflows. Da Transkript-Stage-1 in v19.3 nur fuer
# dokumentation/anamnese aktiv ist (jobs.py _TRANSCRIPT_STAGE1_WORKFLOWS),
# greift der Wir-Stil aktuell nicht aktiv. Bleibt als Future-Proofing
# falls die Whitelist spaeter erweitert wird.
WIR_WORKFLOWS = {"akutantrag", "folgeverlaengerung", "verlaengerung", "entlassbericht"}


TRANSCRIPT_SUMMARY_SYSTEM_PROMPT = """Du bist ein klinisches Verdichtungs-System.

DEINE EINZIGE AUFGABE: Du erhaeltst ein woertliches Sitzungstranskript
zwischen Therapeut*innen und Patient*innen. Erstelle eine strukturierte
3-Sektionen-Verdichtung, die als Grundlage fuer die spaetere klinische
Dokumentation dient. Du formulierst NICHT klinisch, du wertest NICHT,
du diagnostizierst NICHT — du verdichtest nur, was im Transkript steht.

ABSOLUTE REGELN — JEDE VERLETZUNG IST EIN FEHLER:

1. VERDICHTEN, NICHT ERFINDEN. Alle inhaltlich relevanten Aussagen muessen
   erhalten bleiben, aber kompakt formuliert. Wenn ein Bereich im Transkript
   nicht vorkommt: Sektion entsprechend kurz halten.

2. KEINE INTERPRETATION. Schreibe NICHT was die Symptome "bedeuten",
   was der Patient "vermutlich" fuehlt, was "typisch" fuer eine Diagnose ist.
   Nur was im Transkript gesagt wurde.

3. KEINE WERTUNG. Keine Adjektive wie "deutlich", "erheblich",
   "tiefgreifend" wenn sie nicht im Transkript stehen. Bleib an den
   Quell-Adjektiven.

4. PATIENTENNAME ALS INITIALE. Verwende "Frau S." / "Herr R." statt
   vollstaendiger Namen.

5. WOERTLICHE ZITATE NUR WENN DIAGNOSTISCH RELEVANT (z.B. Suizidalitaet,
   konkrete Symptom-Schilderung). Sonst paraphrasieren.

6. UNSICHERHEIT KENNZEICHNEN: Wenn im Transkript etwas unklar bleibt,
   schreibe "im Transkript unklar" oder "nicht weiter ausgefuehrt" —
   nicht raten.
"""


TRANSCRIPT_SUMMARY_STRUCTURE = """STRUKTUR DER VERDICHTUNG (EXAKT EINHALTEN):

Schreibe in DREI Abschnitten, jeweils mit Ueberschrift:

### 1. Auftragsklärung & Hauptanliegen
Was bringt der/die Patient/in mit? Konkrete Wuensche, Anlass, Kontext.
Aus den ersten ~20% des Transkripts. 3-6 Saetze.

### 2. Verlauf & inhaltliche Schwerpunkte
Themen, biographische Bezuege, innere Anteile, Affekte, koerperliche
Phaenomene, Beziehungs- und Familiendynamik. Hauptteil der Verdichtung.
Pro Themenblock 2-4 Saetze, ggf. mit Zeitmarker ("zu Beginn der Sitzung",
"in der zweiten Haelfte").

### 3. Vereinbarungen, Einladungen & Befund-relevantes
Konkrete Aufgaben/Uebungen/Vereinbarungen die der/die Therapeut/in mit
dem/der Patient/in getroffen hat. AMDP-relevante Beobachtungen (Stimmung,
Antrieb, Konzentration, Suizidalitaets-Erwaehnung). Aus den letzten ~20%
des Transkripts. 3-6 Saetze.
"""


def _wir_hint(workflow: Optional[str]) -> str:
    """Stil-Hinweis: Wir-Form fuer Antrags-Workflows, sonst neutral-deskriptiv."""
    if workflow and workflow in WIR_WORKFLOWS:
        return 'Schreibe im Wir-Stil ("Wir nehmen Frau S. wahr...").'
    return 'Schreibe im neutral-deskriptiven Stil ("Frau S. berichtet...").'


async def summarize_transcript(
    transcript_text: str,
    workflow: Optional[str],
    *,
    target_words: Optional[int] = None,
    patient_initial: Optional[str] = None,
) -> dict:
    """
    Stage 1 fuer Sitzungstranskripte.

    Args:
        transcript_text: Whisper-Output (roh oder mit Speaker-Tags).
        workflow:        Workflow-Key (typischerweise dokumentation/anamnese).
        target_words:    Zielwortzahl. None = proportional zum Input:
                         min(1500, max(600, raw_words * 0.20))
                         Beispiele:
                           raw= 5000w → target= 1000w
                           raw= 7000w → target= 1400w
                           raw=10000w → target= 1500w (Hard-Cap)
        patient_initial: Optional Patient-Kuerzel (z.B. "v.M.") fuer den Prompt.

    Returns:
        {
          "summary":              str   — die Verdichtung
          "raw_word_count":       int
          "summary_word_count":   int
          "compression_ratio":    float
          "duration_s":           float
          "telemetry":            dict
          "retry_telemetry":      dict
          "retry_used":           bool
          "degraded":             bool — Output war auch nach Retry verdaechtig
                                          (z.B. critical Hallu nicht behoben)
          "issues":               list — detect_summary_hallucination_signals-
                                          Output: dicts mit type/severity/detail
          "target_words":         int
          "min_acceptable":       int
        }

    Raises:
        RuntimeError wenn auch der Retry implausibel kurz ausfaellt. Der
        Aufrufer (jobs.py-Block) faengt das ab und faellt auf das
        Roh-Transkript zurueck (mit dem bekannten Sampling-Risiko).
    """
    # Lokal-Import um Zirkelimport zu vermeiden.
    from app.services.llm import generate_text

    if not transcript_text or not transcript_text.strip():
        raise RuntimeError("Transcript-Stage 1: leeres Transkript")

    raw_words = len(transcript_text.split())

    # target_words und min_acceptable kommen aus staging.py (testbar).
    from app.services.staging import (
        compute_transcript_target_words,
        compute_transcript_min_acceptable,
    )
    if target_words is None:
        target_words = compute_transcript_target_words(raw_words)
        # Beispiele:
        #   raw= 5000w → target=1000w
        #   raw= 7000w → target=1400w
        #   raw=10000w → target=1500w (Hard-Cap)

    min_acceptable = compute_transcript_min_acceptable(target_words)
    max_acceptable = int(target_words * 2.5)

    wir_hint = _wir_hint(workflow)

    # Anti-Think-Schutz analog v19.2.2 (verlauf_summary.py).
    # Eval-Lauf 14.05.2026 zeigte: einmaliges /no_think reicht bei Qwen3:32b
    # nicht — defense in depth durch System-Prompt-Anhang + /no_think doppelt
    # im User-Content + Temperatur 0.4 statt 0.2.
    anti_think_system = (
        "\n\nWICHTIG: KEIN INNERES NACHDENKEN. "
        "Schreibe direkt die Verdichtung. "
        "KEINE <think>-Tags, KEINE Meta-Reflexion, KEINE Vorbemerkungen. "
        "Beginne sofort mit '### 1. Auftragsklärung & Hauptanliegen'."
    )
    system_prompt = (
        TRANSCRIPT_SUMMARY_SYSTEM_PROMPT
        + "\n\n"
        + TRANSCRIPT_SUMMARY_STRUCTURE
        + f"\n\nSTIL: {wir_hint}"
        + anti_think_system
    )

    user_content = (
        "/no_think\n\n"
        + (f"AKTUELLER PATIENT: {patient_initial}\n\n" if patient_initial else "")
        + (f"WORKFLOW-KONTEXT: {workflow}\n\n" if workflow else "")
        + "QUELLE — Sitzungstranskript:\n"
        + ">>>TRANSKRIPT<<<\n"
        + transcript_text
        + "\n>>>/TRANSKRIPT<<<\n\n"
        + "Verdichte dieses Transkript jetzt in drei Sektionen wie vorgegeben. "
        + f"Zielwortzahl: ca. {target_words} Woerter "
        + f"(akzeptiert: {min_acceptable}-{max_acceptable}).\n\n"
        + "/no_think"
    )

    t0 = time.time()
    result = await generate_text(
        system_prompt=system_prompt,
        user_content=user_content,
        # max_tokens-Heuristik: 2.0x target_words (Wort->Token-Faktor 1.3-1.6
        # plus Puffer fuer Section-Header). Floor 2500 fuer kleine Targets.
        max_tokens=max(2500, int(target_words * 2.0)),
        workflow=None,
        # v19.2.2: 0.4 statt 0.2 — bricht Reasoning-Loops.
        temperature_override=0.4,
        # v19.2.1: strict_mode-Dedup, weil thematische Wiederholungen
        # in Verdichtungen strukturell legitim sind (selbes Thema in
        # Section 1+2). deduplicate_paragraphs wird intern aufgerufen.
        skip_aggressive_dedup=True,
    )

    summary = (result.get("text") or "").strip()
    summary_words = len(summary.split()) if summary else 0
    telemetry = result.get("telemetry", {})

    retry_used = False
    retry_telemetry: dict = {}
    degraded = False

    if not summary:
        raise RuntimeError("Transcript-Stage 1: Verdichtung leer")

    # Halluzinations-Detektion: ICDs, Verfahren, Direkt-Zitat-Wendungen,
    # implausible Sitzungs-Anzahl. Wird aus verlauf_summary wiederverwendet —
    # die Muster sind quellen-unabhaengig (Summary erfindet etwas das nicht
    # in der Quelle steht). Bei Transkripten:
    #   - Whisper-Output enthaelt selten "sagte:\"...\""-Wendungen, daher
    #     ist der wortlaut_halluzination-Check besonders trennscharf.
    #   - Sitzungs-Anzahl-Check feuert kaum (Transkript = eine Sitzung),
    #     aber bleibt als Sicherheitsnetz aktiv.
    from app.services.verlauf_summary import detect_summary_hallucination_signals

    issues = detect_summary_hallucination_signals(summary, transcript_text)
    critical_issues = [i for i in issues if i["severity"] == "critical"]
    length_too_short = summary_words < min_acceptable

    if length_too_short or critical_issues:
        # ONE Retry, der je nach Befund Laengen- und/oder Halluzinations-
        # Korrektur anweist. System-Prompt-Anhang erwaehnt Hallu-Issues
        # (falls vorhanden), User-Content den Laengen-Mangel.
        retry_used = True
        retry_reasons = []
        if length_too_short:
            retry_reasons.append(f"zu kurz ({summary_words}w < {min_acceptable}w Min)")
        if critical_issues:
            retry_reasons.append(
                f"critical Halluzinations-Signale: "
                + "; ".join(f"{i['type']}: {i['detail']}" for i in critical_issues)
            )
        logger.warning("Transcript-Stage 1 Retry startet: %s", " | ".join(retry_reasons))

        # System-Prompt: ggf. Hallu-Hinweis anhaengen
        retry_system_prompt = system_prompt
        if critical_issues:
            issue_summary = "; ".join(
                f"{i['type']}: {i['detail']}" for i in critical_issues
            )
            retry_system_prompt = (
                system_prompt
                + "\n\nWICHTIG: In einem vorherigen Versuch traten folgende "
                + f"Halluzinations-Probleme auf: {issue_summary}. "
                + "Vermeide diese diesmal strikt. Wenn du unsicher bist, "
                + "ob etwas in der Quelle steht, lass es weg."
            )

        # User-Content: Laengen-Hinweis (immer wenn Retry triggert, auch wenn
        # nur Hallu schuld war — schadet nicht, das LLM macht trotzdem die
        # vorgegebene Struktur)
        if length_too_short:
            length_hint = (
                f"Der vorherige Versuch war zu kurz "
                f"({summary_words} Woerter). Schreibe diesmal MINDESTENS "
                f"{target_words} Woerter mit allen drei Sektionen vollstaendig."
            )
        else:
            length_hint = (
                f"Behalte beim Retry die Zielwortzahl von ca. {target_words} "
                f"Woertern bei (akzeptiert: {min_acceptable}-{max_acceptable})."
            )
        retry_user = (
            "/no_think\n\n"
            + (f"AKTUELLER PATIENT: {patient_initial}\n\n" if patient_initial else "")
            + (f"WORKFLOW-KONTEXT: {workflow}\n\n" if workflow else "")
            + "QUELLE — Sitzungstranskript:\n"
            + ">>>TRANSKRIPT<<<\n"
            + transcript_text
            + "\n>>>/TRANSKRIPT<<<\n\n"
            + length_hint
            + "\n\n/no_think"
        )

        try:
            retry_result = await generate_text(
                system_prompt=retry_system_prompt,
                user_content=retry_user,
                max_tokens=max(3000, int(target_words * 2.2)),
                workflow=None,
                # 0.3 statt 0.4 - etwas deterministischer beim Retry, aber
                # nicht zu niedrig (sonst greift Anti-Think nicht mehr).
                temperature_override=0.3,
                skip_aggressive_dedup=True,
            )
        except Exception as e:
            logger.error("Transcript-Stage 1 Retry-Call fehlgeschlagen: %s", e)
            raise RuntimeError(
                f"Transcript-Stage 1 Retry-Call fehlgeschlagen: {e}"
            ) from e

        retry_text = (retry_result.get("text") or "").strip()
        retry_telemetry = retry_result.get("telemetry", {})
        retry_words = len(retry_text.split()) if retry_text else 0

        # Entscheidungsbaum nach Retry:
        #   1. Retry leer + initial war zu kurz  → RuntimeError (hart fail)
        #   2. Retry leer + initial nur Hallu    → degraded, behalte Original
        #   3. Retry zu kurz                     → RuntimeError (hart fail)
        #   4. Retry laengen-ok + Hallu sauber   → erfolg, uebernehmen
        #   5. Retry laengen-ok + Hallu critical → degraded, retry uebernehmen
        if not retry_text:
            if length_too_short:
                raise RuntimeError(
                    f"Transcript-Stage 1: Retry leer und Original zu kurz "
                    f"({summary_words}w < {min_acceptable}w)"
                )
            # Hallu-only: Original behalten, Markierung
            degraded = True
            logger.error(
                "Transcript-Stage 1 Retry leer — Original mit %d critical Hallu "
                "wird mit degraded=True zurueckgegeben",
                len(critical_issues),
            )
        elif retry_words < min_acceptable:
            raise RuntimeError(
                f"Transcript-Stage 1: Verdichtung implausibel kurz nach Retry: "
                f"{retry_words}w < {min_acceptable}w Min "
                f"(target={target_words}w, raw={raw_words}w)"
            )
        else:
            # Retry hat plausible Laenge — Hallu re-check
            retry_issues = detect_summary_hallucination_signals(
                retry_text, transcript_text,
            )
            retry_critical = [
                i for i in retry_issues if i["severity"] == "critical"
            ]
            summary = retry_text
            summary_words = retry_words
            issues = retry_issues
            if retry_critical:
                degraded = True
                logger.error(
                    "Transcript-Stage 1 Retry hat critical Halluzinationen nicht "
                    "behoben (%d -> %d critical) — degraded=True",
                    len(critical_issues), len(retry_critical),
                )
            else:
                logger.info(
                    "Transcript-Stage 1 Retry erfolgreich: %dw, "
                    "%d Issues gesamt (kein critical)",
                    summary_words, len(retry_issues),
                )

    if summary_words > max_acceptable:
        logger.warning(
            "Transcript-Stage 1 ueber Soll: %dw vs Target %dw — "
            "_sample_uniformly koennte trotzdem im Hauptcall greifen",
            summary_words, target_words,
        )

    duration_s = round(time.time() - t0, 1)

    return {
        "summary":              summary,
        "raw_word_count":       raw_words,
        "summary_word_count":   summary_words,
        "compression_ratio":    round(summary_words / raw_words, 3) if raw_words else 0.0,
        "duration_s":           duration_s,
        "telemetry":            telemetry,
        "retry_telemetry":      retry_telemetry,
        "retry_used":           retry_used,
        "degraded":             degraded,
        "issues":               issues,
        "target_words":         target_words,
        "min_acceptable":       min_acceptable,
    }
