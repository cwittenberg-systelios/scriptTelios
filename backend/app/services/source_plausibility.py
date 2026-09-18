"""
source_plausibility.py — v19.25 Sprint Q: Plausibilitaet der hochgeladenen Quellen.

Hintergrund (prompts.log 13.-15.09.2026):
  - Job 0780690823: als "Verlaufsdokumentation" wurde ein Kammer-Beitrags-
    formular (LPK BW) hochgeladen; die "Entlassbericht (zu vervollstaendigen)"-
    Vorlage enthielt einen fertigen Verlauf als HTML mit Mojibake ('?' statt
    Umlaut). Kein QC hat angeschlagen, der Bericht wurde normal erzeugt.
  - c.saur (14./15.09.): Stilvorlage bestand nur aus Ueberschriften
    ("Orga: / Anliegen: / Intervention: / ...") - kein Textbeispiel, wird
    vom Stil-Mechanismus faktisch ignoriert.

Drei deterministische Heuristiken, alle OHNE LLM:
  Q1 verlauf_vocabulary_density  -> VERLAUF_UNPLAUSIBEL
     Therapie-Vokabular pro 1.000 Woerter. Kalibrierung auf 13 echten
     Verlaeufen (10.-15.09.): 29.8-47.6; Kammer-Formular: 2.6.
     Schwelle 12. Datums-/GOAe-Marker taugen NICHT (ein echter Verlauf,
     Job 3fb64ec4, hat keine).
  Q2 encoding_damage             -> SOURCE_ENCODING_DAMAGED
     Mojibake ('\\w?\\w' >= 1 % der Woerter oder >= 20 absolut) oder HTML-Tags.
     strip_html_tags() entfernt Tags vor dem Prompt.
  Q3 style_example_quality       -> STYLE_EXAMPLE_TOO_SHORT
     Beispiel < 60 Woerter oder nur Ueberschriften (jede Zeile <= 5 Woerter).

Ergebnis: Liste von Warn-Dicts {code, severity, source, message, detail};
quality_check._check_source_plausibility macht daraus QualityIssues,
generation_pipeline haengt sie als job.source_warnings an (SSE/Job-API,
damit das Frontend sie schon waehrend des Laufs zeigen kann).
"""
from __future__ import annotations

import html as _html
import re
from typing import Optional

# ── Q1: Therapie-Vokabular ────────────────────────────────────────────────────

_THERAPY_VOCAB_RE = re.compile(
    r"\b(sitzung|gruppe|einzel|patient|klient|therap|gespräch|gespraech|gefühl|gefuehl"
    r"|anteil|körper|koerper|belast|erleb|bericht|stimmung|angst|beziehung|übung|uebung"
    r"|imagination|hypno|trauer|scham|schuld|ressourc|symptom|konflikt|anspannung|entspann"
    r"|reflexion|arbeit an|selbstwert|bedürfnis|beduerfnis|muster|schutz|verletz)\w*",
    re.IGNORECASE,
)
VERLAUF_MIN_WORDS_FOR_CHECK = 300
VERLAUF_VOCAB_THRESHOLD = 12.0     # Treffer pro 1.000 Woerter


def verlauf_vocabulary_density(text: str) -> tuple[int, float]:
    """(Wortzahl, Therapie-Vokabular-Treffer pro 1.000 Woerter)."""
    words = len((text or "").split())
    if not words:
        return 0, 0.0
    hits = len(_THERAPY_VOCAB_RE.findall(text))
    return words, round(1000.0 * hits / words, 1)


# ── Q2: Encoding / HTML ───────────────────────────────────────────────────────

_MOJIBAKE_RE = re.compile(r"\w\?\w")
_HTML_TAG_RE = re.compile(r"</?(p|br|div|span|b|i|u|strong|em|ul|ol|li|h[1-6]|table|tr|td|th|font|a)\b[^>]*>", re.IGNORECASE)
_HTML_ANY_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(\s[^<>]*)?>")
MOJIBAKE_MIN_ABS = 20
MOJIBAKE_MIN_RATIO = 0.01


def encoding_damage(text: str) -> dict:
    """{'mojibake': n, 'mojibake_ratio': r, 'html_tags': n, 'damaged': bool}."""
    t = text or ""
    words = max(1, len(t.split()))
    moji = len(_MOJIBAKE_RE.findall(t))
    tags = len(_HTML_TAG_RE.findall(t))
    ratio = round(moji / words, 4)
    damaged = tags >= 3 or (moji >= MOJIBAKE_MIN_ABS and ratio >= MOJIBAKE_MIN_RATIO)
    return {"mojibake": moji, "mojibake_ratio": ratio, "html_tags": tags, "damaged": damaged}


