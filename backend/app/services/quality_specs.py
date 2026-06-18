"""
quality_specs.py
────────────────
Workflow-spezifische Spezifikationen fuer den QualityCheck.

Was hier liegt:
  - REQUIRED_KEYWORDS:  Pflicht-Keywords pro Workflow (mit Synonym-Sets)
  - REQUIRED_SECTIONS:  Pflicht-Sektionen pro Workflow (mit Synonym-Sets)
  - KEYWORD_SYNONYMS:   gemeinsamer Synonym-Pool

Quelle der Wahrheit: identisch zu den Checks im Eval-Framework
(tests/eval/test_eval.py::EvalResult.check_required_keywords /
check_required_sections). Wenn dort ein Check ergaenzt wird, muss er
auch hier aufgenommen werden - sonst weicht Production-QC vom Eval ab.

WICHTIG: KEINE LLM-Aufrufe. Reine Datendefinitionen + ein paar Helfer.
"""
from __future__ import annotations

from typing import Iterable


# ── Synonym-Pool (Ground Truth) ────────────────────────────────────────────────
#
# Konsistent mit EvalResult.check_required_keywords im Eval-Framework.
# Keys sind lowercase; Werte sind lowercase Indikatoren-Listen.
# Bei Aenderung: tests/eval/test_eval.py mit gleichziehen.
KEYWORD_SYNONYMS: dict[str, list[str]] = {
    "vorstellungsanlass": [
        "vorstellungsanlass", "stellt sich vor", "stellt sich mit",
        "hauptanliegen", "hauptbeschwerde", "kommt mit", "berichtet",
        "vorstellungsgrund", "leidet unter",
    ],
    "behandlungsverlauf": [
        "behandlungsverlauf", "im verlauf", "im einzelprozess",
        "therapeutische arbeit", "wir erlebten", "im stationaeren rahmen",
        "im stationären rahmen", "im laufe der behandlung",
        "im verlauf der behandlung",
    ],
    "empfehlung": [
        "empfehlung", "empfohlen", "empfehlen", "ambulant", "nachsorge",
        "weiterbehandlung", "weitere therapie", "fortführung", "fortsetzen",
    ],
    "anamnese": [
        "anamnese", "berichtet", "biographisch", "vorgeschichte",
        "in der vergangenheit", "fruher", "früher",
    ],
    "befund": [
        "befund", "psychischer befund", "psychopathologisch",
        "im gespräch", "im gespraech", "stimmungslage",
    ],
    "diagnose": [
        "diagnose", "icd-10", "icd 10", "f33", "f32", "f43", "f41",
    ],
}


# ── Pflicht-Keywords pro Workflow ──────────────────────────────────────────────
#
# Ein Workflow-Output muss MINDESTENS EINEN Indikator pro Keyword enthalten.
# Liste ist bewusst klein - das ist der harte Minimumkern. Stilistische
# Vorgaben werden NICHT als Issue gemeldet.
# v19.4: Keyword-Checks entschlackt. Jedes bisher geforderte Keyword
# (behandlungsverlauf, vorstellungsanlass, anamnese, empfehlung) ist
# gleichzeitig eine Pflicht-SEKTION (REQUIRED_SECTIONS) und damit durch die
# verbindliche Ueberschrift ohnehin erfuellt — der Keyword-Check feuerte nie
# und erzeugte nur Rauschen (-> unnoetige Repair-Prompts). Daher alle leer.
# Der Mechanismus bleibt: nicht-redundante INHALTS-Keywords (die NICHT durch
# eine Ueberschrift garantiert sind) koennen hier spaeter ergaenzt werden.
# Die strukturelle Absicherung leistet jetzt allein REQUIRED_SECTIONS.
REQUIRED_KEYWORDS: dict[str, list[str]] = {
    "dokumentation": [],
    "anamnese": [],
    "verlaengerung": [],
    "folgeverlaengerung": [],
    "akutantrag": [],
    "entlassbericht": [],
}


