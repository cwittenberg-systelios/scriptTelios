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
   und Anteilnamen ("Türsteher", "Wächter", ...) werden NUR übernommen wenn
   sie im Quelltext namentlich vorkommen. Sonst beschreibst du, was gemacht
   wurde, ohne das Verfahren zu benennen.

5. PRO SITZUNG NUR EINMAL: Jede Sitzung erscheint im "Verlauf"-Abschnitt
   GENAU EINMAL. Wenn eine Sitzung mehrere Themen oder Methoden hatte,
   nenne sie in DEMSELBEN Absatz, nicht in mehreren.

6. KEINE STRUKTURELLEN DOPPLUNGEN: Schreibe NICHT denselben Inhalt
   in zwei verschiedenen Abschnitten. Wenn du Methode X bei Sitzung 5 schon
   erwähnt hast, erwähne sie nicht nochmal in einem späteren Abschnitt.

7. UNSICHERHEIT KENNZEICHNEN: Wenn aus dem Text unklar bleibt was passiert ist,
   schreibe "im Protokoll unklar" oder "nicht weiter ausgeführt" — nicht raten.

8. SITZUNGS-BEZUG: Wo immer möglich, beziehe Aussagen auf das Sitzungs-Datum
   oder die Seitenzahl ("am 12.01.", "S. 4"). Das ist die Audit-Spur.
"""


VERLAUF_SUMMARY_STRUCTURE = """STRUKTUR DER ZUSAMMENFASSUNG:

Schreibe in DREI Abschnitten, jeweils mit Überschrift:

