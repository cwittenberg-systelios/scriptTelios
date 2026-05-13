"""
Stage 1 der Two-Stage-Pipeline: Verlaufsdoku-Verdichtung (v19.2).

Diese Datei produziert eine strukturierte, strikt quellentreue
Zusammenfassung der Verlaufsdokumentation, die dann als Input
für die eigentliche Antrags-Generierung (Stage 2) dient.

Designprinzipien:
  - Quellentreue ueber alles (kein Erfinden, keine Interpretation)
  - Kein Stilbeispiel, kein Workflow-spezifischer BASE_PROMPT
  - Niedrige Temperatur fuer Determinismus
  - Eigener Halluzinations-Detektor + max. EIN Retry
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Stage-1-Prompts (Schritt 1)
# ─────────────────────────────────────────────────────────────────────────────

VERLAUF_SUMMARY_SYSTEM_PROMPT = """Du bist ein Verdichtungssystem für klinische Verlaufsdokumentation.

DEINE EINZIGE AUFGABE: Verdichte das vorliegende Sitzungsprotokoll in eine
strukturierte Zusammenfassung. Du formulierst NICHT klinisch, du wertest NICHT,
du diagnostizierst NICHT, du argumentierst NICHT — du fasst nur zusammen was
in den Quellen steht.

ABSOLUTE REGELN — JEDE VERLETZUNG IST EIN FEHLER:

1. QUELLENTREUE: Schreibe AUSSCHLIESSLICH was wörtlich oder sinngemäß im Quelltext
   steht. Wenn etwas nicht in den Quellen steht, kommt es NICHT in die
   Zusammenfassung. Auch nicht "naheliegende" Schlussfolgerungen.

2. KEINE INTERPRETATION: Schreibe NICHT was die Symptome "bedeuten",
   was der Patient "vermutlich" fühlt, was "typisch" für eine Diagnose ist.
   Nur was dokumentiert wurde.

3. KEINE WERTUNG: Keine Adjektive wie "deutlich", "erheblich", "tiefgreifend"
   wenn sie nicht im Quelltext stehen. Bleib bei den Quell-Adjektiven.

4. ZITAT-NAH BEI FACHBEGRIFFEN: Therapieverfahren (IFS, Stuhlarbeit, EMDR, ...)
   werden NUR übernommen wenn sie im Quelltext namentlich vorkommen. Sonst
   beschreibst du, was gemacht wurde, ohne das Verfahren zu benennen.

5. UNSICHERHEIT KENNZEICHNEN: Wenn aus dem Text unklar bleibt was passiert ist,
   schreibe "im Protokoll unklar" oder "nicht weiter ausgeführt" — nicht raten.

6. SITZUNGS-BEZUG: Wo immer möglich, beziehe Aussagen auf das Sitzungs-Datum
   oder die Seitenzahl ("am 12.01.", "S. 4"). Das ist die Audit-Spur.
"""


VERLAUF_SUMMARY_STRUCTURE = """STRUKTUR DER ZUSAMMENFASSUNG:

Schreibe in vier Abschnitten, jeweils mit Überschrift:

