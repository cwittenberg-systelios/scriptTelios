"""
Qualitäts-Spezifikationen pro Workflow.

Single Source of Truth für die produktiven Qualitätskriterien.
Wird von quality_check.run_quality_check() konsumiert.

Tests (test_eval.py) referenzieren dieselben Specs und ergänzen pro
Testfall um fixture-spezifische Felder (forbidden_names, must_not_hallucinate).
"""
from __future__ import annotations

from typing import TypedDict


class WorkflowSpec(TypedDict, total=False):
    """Erwartungen an den Output eines Workflows."""
    min_words: int
    max_words: int
    required_keywords: list[str]
    forbidden_patterns: list[str]
    required_sections: list[str]


# Workflow-Specs.
#
# Diese Werte sind absichtlich konservativer (toleranter) als die
# fixture-spezifischen Werte in test_eval.py / fixtures.json:
# - In Tests prüft man gegen *bekannte Eingaben* mit engen Erwartungen
# - In Produktion kennt man die Eingabe nicht im Detail; eine zu strenge
#   Wortzahl-Schranke führt zu False-Positives bei legitim kurzen Inputs
#
# Aktualisiert: WORKFLOW_SPECS spiegelt die Mindestanforderungen jedes
# Workflows wider — was darunter geht ist mit hoher Wahrscheinlichkeit
# ein echtes Qualitätsproblem.
WORKFLOW_SPECS: dict[str, WorkflowSpec] = {
    "dokumentation": {
        "min_words": 150,
        "max_words": 600,
        "required_sections": [
            "Auftragsklärung",
            "Relevante Gesprächsinhalte",
            "Hypothesen",
            "Einladungen",
        ],
        "forbidden_patterns": [
            "**", "##", "---",
            "SYSTEMISCHE EINSCHÄTZUNG",
            ">>>wörtlich<<<",
            "die Klientin/der Klient",
        ],
    },
    "anamnese": {
        "min_words": 300,
        "max_words": 700,
        "required_keywords": ["Vorstellungsanlass", "Ressourcen"],
        "forbidden_patterns": [
            "**", "##", "---",
            "SYSTEMISCHE EINSCHÄTZUNG",
            "Diagnosen:",
            "###BEFUND###",
            ">>>wörtlich<<<",
            "die Klientin/der Klient",
        ],
    },
    "befund": {
        # Befund hat ein festes Format (AMDP-Vorlage), keine Wir-Perspektive,
        # andere Erwartungen als der Anamnese-Fließtext.
        "min_words": 100,
        "max_words": 500,
        "forbidden_patterns": [
            "**", "##", "---",
            "###BEFUND###",
            "die Klientin/der Klient",
        ],
    },
    "verlaengerung": {
        "min_words": 300,
        "max_words": 700,
        "required_keywords": ["Verlängerung"],
        "required_sections": ["Verlauf", "Verlängerung"],
        "forbidden_patterns": [
            "**", "##", "---",
            "Diagnose:", "Diagnosen:",
            "Stammdaten",
            "Anamnese:",
            ">>>wörtlich<<<",
            "die Klientin/der Klient",
        ],
    },
    "folgeverlaengerung": {
        "min_words": 300,
        "max_words": 700,
        "required_keywords": ["Verlängerung"],
        "required_sections": ["Verlauf", "Verlängerung"],
        "forbidden_patterns": [
            "**", "##", "---",
            "Diagnose:", "Diagnosen:",
            "Stammdaten",
            "Anamnese:",
            ">>>wörtlich<<<",
            "die Klientin/der Klient",
        ],
    },
    "akutantrag": {
        "min_words": 150,
        "max_words": 450,
        "required_keywords": ["stationär", "akut"],
        "required_sections": ["Krankheitssymptomatik"],
        "forbidden_patterns": [
            "**", "##", "---",
            "Aktuelle Anamnese:",
            "Psychischer Befund:",
            ">>>wörtlich<<<",
            "die Klientin/der Klient",
        ],
    },
    "entlassbericht": {
        "min_words": 500,
        "max_words": 1000,
        "required_keywords": ["Behandlungsverlauf", "Empfehlung"],
        "required_sections": ["Behandlungsverlauf", "Empfehlung"],
        "forbidden_patterns": [
            "**", "##", "---",
            "Epikrise:", "Procedere:",
            ">>>wörtlich<<<",
            "die Klientin/der Klient",
        ],
    },
}


def get_spec(workflow: str) -> WorkflowSpec:
    """Gibt die Spec für einen Workflow zurück. Leeres Dict wenn unbekannt."""
    return WORKFLOW_SPECS.get(workflow, {})
