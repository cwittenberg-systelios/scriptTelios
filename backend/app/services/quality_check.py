"""
Qualitätsprüfung für generierte Dokumente.

Refactor von tests/test_eval.py::EvalResult in produktive Form:
  - Severities (CRITICAL | WARNING | INFO) statt flacher Issue-Liste
  - Maschinenlesbare Issue-Codes für Frontend-Logik
  - Repair-Hints pro Issue für späteren Auto-Repair-Pass

Verwendung:
    from app.services.quality_check import run_quality_check
    result = run_quality_check(workflow="entlassbericht", text=output_text)
    if not result.is_passing:
        for issue in result.issues:
            print(f"[{issue.severity}] {issue.message}")

Tests in test_eval.py importieren ebenfalls aus diesem Modul, damit
Test- und Produktions-Logik nicht auseinanderlaufen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Literal

from app.services.quality_specs import WorkflowSpec, get_spec


# ── Konstanten (aus test_eval.py uebernommen) ────────────────────────────────

# IFS/systemische Fachbegriffe für Stil-Dichte-Messung
FACHBEGRIFFE: set[str] = {
    "anteile", "anteil", "anteilearbeit", "manager", "exile", "feuerwehr",
    "self-energy", "selbst-energie", "steuerungsposition", "schutzanteil",
    "schutzanteile", "inneres kind", "türsteher", "wächter", "wächterin",
    "hypnosystemisch", "systemisch", "ressource", "ressourcen",
    "ressourcenorientiert", "reframing", "externalisierung", "stuhlarbeit",
    "körperarbeit", "netzwerkarbeit", "biographiearbeit", "auftragsklärung",
    "dissoziation", "affektregulation", "bindungsmuster", "traumatisierung",
    "co-regulation", "selbstwirksamkeit", "selbstfürsorge", "selbstwert",
    "schwingungsfähigkeit", "vulnerabilität", "stabilisierung",
}

# Synonym-Mappings für tolerante Keyword-/Sektion-Matches
KEYWORD_SYNONYMS: dict[str, list[str]] = {
    "vorstellungsanlass": [
        "vorstellungsanlass", "stellt sich vor", "stellt sich mit",
        "hauptanliegen", "hauptbeschwerde", "kommt mit", "berichtet",
    ],
    "behandlungsverlauf": [
        "behandlungsverlauf", "im verlauf", "im einzelprozess",
        "therapeutische arbeit", "wir erlebten",
    ],
    "empfehlung": [
        "empfehlung", "empfohlen", "ambulant", "nachsorge",
        "weiterbehandlung", "weitere therapie",
    ],
}

SECTION_SYNONYMS: dict[str, list[str]] = {
    "Behandlungsverlauf": [
        "behandlungsverlauf", "verlauf", "im einzelprozess", "therapeutische arbeit",
        "im laufe der behandlung", "im verlauf der behandlung", "wir erlebten",
        "im stationaren rahmen", "im stationären rahmen",
    ],
    "Empfehlung": [
        "empfehlung", "empfohlen", "empfehlen", "ambulant", "nachsorge",
        "weiterbehandlung", "weitere therapie", "fortführung", "fortsetzen",
    ],
    "Vorstellungsanlass": [
        "vorstellungsanlass", "stellt sich vor", "stellt sich mit",
        "hauptanliegen", "hauptbeschwerde", "vorstellungsgrund",
        "kommt mit", "leidet unter", "berichtet über", "berichtet von",
    ],
    "Anamnese": [
        "anamnese", "berichtet", "biographisch", "vorgeschichte",
        "in der vergangenheit", "fruher", "früher",
    ],
    "Befund": [
        "befund", "psychischer befund", "psychopathologisch",
        "im gespräch", "im gespraech", "stimmungslage",
    ],
}


# ── Datenklassen ─────────────────────────────────────────────────────────────

Severity = Literal["critical", "warning", "info"]


@dataclass
class Issue:
    """Ein einzelnes Qualitätsproblem im Output."""
    severity: Severity
    code: str
    message: str
    repair_hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QualityCheckResult:
    """Ergebnis einer Qualitätsprüfung."""
    workflow: str
    word_count: int
    passed: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    style_metrics: dict | None = None

    @property
    def score(self) -> float:
        """Score 0.0-1.0 basierend auf bestandenen Checks vs. Issues."""
        total = len(self.passed) + len(self.issues)
        return len(self.passed) / total if total > 0 else 0.0

    @property
    def has_critical(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)

    @property
    def is_passing(self) -> bool:
        """
        Bestanden = keine kritischen Issues UND Score >= 0.7.
        Warnings allein lassen den Check noch passing — nur Critical
        oder ein massives Score-Defizit blockt.
        """
        return not self.has_critical and self.score >= 0.7

    def to_dict(self) -> dict:
        """JSON-serialisierbares Dict für Frontend/DB."""
        return {
            "workflow": self.workflow,
            "word_count": self.word_count,
            "score": round(self.score, 2),
            "is_passing": self.is_passing,
            "has_critical": self.has_critical,
            "passed_count": len(self.passed),
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
            "style_metrics": self.style_metrics,
        }


# ── Stil-Analyse (1:1 aus test_eval.py uebernommen) ─────────────────────────

class StyleAnalyzer:
    """Extrahiert messbare Stilmerkmale aus einem Text."""

    def __init__(self, text: str):
        self.text = text
        self.sentences = self._split_sentences(text)
        self.words = text.split()
        self.paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        parts = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÜ])', text)
        return [s.strip() for s in parts if s.strip() and len(s.split()) >= 3]

    @property
    def avg_sentence_length(self) -> float:
        if not self.sentences:
            return 0.0
        lengths = [len(s.split()) for s in self.sentences]
        return sum(lengths) / len(lengths)

    @property
    def avg_paragraph_length(self) -> float:
        if not self.paragraphs:
            return 0.0
        lengths = [len(p.split()) for p in self.paragraphs]
        return sum(lengths) / len(lengths)

    @property
    def fachbegriff_density(self) -> float:
        if not self.words:
            return 0.0
        lower_text = self.text.lower()
        hits = sum(1 for fb in FACHBEGRIFFE if fb in lower_text)
        return hits / (len(self.words) / 100) if self.words else 0.0

    @property
    def wir_perspektive_ratio(self) -> float:
        if not self.sentences:
            return 0.0
        wir_pattern = re.compile(r'\b(wir|uns|unser)\b', re.IGNORECASE)
        wir_count = sum(1 for s in self.sentences if wir_pattern.search(s))
        return wir_count / len(self.sentences)

    @property
    def direkte_zitate_count(self) -> int:
        return len(re.findall(r'[„"«].*?[""»]', self.text))

    def to_dict(self) -> dict:
        return {
            "word_count": len(self.words),
            "sentence_count": len(self.sentences),
            "paragraph_count": len(self.paragraphs),
            "avg_sentence_length": round(self.avg_sentence_length, 1),
            "avg_paragraph_length": round(self.avg_paragraph_length, 1),
            "fachbegriff_density": round(self.fachbegriff_density, 2),
            "wir_perspektive_ratio": round(self.wir_perspektive_ratio, 2),
            "direkte_zitate": self.direkte_zitate_count,
        }


# ── Einzel-Checks ────────────────────────────────────────────────────────────

def _check_no_think_blocks(text: str, result: QualityCheckResult) -> None:
    if "</think>" in text or "<think>" in text:
        result.issues.append(Issue(
            severity="critical",
            code="THINK_BLOCK",
            message="Think-Block (<think>...</think>) im Output gefunden",
            repair_hint="Entferne alle <think>...</think>-Blöcke aus dem Text. "
                        "Gib ausschließlich den finalen Bericht aus.",
        ))
    else:
        result.passed.append("Kein Think-Block im Output")


def _check_word_count(text: str, min_w: int, max_w: int,
                      result: QualityCheckResult) -> None:
    wc = result.word_count
    if wc < min_w:
        result.issues.append(Issue(
            severity="warning",
            code="WORD_COUNT_LOW",
            message=f"Zu kurz: {wc}w < {min_w}w Minimum",
            repair_hint=(
                f"Der Text hat nur {wc} Wörter. Erweitere ihn auf mindestens "
                f"{min_w} Wörter, indem du den Inhalt detaillierter ausführst — "
                f"OHNE neue Inhalte zu erfinden, die nicht in den Quellen stehen."
            ),
        ))
    elif wc > max_w:
        result.issues.append(Issue(
            severity="warning",
            code="WORD_COUNT_HIGH",
            message=f"Zu lang: {wc}w > {max_w}w Maximum",
            repair_hint=(
                f"Der Text hat {wc} Wörter, das Limit ist {max_w}. "
                f"Kürze auf das Wesentliche, behalte alle wichtigen Inhalte."
            ),
        ))
    else:
        result.passed.append(f"Wortanzahl OK: {wc}w ({min_w}-{max_w})")


def _check_required_keywords(text: str, keywords: list[str],
                             result: QualityCheckResult) -> None:
    text_lower = text.lower()
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in text_lower:
            result.passed.append(f"Keyword vorhanden: '{kw}'")
            continue
        synonyms = KEYWORD_SYNONYMS.get(kw_lower, [kw_lower])
        if any(s in text_lower for s in synonyms):
            result.passed.append(f"Keyword (semantisch) vorhanden: '{kw}'")
        else:
            result.issues.append(Issue(
                severity="warning",
                code="MISSING_KEYWORD",
                message=f"Keyword fehlt: '{kw}'",
                repair_hint=(
                    f"Der Begriff '{kw}' (oder ein Synonym davon) sollte im "
                    f"Text vorkommen. Füge einen Absatz oder eine Passage "
                    f"hinzu, die diesen Aspekt thematisiert."
                ),
            ))


def _check_forbidden_patterns(text: str, patterns: list[str],
                              result: QualityCheckResult) -> None:
    for pat in patterns:
        if pat in text:
            # Schweregrad: Markdown-Sterne sind Kosmetik (warning),
            # Platzhalter-Reste sind kritisch (datenschutzrelevant nicht direkt,
            # aber Formfehler den der Therapeut nicht im Bericht haben will).
            sev: Severity = "critical" if pat in (
                "die Klientin/der Klient",
                ">>>wörtlich<<<",
                "###BEFUND###",
            ) else "warning"
            result.issues.append(Issue(
                severity=sev,
                code="FORBIDDEN_PATTERN",
                message=f"Verbotenes Pattern gefunden: '{pat}'",
                repair_hint=(
                    f"Das Pattern '{pat}' darf nicht im Output stehen. "
                    f"Entferne es vollständig. "
                    f"{'Markdown-Formatierung wie ** oder ## ist nicht erlaubt — schreibe als reinen Fließtext.' if pat in ('**', '##', '---') else ''}"
                    f"{'Platzhalter wie diesen NIE im fertigen Bericht stehen lassen — Inhalt muss konkret formuliert sein.' if pat in ('die Klientin/der Klient', '>>>wörtlich<<<') else ''}"
                ).strip(),
            ))
        else:
            result.passed.append(f"Pattern nicht vorhanden: '{pat}'")


def _check_required_sections(text: str, sections: list[str],
                             result: QualityCheckResult) -> None:
    text_lower = text.lower()
    for section in sections:
        section_lower = section.lower()
        if section_lower in text_lower:
            result.passed.append(f"Sektion vorhanden: '{section}'")
            continue
        indicators = SECTION_SYNONYMS.get(section, [section_lower])
        if any(ind in text_lower for ind in indicators):
            result.passed.append(f"Sektion (semantisch) vorhanden: '{section}'")
        else:
            result.issues.append(Issue(
                severity="warning",
                code="MISSING_SECTION",
                message=f"Sektion fehlt: '{section}' (auch keine Synonyme gefunden)",
                repair_hint=(
                    f"Der Abschnitt '{section}' fehlt im Text. Ergänze einen "
                    f"entsprechenden Absatz, der diesen Aspekt behandelt."
                ),
            ))


def _check_forbidden_names(text: str, names: list[str],
                           result: QualityCheckResult) -> None:
    """Datenschutz-Check: bestimmte Klarnamen dürfen nicht im Output sein."""
    for name in names:
        if name in text:
            result.issues.append(Issue(
                severity="critical",
                code="FORBIDDEN_NAME",
                message=f"DATENSCHUTZ: Name '{name}' im Text gefunden",
                repair_hint=(
                    f"KRITISCH (Datenschutz): Der Name '{name}' darf nicht im "
                    f"Bericht erscheinen. Ersetze durch Initialen oder anonyme "
                    f"Bezeichnungen wie 'die Patientin', 'der Patient'."
                ),
            ))
        else:
            result.passed.append(f"Datenschutz OK: '{name}' nicht im Text")


# ── Haupt-Funktion ───────────────────────────────────────────────────────────

def run_quality_check(
    workflow: str,
    text: str,
    *,
    forbidden_names: list[str] | None = None,
    custom_spec: WorkflowSpec | None = None,
    include_style_metrics: bool = True,
) -> QualityCheckResult:
    """
    Führt alle relevanten Qualitäts-Checks für einen Workflow-Output aus.

    Args:
        workflow: z.B. "entlassbericht", "anamnese", "befund", ...
                  Bei unbekanntem Workflow wird ein leeres Spec verwendet
                  (nur Think-Block-Check und Stil-Metriken).
        text: Der zu prüfende generierte Text.
        forbidden_names: Optionale Liste von Klarnamen die NICHT im
                         Text vorkommen dürfen (z.B. aus Patientenstamm-
                         daten). Erzeugt CRITICAL-Issue wenn gefunden.
        custom_spec: Überschreibt die Default-Spec für den Workflow.
                     Hauptsächlich für Tests (fixture-spezifische Erwartungen).
        include_style_metrics: Wenn True, hängt StyleAnalyzer-Metriken
                               an result.style_metrics.

    Returns:
        QualityCheckResult mit passed-Liste, Issues und Score.
    """
    text = text or ""
    spec = custom_spec if custom_spec is not None else get_spec(workflow)

    result = QualityCheckResult(
        workflow=workflow,
        word_count=len(text.split()),
    )

    # Immer prüfen: Think-Blocks (workflow-unabhängig)
    _check_no_think_blocks(text, result)

    if "min_words" in spec:
        _check_word_count(
            text,
            spec["min_words"],
            spec.get("max_words", 99999),
            result,
        )

    if spec.get("required_keywords"):
        _check_required_keywords(text, spec["required_keywords"], result)

    if spec.get("forbidden_patterns"):
        _check_forbidden_patterns(text, spec["forbidden_patterns"], result)

    if spec.get("required_sections"):
        _check_required_sections(text, spec["required_sections"], result)

    if forbidden_names:
        _check_forbidden_names(text, forbidden_names, result)

    if include_style_metrics and text.strip():
        try:
            analyzer = StyleAnalyzer(text)
            result.style_metrics = analyzer.to_dict()
        except Exception:
            # Stil-Analyse ist nice-to-have, kein Hartfehler
            pass

    return result