def strip_html_tags(text: str) -> str:
    """Entfernt HTML-Tags und dekodiert Entities; <br>/<p>/<li> werden zu
    Zeilenumbruechen. Kein-op, wenn kein Tag enthalten ist."""
    if not text or not _HTML_ANY_TAG_RE.search(text):
        return text
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    t = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", t)
    t = _HTML_ANY_TAG_RE.sub("", t)
    t = _html.unescape(t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ── Q3: Stilvorlage ───────────────────────────────────────────────────────────

STYLE_MIN_WORDS = 60
STYLE_HEADING_MAX_WORDS = 5


def style_example_quality(text: str) -> dict:
    """{'words': n, 'heading_only': bool, 'too_short': bool}."""
    t = (text or "").strip()
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    words = len(t.split())
    heading_only = bool(lines) and all(len(ln.split()) <= STYLE_HEADING_MAX_WORDS for ln in lines)
    return {"words": words, "heading_only": heading_only,
            "too_short": words < STYLE_MIN_WORDS or heading_only}


# ── Sammler ───────────────────────────────────────────────────────────────────

CODE_VERLAUF_UNPLAUSIBEL = "VERLAUF_UNPLAUSIBEL"
CODE_SOURCE_ENCODING_DAMAGED = "SOURCE_ENCODING_DAMAGED"
CODE_STYLE_EXAMPLE_TOO_SHORT = "STYLE_EXAMPLE_TOO_SHORT"


def collect_source_warnings(
    *,
    verlauf_text: Optional[str] = None,
    sources: Optional[dict[str, Optional[str]]] = None,
    style_examples: Optional[list[str]] = None,
    style_source: Optional[str] = None,
) -> list[dict]:
    """Alle Quellen-Warnungen eines Jobs (leer = alles plausibel).

    sources:        {"Antragsvorlage": text, "Verlaufsdokumentation": text, ...}
                    fuer den Encoding-Check (Roh-Extrakte, VOR strip_html_tags).
    style_examples: Einzelbeispiele (split_style_examples); style_source
                    "text_input" | "file_upload" | "style_library".
    """
    out: list[dict] = []

    # Q1
    if verlauf_text and verlauf_text.strip():
        words, density = verlauf_vocabulary_density(verlauf_text)
        if words >= VERLAUF_MIN_WORDS_FOR_CHECK and density < VERLAUF_VOCAB_THRESHOLD:
            out.append({
                "code": CODE_VERLAUF_UNPLAUSIBEL,
                "severity": "warning",
                "source": "Verlaufsdokumentation",
                "message": (
                    "Die hochgeladene Verlaufsdokumentation sieht nicht nach einer "
                    "Verlaufsdokumentation aus (kaum therapeutisches Vokabular: "
                    f"{density}/1.000 Woerter, erwartet >= {int(VERLAUF_VOCAB_THRESHOLD)}). "
                    "Wurde ein falsches Dokument (Formular, Fremdunterlage) hochgeladen?"
                ),
                "detail": {"words": words, "density": density, "threshold": VERLAUF_VOCAB_THRESHOLD},
            })

    # Q2
    for name, text in (sources or {}).items():
        if not text or not text.strip():
            continue
        d = encoding_damage(text)
        if d["damaged"]:
            parts = []
            if d["html_tags"] >= 3:
                parts.append(f"{d['html_tags']} HTML-Tags")
            if d["mojibake"] >= MOJIBAKE_MIN_ABS and d["mojibake_ratio"] >= MOJIBAKE_MIN_RATIO:
                parts.append(f"{d['mojibake']} Woerter mit '?' statt Umlaut")
            out.append({
                "code": CODE_SOURCE_ENCODING_DAMAGED,
                "severity": "warning",
                "source": name,
                "message": (
                    f"Quelle '{name}' ist technisch beschaedigt ({', '.join(parts)}). "
                    "HTML wurde entfernt; fehlende Umlaute koennen sich im Bericht "
                    "fortsetzen - Quelle ggf. als sauberes PDF/DOCX neu exportieren."
                ),
                "detail": {k: v for k, v in d.items() if k != "damaged"},
            })

    # Q3
    for idx, ex in enumerate(style_examples or [], start=1):
        q = style_example_quality(ex)
        if q["too_short"]:
            where = {
                "style_library": "in der Stilbibliothek hinterlegtes Beispiel",
                "file_upload": "hochgeladene Stilvorlage",
                "text_input": "eingegebene Stilvorlage",
            }.get(style_source or "", "Stilvorlage")
            out.append({
                "code": CODE_STYLE_EXAMPLE_TOO_SHORT,
                "severity": "info",
                "source": "Stilvorlage",
                "message": (
                    f"Stilvorlage {idx} ({where}) enthaelt kein Textbeispiel "
                    + ("(nur Ueberschriften)" if q["heading_only"] else f"({q['words']} Woerter, mindestens {STYLE_MIN_WORDS})")
                    + ". Der Schreibstil kann daraus nicht uebernommen werden; eine eigene "
                    "Gliederung bitte ueber die Fokus-Themen/Hinweise angeben."
                ),
                "detail": {"index": idx, **q, "style_source": style_source},
            })
    return out
