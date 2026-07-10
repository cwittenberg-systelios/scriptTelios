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

import re
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
    # ── dokumentation-Sektionen (2026-07-01, siehe REQUIRED_SECTIONS) ────────
    # Umlaut-Varianten explizit, da Outputs mal 'ä' mal 'ae' schreiben.
    "auftragsklärung": [
        "auftragsklärung", "auftragsklaerung",
        "im mittelpunkt stand", "kam mit dem anliegen", "anliegen des",
        "worum es", "gemeinsame ziel",
    ],
    "relevante gesprächsinhalte": [
        "relevante gesprächsinhalte", "relevante gespraechsinhalte",
        "gesprächsinhalte", "gespraechsinhalte", "inhaltlich fokussierte",
        "zentrale themen", "zentraler themenkomplex",
    ],
    "hypothesen und entwicklungsperspektiven": [
        "hypothesen und entwicklungsperspektiven",
        "hypothesen", "entwicklungsperspektiv",
    ],
    "einladungen": [
        "einladungen", "einladung", "wurde eingeladen",
        "keine konkrete einladung",
    ],
    # ── entlassbericht-Einstieg (2026-07-10, siehe REQUIRED_SECTIONS) ─────────
    # Ressourcen-/auftragsorientierter Einstieg des Verlaufs: mit welchem
    # Veraenderungswunsch kam der Klient (Auftragsklaerung), im Gegensatz zur
    # Defizitorientierung der Anamnese ('Vorstellungsanlass' war hier fehl am
    # Platz und feuerte staendig falsch). Zwei Marker-Familien:
    #   (a) Anliegen / Auftrag / Veraenderungswunsch / Ziel-Vokabular
    #   (b) verlaufstypische Einstiegsmarker ("zu Beginn der Begleitung",
    #       "bei Aufnahme" ...). Bare "zu beginn"/"beginn" bewusst NICHT
    #       aufgenommen (zu haeufig -> Check wuerde faktisch immer bestehen);
    #       nur qualifizierte Formen.
    "anliegen und behandlungsziele": [
        # (a) Auftrag / Anliegen / Veraenderungswunsch / Ziel
        "anliegen", "auftrag", "veränderungswunsch", "veraenderungswunsch",
        "wunsch nach veränderung", "wunsch nach veraenderung",
        "behandlungsziel", "zielsetzung", "mit dem ziel",
        "ziel des aufenthalts", "ziele des aufenthaltes", "ziel des aufenthaltes",
        "ziel der behandlung", "ziele der behandlung",
        "kam mit dem wunsch", "kam mit dem anliegen",
        "wünschte sich", "wuenschte sich",
        # (b) verlaufstypische Einstiegsmarker
        "zu beginn der begleitung", "zu beginn der behandlung",
        "zu beginn des aufenthalts", "zu beginn des aufenthaltes",
        "zu beginn des stationären aufenthalt",
        "zu behandlungsbeginn", "zu therapiebeginn",
        "bei aufnahme", "eingangs", "anfangs",
        # (c) Ankommen / Ersteindruck (v19.6.1) - der Einstieg beschreibt oft
        # auch das Ankommen und den Ersteindruck ("Wir erlebten sie zu
        # Therapiebeginn ..."). Ein Marker daraus genuegt.
        "ankommen", "angekommen", "ersteindruck",
        "wir erlebten sie", "wir erlebten ihn", "erlebten wir",
    ],
    # ── Gesamtbewertung (v19.6.1) - zusammenfassende Gesamtwuerdigung am Ende
    # des Verlaufs (Symptomentwicklung, Prae-Post, Gesamtbild). Distinktive
    # Marker (KEIN bare 'insgesamt'/'abschliessend' -> zu haeufig, waere immer
    # erfuellt und damit zahnlos).
    "gesamtbewertung": [
        "gesamtbewertung", "gesamtverlauf", "im gesamtverlauf", "gesamtbild",
        "im gesamtbild", "gesamtprozess", "im gesamtprozess", "gesamtwürdigung",
        "zusammenfassend", "zusammenfassende", "prä-post", "prä- post",
        "praepost", "prä-/post", "symptomreduktion", "über die begleitung",
        "über die gesamte begleitung",
    ],
    # ── empfohlene Therapie-Modalitaeten (v19.6.1, siehe RECOMMENDED_SECTIONS) ─
    # NICHT pflicht - nur erwartet, WENN die Modalitaet stattgefunden hat. Werden
    # auf info-Ebene geprueft (kein Fehlalarm, wenn z.B. keine Kunsttherapie lief).
    "einzeltherapie": [
        "einzeltherapie", "einzelprozess", "im einzelprozess", "einzelsetting",
        "einzelgespräch", "einzelsitzung", "im einzelkontakt",
    ],
    "gruppentherapie": [
        "gruppentherapie", "therapeutischen gruppen", "therapeutische gruppe",
        "in der gruppe", "in den gruppen", "gruppenprozess", "gruppensetting",
    ],
    "nonverbale therapie": [
        "nonverbale", "nonverbaler", "kunsttherapie", "musiktherapie",
        "körperpsychotherapie", "koerperpsychotherapie", "körpertherapie",
        "körperarbeit", "koerperarbeit", "bewegungstherapie", "tanztherapie",
        "gestaltungstherapie", "kreativtherapie", "maltherapie",
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
    # 2026-07-01: vorher leer - ausgerechnet der Workflow, dessen
    # 'Einladungen'-Sektion durch den Hard-Cap-Bug verloren ging (Issue 1),
    # hatte keinen Sektions-Check. Die vier Sektionen sind die kanonische
    # Gliederung aus WORKFLOW_INSTRUCTIONS_DEFAULT["dokumentation"]
    # (prompts.py). Deterministischer Post-Check; greift NICHT in die
    # Generierung ein.
    "dokumentation": [
        "Auftragsklärung",
        "Relevante Gesprächsinhalte",
        "Hypothesen und Entwicklungsperspektiven",
        "Einladungen",
    ],
    "anamnese": ["Vorstellungsanlass", "Anamnese", "Befund"],
    "verlaengerung": ["Behandlungsverlauf"],
    "folgeverlaengerung": ["Behandlungsverlauf"],
    "akutantrag": [],
    "entlassbericht": [
        "Anliegen und Behandlungsziele", "Behandlungsverlauf",
        "Gesamtbewertung", "Empfehlung",
    ],
}