### Übersicht
Anzahl und Art der Sitzungen, Zeitraum, Dichte (z.B. "12 Einzelgespräche
und 8 Gruppensitzungen vom 01.01. bis 03.02., zusätzlich 4 nonverbale
Therapien"). 2-4 Sätze.

### Verlauf
Chronologische Darstellung der Sitzungen.
Pro Sitzung EIN Absatz mit Datum, Sitzungstyp, Themen UND Methoden zusammen.

Beispiel-Format:
"12.01. (Einzelgespräch): Patientin bearbeitet Selbstabwertung in
Familienkontext. Therapeut nutzt Stuhlarbeit und Externalisierung
des inneren Kritikers."

Fasse aufeinanderfolgende Sitzungen mit demselben Thema zu einem Block zusammen:
"15.01.–19.01. (3 Gruppensitzungen): Bearbeitung von Bindungsmustern in
Konfliktsituationen. Schwerpunkt Imaginationstechniken."

### Gesamtentwicklung
Was hat sich im Behandlungszeitraum verändert. 3-6 Sätze. Strikt nach
Protokoll. Stabilisierung, Destabilisierung, neue Symptome, Verschlechterung.
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
    target_words: Optional[int] = None,
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
        target_words:     Ziel-Wortzahl der Zusammenfassung. None = proportional
                          zum Input berechnet (12% des Inputs, mind. 800w).
                          v19.2.1: Eval-Daten zeigen dass das LLM konsistent
                          4-20% des Inputs als Synthese produziert. Fixe 4000w
                          waren unrealistisch hoch und fuehrten zu 95% Failure-Rate.

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
          "target_words":         int   — verwendetes Target (fuer Audit)
          "min_acceptable":       int   — verwendete Untergrenze (fuer Audit)
        }

    Raises:
        RuntimeError wenn auch der initiale Call keinen plausibel langen
        Output liefert (< 50% Zielwortzahl). Der Aufrufer (Pipeline) faengt
        das ab und faellt auf das Original zurueck.
    """
    # Lokal-Import um Zirkelimport zu vermeiden (llm.py kennt diese Datei nicht).
    from app.services.llm import generate_text

    if not verlauf_text or not verlauf_text.strip():
        raise RuntimeError("Stage 1: leerer Verlauf-Text")

    raw_words = len(verlauf_text.split())

    # v19.2.1: target_words proportional zum Input statt fix 4000.
    # Hintergrund: Eval-Daten (13.05.2026) zeigten dass das LLM bei 6789-12962w
    # Input konsistent 500-1500w produziert (mit dem einen Erfolg bei 2571w).
    # Fixe 4000w + min_acceptable=1600w fuehrten zu 95% Failure-Rate.
    if target_words is None:
        target_words = max(800, int(raw_words * 0.12))
        # Beispiel:
        #   raw=12962w → target=1555w
        #   raw= 6789w → target= 814w
        #   raw= 3000w → target= 800w (Floor)

    # v19.2.1: Threshold ebenfalls relativ - 50% statt 40%, Floor 400.
    min_acceptable = max(400, int(target_words * 0.5))
    max_acceptable = int(target_words * 2.5)  # mehr Headroom nach oben

    focus_hint = _build_focus_hint(workflow)
    # v19.2.2: Anti-Think-Anweisung direkt im System-Prompt anhängen.
    # Hintergrund (Eval-Lauf 14.05.2026): Stage-1 zeigte think_ratio=50-67%
    # bei 12962w-Inputs. Qwen3:32b ignoriert "think:False" + einmaliges
    # "/no_think" bei komplexen Verdichtungsaufgaben — defense in depth nötig.
    anti_think_system = (
        "\n\nWICHTIG: KEIN INNERES NACHDENKEN. "
        "Schreibe direkt die Zusammenfassung. "
        "KEINE <think>-Tags, KEINE Meta-Reflexion, KEINE Vorbemerkungen. "
        "Beginne sofort mit dem ersten Abschnitt '### Übersicht'."
    )
    system_prompt = (
        VERLAUF_SUMMARY_SYSTEM_PROMPT
        + "\n\n"
        + VERLAUF_SUMMARY_STRUCTURE
        + (f"\n\nFOCUS: {focus_hint}\n" if focus_hint else "")
        + anti_think_system
    )

    # v19.2.2: /no_think doppelt - Anfang UND Ende des user_content.
    # llm.py haengt am Ende sowieso /no_think an (idempotent durch rstrip),
    # aber am Anfang ist hier neu und wirkt staerker.
    user_content = (
        "/no_think\n\n"
        + (f"AKTUELLER PATIENT: {patient_initial}\n\n" if patient_initial else "")
        + "QUELLE — Verlaufsdokumentation:\n"
        + ">>>VERLAUFSDOKU<<<\n"
        + verlauf_text
        + "\n>>>/VERLAUFSDOKU<<<\n\n"
        + f"Verdichte diese Verlaufsdokumentation jetzt. "
        + f"Zielwortzahl: ca. {target_words} Wörter "
        + f"(akzeptiert: {min_acceptable}–{max_acceptable}).\n\n"
        + "/no_think"
    )

    # Erste Generierung — kein Workflow (kein BASE_PROMPT, kein Primer),
    # eigenes Token-Budget.
    # v19.2.1: skip_aggressive_dedup=True - thematische Wiederholungen in Stage-1
    # Synthesen sind strukturell erwuenscht ("Sitzung X kam in Themen-Block A
    # vor"), nicht echte Dopplungen. Eval-Befund: 8 von 10 Stage-1-Failures
    # wurden durch faelschliche Dedup-Aktion zu kurz (12-18 Absaetze entfernt).
    # v19.2.2: Temperatur 0.4 statt 0.2 — bricht deterministische Reasoning-Loops
    # bei Qwen3. Niedrige Temperatur (0.0-0.2) provoziert lange Think-Bloecke
    # weil das Modell deterministisch in den "vorsichtig denken"-Pfad geht.
    import time as _t
    t0 = _t.time()
    result = await generate_text(
        system_prompt=system_prompt,
        user_content=user_content,
        max_tokens=int(target_words * 1.5),  # Wort->Token-Puffer
        workflow=None,
        temperature_override=0.4,
        skip_aggressive_dedup=True,
    )

    summary = (result.get("text") or "").strip()
    summary_words = len(summary.split()) if summary else 0

    if not summary:
        raise RuntimeError("Stage 1: Zusammenfassung leer")
    if summary_words < min_acceptable:
        raise RuntimeError(
            f"Stage 1: Zusammenfassung implausibel kurz: {summary_words}w "
            f"< {min_acceptable}w Minimum (target={target_words}w, raw={raw_words}w)"
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
        "target_words":         target_words,
        "min_acceptable":       min_acceptable,
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
    Anti-Think-Schutz wie im Hauptcall (v19.2.2). Maximal EIN Retry, kein Loop.

    Returns:
        (retry_summary, retry_telemetry)
    """
    from app.services.llm import generate_text

    issue_summary = "; ".join(
        f"{i['type']}: {i['detail']}" for i in previous_issues
    )

    # Akzeptanz-Range konsistent zu summarize_verlauf
    min_acceptable = max(400, int(target_words * 0.5))
    max_acceptable = int(target_words * 2.5)

    focus_hint = _build_focus_hint(workflow)
    # v19.2.2: Anti-Think auch im retry-Pfad konsistent
    anti_think_system = (
        "\n\nWICHTIG: KEIN INNERES NACHDENKEN. "
        "Schreibe direkt die Zusammenfassung. "
        "KEINE <think>-Tags, KEINE Meta-Reflexion, KEINE Vorbemerkungen. "
        "Beginne sofort mit dem ersten Abschnitt '### Übersicht'."
    )
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
        + anti_think_system
    )

    # v19.2.2: /no_think doppelt - Anfang UND Ende
    user_content = (
        "/no_think\n\n"
        + (f"AKTUELLER PATIENT: {patient_initial}\n\n" if patient_initial else "")
        + "QUELLE — Verlaufsdokumentation:\n"
        + ">>>VERLAUFSDOKU<<<\n"
        + verlauf_text
        + "\n>>>/VERLAUFSDOKU<<<\n\n"
        + f"Verdichte diese Verlaufsdokumentation jetzt. "
        + f"Zielwortzahl: ca. {target_words} Wörter "
        + f"(akzeptiert: {min_acceptable}–{max_acceptable}).\n\n"
        + "/no_think"
    )

    try:
        result = await generate_text(
            system_prompt=system_prompt,
            user_content=user_content,
            max_tokens=int(target_words * 1.5),
            workflow=None,
            # v19.2.2: Temperatur 0.3 statt 0.1 - selbst beim Halluzinations-Retry
            # nicht zu deterministisch, sonst greift Anti-Think-Schutz nicht
            temperature_override=0.3,
            skip_aggressive_dedup=True,  # v19.2.1: konsistent zu summarize_verlauf
        )
    except Exception as e:
        logger.error("Stage 1 Retry-Call fehlgeschlagen: %s", e)
        return "", {}

    retry_summary = (result.get("text") or "").strip()
    return retry_summary, result.get("telemetry", {})