# ── Pflicht-Sektionen pro Workflow ─────────────────────────────────────────────
#
# Sektionen werden semantisch erkannt (Synonyme aus KEYWORD_SYNONYMS,
# Fallback exakter Match). Anders als Keywords sind Sektionen
# strukturell - ihr Fehlen ist ein staerkeres Signal fuer Repair-Bedarf.
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "dokumentation": [],
    "anamnese": ["Vorstellungsanlass", "Anamnese", "Befund"],
    "verlaengerung": ["Behandlungsverlauf"],
    "folgeverlaengerung": ["Behandlungsverlauf"],
    "akutantrag": [],
    "entlassbericht": ["Vorstellungsanlass", "Behandlungsverlauf", "Empfehlung"],
}


# ── Workflow-spezifische Trenner ───────────────────────────────────────────────
#
# Anamnese ist Two-Stage: Anamnese-Text + ###BEFUND###-Trenner + Befund-Text.
# Andere Workflows haben (aktuell) keine Trenner-Anforderungen.
BEFUND_SEPARATOR = "###BEFUND###"

WORKFLOWS_REQUIRING_BEFUND_SEPARATOR: frozenset[str] = frozenset({"anamnese"})


# ── Helfer ─────────────────────────────────────────────────────────────────────

def synonyms_for(term: str) -> list[str]:
    """Gibt Synonyme fuer einen Keyword/Section-Term zurueck.

    Fallback: nur der lowercase-Term selbst, sodass die exakte Suche
    weiterhin greift.
    """
    return KEYWORD_SYNONYMS.get(term.lower(), [term.lower()])


def upper_code_suffix(term: str) -> str:
    """Wandelt einen Term in einen sicheren Code-Suffix (^[A-Z_]+$).

    Beispiele:
      "Behandlungsverlauf"  -> "BEHANDLUNGSVERLAUF"
      "Empfehlung"          -> "EMPFEHLUNG"
      "Bio-grafie"          -> "BIO_GRAFIE"
      "Über-Sicht (Test)"   -> "UEBER_SICHT_TEST"

    Ergebnis matched garantiert ^[A-Z_]+$ - sonst leerer String.
    """
    if not term:
        return ""
    s = term.strip().lower()
    # Umlaute / sz transliterieren
    repl = {
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    # Alles ausser a-z0-9 wird zu "_"
    out_chars = []
    for ch in s:
        if "a" <= ch <= "z":
            out_chars.append(ch.upper())
        else:
            out_chars.append("_")
    raw = "".join(out_chars)
    # Mehrfache "_" zusammenfassen, Rand-Underscores entfernen
    while "__" in raw:
        raw = raw.replace("__", "_")
    raw = raw.strip("_")
    # Ziffern raus (Code matched ^[A-Z_]+$ - nur Buchstaben + Underscore)
    raw = "".join(c for c in raw if "A" <= c <= "Z" or c == "_")
    return raw


def required_keywords_for(workflow: str) -> list[str]:
    """Pflicht-Keywords fuer einen Workflow. Leer falls keine."""
    return list(REQUIRED_KEYWORDS.get(workflow, []))


def required_sections_for(workflow: str) -> list[str]:
    """Pflicht-Sektionen fuer einen Workflow. Leer falls keine."""
    return list(REQUIRED_SECTIONS.get(workflow, []))


def requires_befund_separator(workflow: str) -> bool:
    return workflow in WORKFLOWS_REQUIRING_BEFUND_SEPARATOR


def _any_indicator_present(text_lower: str, indicators: Iterable[str]) -> bool:
    """True wenn mindestens ein Indikator im (bereits lowercase) Text vorkommt."""
    return any(ind in text_lower for ind in indicators)


def keyword_present(text: str, term: str) -> bool:
    """True wenn term ODER ein Synonym im Text vorkommt (case-insensitive)."""
    text_lower = text.lower()
    if term.lower() in text_lower:
        return True
    return _any_indicator_present(text_lower, synonyms_for(term))


def section_present(text: str, section: str) -> bool:
    """True wenn section ODER ein Synonym im Text vorkommt (case-insensitive)."""
    text_lower = text.lower()
    if section.lower() in text_lower:
        return True
    return _any_indicator_present(text_lower, synonyms_for(section))