# ── Empfohlene (optionale) Sektionen pro Workflow (v19.6.1) ────────────────────
#
# Anders als REQUIRED_SECTIONS: diese Sektionen sind ERWARTET, aber optional -
# sie erscheinen, WENN die Modalitaet stattgefunden hat. Ihr Fehlen ist KEIN
# Mangel (der Patient hat die Modalitaet evtl. nicht genutzt), sondern nur ein
# info-Hinweis ("keine Gruppentherapie erwaehnt - beabsichtigt?"). Beim
# entlassbericht sind das die im Fewshot vorgesehenen Therapie-Bausteine, die je
# nach Behandlung variieren. So bekommt der Therapeut eine Abdeckungs-Uebersicht,
# ohne dass gueltige Berichte (ohne diese Modalitaet) faelschlich failen.
RECOMMENDED_SECTIONS: dict[str, list[str]] = {
    "entlassbericht": ["Einzeltherapie", "Gruppentherapie", "Nonverbale Therapie"],
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


def recommended_sections_for(workflow: str) -> list[str]:
    """Empfohlene (optionale) Sektionen fuer einen Workflow. Leer falls keine.
    Fehlen ist KEIN Mangel - nur info-Hinweis (siehe RECOMMENDED_SECTIONS)."""
    return list(RECOMMENDED_SECTIONS.get(workflow, []))


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


# ── Stichpunkte / Fokus-Themen (v19.6, Punkt 6) ────────────────────────────────
#
# Dynamische PRO-JOB-Keywords: die vom Therapeuten mitgegebenen Stichpunkte (P1)
# bzw. Fokus-Themen (P3/P4, Feld 'bullets') sollen im Output vorkommen. Ersetzt
# die statischen (leeren) REQUIRED_KEYWORDS durch etwas Sinnhaftes/Individuelles.
# Bewusst GENEROESE Erkennung (Bias gegen Rausch-Repairs): ein Stichpunkt gilt
# als erfuellt, wenn EIN distinktiver Begriff daraus im Text auftaucht. Generische
# Fuellwoerter ('Arbeit', 'Thema' ...) zaehlen NICHT als Anker - sonst wuerde
# 'Traumafokussierte Arbeit' schon durch das ueberall vorkommende 'Arbeit' als
# erfuellt gelten.

_STICHWORT_STOP: frozenset[str] = frozenset({
    "und", "oder", "der", "die", "das", "des", "dem", "den", "ein", "eine",
    "einer", "eines", "einem", "einen", "mit", "von", "vom", "zum", "zur",
    "im", "in", "am", "auf", "für", "fuer", "bei", "als", "auch", "sowie",
    "sich", "seine", "seiner", "ihre", "ihrer", "insb", "bzgl", "the",
})

# Generisches Klinik-Fuellwort - kommt in nahezu jedem Bericht vor, taugt daher
# NICHT als Anker fuer die Anwesenheit eines spezifischen Stichpunkts.
_STICHWORT_FILLER: frozenset[str] = frozenset({
    "arbeit", "thema", "themen", "bereich", "aspekt", "aspekte", "umgang",
    "sitzung", "sitzungen", "gespräch", "gespräche", "gespraech", "prozess",
    "punkt", "punkte", "frage", "fragen", "ziel", "ziele",
})


def stichpunkt_terms(bullet: str) -> list[str]:
    """Distinktive Anker-Begriffe eines Stichpunkts (lowercase, ohne Stop-/
    Fuellwoerter, Mindestlaenge 5). Fallback: laengstes Token, falls nach dem
    Filtern nichts uebrig bleibt (reiner Fuellwort-Stichpunkt)."""
    raw = re.findall(r"[a-zäöüß]+", (bullet or "").lower())
    terms = [
        t for t in raw
        if len(t) >= 5 and t not in _STICHWORT_STOP and t not in _STICHWORT_FILLER
    ]
    if terms:
        return terms
    return [max(raw, key=len)] if raw else []


def stichpunkt_present(text: str, bullet: str) -> bool:
    """True, wenn mindestens ein distinktiver Begriff des Stichpunkts (Wortstamm
    an Wortgrenze) im Text vorkommt. Leerer Stichpunkt -> True (nichts zu
    pruefen)."""
    terms = stichpunkt_terms(bullet)
    if not terms:
        return True
    text_lo = text.lower()
    # Wortstamm ab Wortgrenze; Suffix frei fuer Flexion (traumafokussiert ->
    # traumafokussierte). Praefix auf max. 8 Zeichen begrenzt, damit lange
    # Komposita ueber Flexions-/Fugengrenzen matchen.
    for t in terms:
        if re.search(r"\b" + re.escape(t[:8]), text_lo):
            return True
    return False


def split_stichpunkte(raw: str | None) -> list[str]:
    """Zerlegt das freitextliche 'bullets'-Feld in einzelne Punkte. Trennt an
    Zeilenumbruechen, ';' und fuehrenden Aufzaehlungszeichen/Nummerierungen.
    Kommas werden NICHT getrennt (Themen enthalten oft 'Familien- und
    Paardynamik, insb. ...'). Leerpunkte raus."""
    if not raw or not raw.strip():
        return []
    parts: list[str] = []
    for line in raw.replace(";", "\n").splitlines():
        s = line.strip().lstrip("•-*–—").strip()
        s = re.sub(r"^\d+[.)]\s*", "", s).strip()
        if s:
            parts.append(s)
    return parts