### Sitzungsübersicht
Anzahl und Art der Sitzungen, Zeitraum, Dichte (z.B. "12 Einzelgespräche
und 8 Gruppensitzungen vom 01.01. bis 03.02., zusätzlich 4 nonverbale
Therapien").

### Bearbeitete Themen (chronologisch)
Pro Thema 1–3 Sätze, mit Sitzungs-Datum oder Seitenzahl in Klammern.
Konzentriere dich auf das WAS, nicht das WIE.
Beispiel: "Thema Selbstabwertung in Familienkontext (12.01., 15.01., 22.01.):
Patientin bringt wiederkehrend ein Gefühl ein, in der Familie nicht
gesehen zu werden, insbesondere im Verhältnis zum Bruder."

### Therapeutische Interventionen
Welche Methoden, Techniken, Übungen wurden eingesetzt? Mit Sitzungs-Bezug.
NUR benannte Verfahren übernehmen — nicht eigenständig Verfahrensnamen vergeben.

### Beobachtete Entwicklung
Was hat sich verändert — strikt nach Protokoll. Stabilisierung,
Destabilisierung, neue Symptome, Verschlechterung. Mit Sitzungs-Bezug.
Wenn keine Veränderung beschrieben: "Verlauf im Protokoll weitgehend
gleichbleibend beschrieben."
"""


# ─────────────────────────────────────────────────────────────────────────────
# Workflow-spezifische Fokus-Hinweise
# ─────────────────────────────────────────────────────────────────────────────

def _build_focus_hint(workflow: Optional[str]) -> str:
    """Workflow-spezifischer Hinweis was besonders relevant ist."""
    if not workflow:
        return ""
    return {
        "verlaengerung": (
            "Für einen Verlängerungsantrag relevant: aktuelle Belastung, "
            "begonnene aber noch nicht abgeschlossene therapeutische Prozesse, "
            "Stabilisierungs- und Destabilisierungsmomente, Beziehungsdynamik in der Gruppe."
        ),
        "folgeverlaengerung": (
            "Für eine Folgeverlängerung relevant: Veränderung SEIT dem letzten "
            "Verlängerungsantrag (frühere Phase nur kurz). Welche neuen Themen "
            "sind hinzugekommen, welche bisherigen Fortschritte haben sich gefestigt?"
        ),
        "entlassbericht": (
            "Für einen Entlassbericht relevant: chronologischer Gesamtbogen — "
            "Ausgangslage, Hauptphasen der Behandlung, Wendepunkte, Endzustand. "
            "Auch Episoden die nicht 'erfolgreich' waren gehören dazu."
        ),
    }.get(workflow, "")


# ─────────────────────────────────────────────────────────────────────────────
# Halluzinations-Detektion (Schritt 3)
# ─────────────────────────────────────────────────────────────────────────────

# Bekannte Therapieverfahren, die NIE in der Summary auftauchen duerfen wenn sie
# nicht im Source vorkommen. Conservative Liste — false positives sind ok.
KNOWN_VERFAHREN = (
    "IFS",
    "Anteilearbeit",
    "Stuhlarbeit",
    "EMDR",
    "Schematherapie",
    "Hypnose",
    "Hypnosystemik",
    "DBT",
    "Skills-Training",
    "Achtsamkeit",
    "MBSR",
    "Imagination",
    "Trauma-Konfrontation",
)

_ICD_RE = re.compile(r"\b[FZGH]\d{2}\.\d\b")
_DIRECT_SPEECH_PATS = (
    r'sagte[:,]?\s*"',
    r'äußerte[:,]?\s*"',
    r'berichtete[:,]?\s*"',
)
_COUNT_RE = re.compile(
    r"(\d+)\s+(Einzelgespr(?:ä|ae)che[rn]?|Sitzungen?|Gruppensitzungen?)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")


def detect_summary_hallucination_signals(
    summary: str,
    source_text: str,
) -> list[dict]:
    """
    Prueft ob die Stage-1-Zusammenfassung typische Halluzinations-Anzeichen
    enthaelt. Conservative — false positives sind okay, false negatives gefaehrlich.

    Schweregrade:
      "critical" — quasi unverhandelbar (ICD-Codes erfunden)
      "high"     — eingreifend (Verfahren erfunden)
      "medium"   — verdaechtig (Patienten-Zitat-Wendungen erfunden,
                                Sitzungs-Anzahl implausibel)

    Returns:
        Liste von Issue-Dicts mit Feldern type, severity, detail.
        Leere Liste = okay.
    """
    issues: list[dict] = []

    if not summary or not source_text:
        return issues

    summary_low = summary.lower()
    source_low = source_text.lower()

    # 1. Verfahrens-Halluzinationen (high)
    for v in KNOWN_VERFAHREN:
        if v.lower() in summary_low and v.lower() not in source_low:
            issues.append({
                "type": "verfahren_halluzination",
                "severity": "high",
                "detail": f"Verfahren '{v}' in Zusammenfassung aber nicht in Quelle",
            })

    # 2. ICD-Halluzinationen (critical)
    summary_icds = set(_ICD_RE.findall(summary))
    source_icds = set(_ICD_RE.findall(source_text))
    for icd in sorted(summary_icds - source_icds):
        issues.append({
            "type": "icd_halluzination",
            "severity": "critical",
            "detail": f"ICD-Code '{icd}' in Zusammenfassung aber nicht in Quelle",
        })

    # 3. Patienten-Zitat-Wendungen (medium)
    for pat in _DIRECT_SPEECH_PATS:
        if re.search(pat, summary) and not re.search(pat, source_text):
            issues.append({
                "type": "wortlaut_halluzination",
                "severity": "medium",
                "detail": f"Direktes Zitat-Muster '{pat}' in Summary aber nicht in Quelle",
            })

    # 4. Sitzungs-Anzahl-Plausibilitaet (medium)
    summary_counts = _COUNT_RE.findall(summary)
    source_dates = _DATE_RE.findall(source_text)
    if summary_counts and source_dates:
        for count_str, _kind in summary_counts:
            try:
                n = int(count_str)
            except ValueError:
                continue
            # Wenn behauptete Zahl deutlich groesser als die im Source vorhandenen
            # Datums-Anker (Faktor 3 als Sicherheitspuffer), Verdacht melden.
            if n > len(source_dates) * 3 and n > 5:
                issues.append({
                    "type": "anzahl_implausibel",
                    "severity": "medium",
                    "detail": (
                        f"{n} Sitzungen genannt, aber nur {len(source_dates)} "
                        f"Datums-Marker in Quelle"
                    ),
                })
                break  # ein Treffer reicht

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Hauptfunktion: Stage-1-Service (Schritte 2 + 3 + 4)
# ─────────────────────────────────────────────────────────────────────────────

async def summarize_verlauf(
    verlauf_text: str,
    workflow: Optional[str],
    patient_initial: Optional[str] = None,
    *,
    target_words: int = 4000,
) -> dict:
    """
    Stage 1 der Two-Stage-Pipeline.

    Verdichtet rohe Verlaufsdoku auf eine strukturierte Synthese,
    die dann als Input für Stage 2 (Antrags-Generierung) dient.

    Args:
        verlauf_text:     Bereinigter Verlaufsdoku-Text (nach clean_verlauf_text)
        workflow:         Workflow-Key (verlaengerung/folgeverlaengerung/
                          entlassbericht); steuert focus_hint
        patient_initial:  Patient-Kuerzel fuer Anrede in der Quelle
        target_words:     Ziel-Wortzahl der Zusammenfassung

    Returns:
        {
          "summary":              str   — die Zusammenfassung (gereinigt)
          "raw_word_count":       int   — Wortzahl Input
          "summary_word_count":   int   — Wortzahl Output
          "compression_ratio":    float — summary/raw
          "duration_s":           float — Gesamtdauer (Stage 1 + ggf. Retry)
          "telemetry":            dict  — Telemetrie aus llm.generate_text
          "issues":               list  — gefundene Halluzinations-Signale
          "retry_used":           bool
          "degraded":             bool  — Output war auch nach Retry verdaechtig
        }

    Raises:
        RuntimeError wenn auch der initiale Call keinen plausibel langen
        Output liefert (< 40% Zielwortzahl). Der Aufrufer (Pipeline) faengt
        das ab und faellt auf das Original zurueck.
    """
    # Lokal-Import um Zirkelimport zu vermeiden (llm.py kennt diese Datei nicht).
    from app.services.llm import generate_text

    if not verlauf_text or not verlauf_text.strip():
        raise RuntimeError("Stage 1: leerer Verlauf-Text")

    raw_words = len(verlauf_text.split())

    focus_hint = _build_focus_hint(workflow)
    system_prompt = (
        VERLAUF_SUMMARY_SYSTEM_PROMPT
        + "\n\n"
        + VERLAUF_SUMMARY_STRUCTURE
        + (f"\n\nFOCUS: {focus_hint}\n" if focus_hint else "")
    )

    user_content = (
        (f"AKTUELLER PATIENT: {patient_initial}\n\n" if patient_initial else "")
        + "QUELLE — Verlaufsdokumentation:\n"
        + ">>>VERLAUFSDOKU<<<\n"
        + verlauf_text
        + "\n>>>/VERLAUFSDOKU<<<\n\n"
        + f"Verdichte diese Verlaufsdokumentation jetzt. "
        + f"Zielwortzahl: ca. {target_words} Wörter "
        + f"(akzeptiert: {int(target_words*0.6)}–{int(target_words*1.4)})."
    )

    # Erste Generierung — niedrige Temperatur, kein Workflow (kein BASE_PROMPT,
    # kein Primer), eigenes Token-Budget.
    import time as _t
    t0 = _t.time()
    result = await generate_text(
        system_prompt=system_prompt,
        user_content=user_content,
        max_tokens=int(target_words * 1.5),  # Wort->Token-Puffer
        workflow=None,
        temperature_override=0.2,
    )

    summary = (result.get("text") or "").strip()
    summary_words = len(summary.split()) if summary else 0

    min_acceptable = int(target_words * 0.4)
    max_acceptable = int(target_words * 2.0)

    if not summary:
        raise RuntimeError("Stage 1: Zusammenfassung leer")
    if summary_words < min_acceptable:
        raise RuntimeError(
            f"Stage 1: Zusammenfassung implausibel kurz: {summary_words}w "
            f"< {min_acceptable}w Minimum (target={target_words}w)"
        )
    if summary_words > max_acceptable:
        logger.warning(
            "Stage 1 ueber Soll: %dw vs Target %dw — Stage 2 bekommt mehr Input",
            summary_words, target_words,
        )

    # Halluzinations-Check + ggf. EIN Retry bei critical issues
    issues = detect_summary_hallucination_signals(summary, verlauf_text)
    critical_issues = [i for i in issues if i["severity"] == "critical"]
    retry_used = False
    degraded = False
    retry_telemetry: dict = {}

    if critical_issues:
        logger.warning(
            "Stage 1 critical Halluzinations-Signal(e): %s — Retry startet",
            critical_issues,
        )
        retry_summary, retry_telemetry = await _retry_stricter_summary(
            verlauf_text=verlauf_text,
            workflow=workflow,
            patient_initial=patient_initial,
            target_words=target_words,
            previous_issues=critical_issues,
        )
        retry_used = True

        if retry_summary:
            retry_issues = detect_summary_hallucination_signals(
                retry_summary, verlauf_text,
            )
            retry_critical = [
                i for i in retry_issues if i["severity"] == "critical"
            ]
            retry_words = len(retry_summary.split())
            if not retry_critical and retry_words >= min_acceptable:
                # Retry hat geliefert UND ist sauber → nehmen
                logger.info(
                    "Stage 1 Retry erfolgreich: %d Issues -> %d (kein critical mehr)",
                    len(critical_issues), len(retry_issues),
                )
                summary = retry_summary
                summary_words = retry_words
                issues = retry_issues
            else:
                # Retry konnte das Problem nicht loesen — Original behalten,
                # aber als degraded markieren
                logger.error(
                    "Stage 1 Retry hat critical Halluzinationen nicht behoben "
                    "(retry_critical=%d, retry_words=%d). Original-Summary "
                    "wird mit degraded=True zurueckgegeben.",
                    len(retry_critical), retry_words,
                )
                degraded = True
        else:
            logger.error("Stage 1 Retry lieferte leeren Output — degraded.")
            degraded = True

    duration_s = round(_t.time() - t0, 1)

    return {
        "summary":              summary,
        "raw_word_count":       raw_words,
        "summary_word_count":   summary_words,
        "compression_ratio":    round(summary_words / raw_words, 3) if raw_words else 0.0,
        "duration_s":           duration_s,
        "telemetry":            result.get("telemetry", {}),
        "retry_telemetry":      retry_telemetry,
        "issues":               issues,
        "retry_used":           retry_used,
        "degraded":             degraded,
    }


async def _retry_stricter_summary(
    verlauf_text: str,
    workflow: Optional[str],
    patient_initial: Optional[str],
    target_words: int,
    previous_issues: list[dict],
) -> tuple[str, dict]:
    """
    Zweiter Versuch wenn Stage 1 critical Halluzinations-Signale hatte.

    Strengeres Prompt mit expliziter Erwähnung der gefundenen Issues.
    Temperatur noch niedriger (0.1) fuer maximale Reproduktion.
    Maximal EIN Retry, kein Loop.

    Returns:
        (retry_summary, retry_telemetry)
    """
    from app.services.llm import generate_text

    issue_summary = "; ".join(
        f"{i['type']}: {i['detail']}" for i in previous_issues
    )

    focus_hint = _build_focus_hint(workflow)
    system_prompt = (
        VERLAUF_SUMMARY_SYSTEM_PROMPT
        + "\n\n"
        + VERLAUF_SUMMARY_STRUCTURE
        + (f"\n\nFOCUS: {focus_hint}\n" if focus_hint else "")
        + "\n\n"
        + "WICHTIG: In einem vorherigen Versuch traten folgende "
        + f"Halluzinations-Probleme auf: {issue_summary}. "
        + "Vermeide diese diesmal strikt. Wenn du unsicher bist ob etwas in "
        + "der Quelle steht, lass es weg."
    )

    user_content = (
        (f"AKTUELLER PATIENT: {patient_initial}\n\n" if patient_initial else "")
        + "QUELLE — Verlaufsdokumentation:\n"
        + ">>>VERLAUFSDOKU<<<\n"
        + verlauf_text
        + "\n>>>/VERLAUFSDOKU<<<\n\n"
        + f"Verdichte diese Verlaufsdokumentation jetzt. "
        + f"Zielwortzahl: ca. {target_words} Wörter "
        + f"(akzeptiert: {int(target_words*0.6)}–{int(target_words*1.4)})."
    )

    try:
        result = await generate_text(
            system_prompt=system_prompt,
            user_content=user_content,
            max_tokens=int(target_words * 1.5),
            workflow=None,
            temperature_override=0.1,  # noch niedriger
        )
    except Exception as e:
        logger.error("Stage 1 Retry-Call fehlgeschlagen: %s", e)
        return "", {}

    retry_summary = (result.get("text") or "").strip()
    return retry_summary, result.get("telemetry", {})
