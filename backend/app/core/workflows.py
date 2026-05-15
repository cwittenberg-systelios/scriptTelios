"""
Workflow-Definitionen — die EINZIGE Quelle der Wahrheit fuer alle
Workflow-bezogenen Konstanten.

Wenn du einen neuen Workflow ergaenzst, einen Wortlimit aenderst oder
ein Label umbenennst: NUR HIER, sonst nirgends.

Konsumenten (alle leiten ihre Daten aus diesem Modul ab):
  - app/models/schemas.py        → WorkflowLiteral (Pydantic Form-Validation)
  - app/models/db.py             → DOKUMENTTYPEN, DOKUMENTTYP_LABELS
                                   (pgvector-Filter + UI-Labels)
  - app/services/prompts.py      → STRUCTURAL_WORKFLOWS
                                   (BASE_PROMPTS und WORKFLOW_INSTRUCTIONS_DEFAULT
                                   verwenden weiterhin Workflow-Keys, werden
                                   per Sync-Test gegen WORKFLOW_KEYS gepruefft)
  - app/api/jobs.py              → word_limit_for(), max_tokens_for(),
                                   expected_tokens_for()
  - app/api/workflow_manifest.py → GET /api/workflows liefert das
                                   Manifest ans Frontend
  - scripts/eval_report.py       → WF_COL, WF_LBL aus den Specs aufgebaut

NICHT in WORKFLOWS aufnehmen:
  - "befund" - das ist ein interner Sub-Call von "anamnese", kein eigener
    Workflow im Sinne des Frontend-Pickers oder der DB-Filterung. Bleibt
    als String-Konstante in prompts.py.

Der Sync-Test test_suite.test_all_workflow_constants_synchronized()
schlaegt fehl, sobald irgendeine andere Stelle aus dem Sync gelaeuft.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class WorkflowSpec:
    """Vollstaendige Spezifikation eines Workflows.

    Frozen weil es eine Konstante ist - nirgends im Code soll dieser
    Wert zur Laufzeit geaendert werden.
    """
    # ── Identitaet ──────────────────────────────────────────────────
    key: str                          # "anamnese" - URL-Slug, DB-Key
    label: str                        # "Anamnese" - lange Form (UI, DB)
    short_label: str                  # "Anamnese" - max ~16 Zeichen, fuer Tabellen

    # ── Verhalten ───────────────────────────────────────────────────
    is_structural: bool               # True = Stilbeispiel als Schablone
                                      # (Gliederung/Laenge wird uebernommen)

    # ── Generierungs-Parameter ──────────────────────────────────────
    word_limit: tuple[int, int]       # min/max Woerter (Fallback ohne Stilvorlage)
    max_tokens: int                   # max LLM-Tokens fuer Generierung
    expected_tokens: int              # erwartete Output-Tokens (Progress-Schaetzung)

    # ── Darstellung ─────────────────────────────────────────────────
    color_hex: str                    # Hex-Farbe fuer Eval-Report-Charts


# ── DIE ZENTRALE LISTE ───────────────────────────────────────────────
# Reihenfolge entspricht dem Frontend-Dropdown (von "einfach" zu "komplex").
WORKFLOWS: tuple[WorkflowSpec, ...] = (
    WorkflowSpec(
        key="dokumentation",
        label="Gesprächsdokumentation",
        short_label="Gesprächsdoku",
        is_structural=False,
        word_limit=(150, 450),
        max_tokens=3500,           # v19.2.3: 2048 → 3500 (Headroom fuer 450W
                                   # word_limit + Thinking-Leak + sauberer
                                   # Satzabschluss; 2048 fuehrte regelmaessig
                                   # zu mid-word cap-hits bei P1)
        expected_tokens=1000,
        color_hex="#2c2c2c",
    ),
    WorkflowSpec(
        key="anamnese",
        label="Anamnese",
        short_label="Anamnese",
        is_structural=True,
        word_limit=(280, 650),
        max_tokens=4500,           # v19.1: 3000 → 4500 (anamnese teilt intern
                                   # in 2 LLM-Calls mit 0.6/0.5-Faktor → Anamnese
                                   # ~2700, Befund ~2250 Tokens Headroom)
        expected_tokens=1500,
        color_hex="#2d7a3a",
    ),
    WorkflowSpec(
        key="verlaengerung",
        label="Verlängerungsantrag",
        short_label="Verlängerung",
        is_structural=True,
        word_limit=(350, 650),
        max_tokens=4500,           # v19.1: 3000 → 4500 (Headroom fuer Think+Output)
        expected_tokens=1500,
        color_hex="#1a5c8b",
    ),
    WorkflowSpec(
        key="folgeverlaengerung",
        label="Folgeverlängerungsantrag",
        short_label="Folgeverlängerung",
        is_structural=True,
        word_limit=(350, 650),
        max_tokens=4500,           # v19.1: 3000 → 4500 (Headroom fuer Think+Output)
        expected_tokens=1500,
        color_hex="#c47d1a",
    ),
    WorkflowSpec(
        key="akutantrag",
        label="Akutantrag",
        short_label="Akutantrag",
        is_structural=True,
        word_limit=(150, 350),
        max_tokens=2048,
        expected_tokens=800,
        color_hex="#7b1fa2",
    ),
    WorkflowSpec(
        key="entlassbericht",
        label="Entlassbericht",
        short_label="Entlassbericht",
        is_structural=True,
        word_limit=(500, 900),
        max_tokens=5500,           # v19.1: 4000 → 5500 (Headroom fuer Think+Output;
                                   # Eval c9cf7204 traf Hard-Cap bei 4000 Tokens)
        expected_tokens=2000,
        color_hex="#8b1a1a",
    ),
)


# ── Abgeleitete Lookups (read-only) ──────────────────────────────────
WORKFLOW_KEYS: tuple[str, ...] = tuple(w.key for w in WORKFLOWS)
WORKFLOW_BY_KEY: dict[str, WorkflowSpec] = {w.key: w for w in WORKFLOWS}


# ── Pydantic/FastAPI Literal-Typ ─────────────────────────────────────
# WICHTIG: Literal[] braucht bei der Definition statische Argumente.
# Python 3.11+ erlaubt Unpacking via Literal[*WORKFLOW_KEYS]; aeltere
# Versionen brauchen die hardcoded Variante. Der Sync-Test stellt sicher,
# dass die hardcoded Variante mit WORKFLOW_KEYS uebereinstimmt - sonst
# CI-Fehler.
try:
    # Python 3.11+: Literal mit Unpacking
    WorkflowLiteral = Literal[*WORKFLOW_KEYS]  # type: ignore[valid-type]
except TypeError:
    # Fallback fuer aeltere Python-Versionen.
    # MUSS bei Aenderung von WORKFLOWS manuell synchronisiert werden -
    # test_all_workflow_constants_synchronized() schlaegt sonst fehl.
    WorkflowLiteral = Literal[
        "dokumentation",
        "anamnese",
        "verlaengerung",
        "folgeverlaengerung",
        "akutantrag",
        "entlassbericht",
    ]


# ── Convenience-Funktionen ───────────────────────────────────────────
# Bevorzugt vor direktem Zugriff auf WORKFLOW_BY_KEY[k] - die Funktionen
# liefern sinnvolle Defaults statt KeyError und machen den Aufrufer-Code
# leichter testbar.

def get(key: str) -> WorkflowSpec | None:
    """Gibt die WorkflowSpec zu einem Key zurueck, oder None."""
    return WORKFLOW_BY_KEY.get(key)


def label_for(key: str) -> str:
    """Lange Anzeigeform. Fallback: Key selbst."""
    spec = WORKFLOW_BY_KEY.get(key)
    return spec.label if spec else key


def short_label_for(key: str) -> str:
    """Kurze Anzeigeform fuer Tabellen. Fallback: Key selbst."""
    spec = WORKFLOW_BY_KEY.get(key)
    return spec.short_label if spec else key


def word_limit_for(
    key: str,
    fallback: tuple[int, int] = (200, 800),
) -> tuple[int, int]:
    """Default-Wortlimit (min, max). Fallback fuer unbekannte Keys."""
    spec = WORKFLOW_BY_KEY.get(key)
    return spec.word_limit if spec else fallback


def max_tokens_for(key: str, fallback: int = 2500) -> int:
    """Max LLM-Tokens fuer einen Workflow."""
    spec = WORKFLOW_BY_KEY.get(key)
    return spec.max_tokens if spec else fallback


def expected_tokens_for(key: str, fallback: int = 1500) -> int:
    """Erwartete Output-Tokens (fuer Progress-Schaetzung)."""
    spec = WORKFLOW_BY_KEY.get(key)
    return spec.expected_tokens if spec else fallback


def color_for(key: str, fallback: str = "#999999") -> str:
    """Hex-Farbe fuer Charts."""
    spec = WORKFLOW_BY_KEY.get(key)
    return spec.color_hex if spec else fallback


def is_structural(key: str) -> bool:
    """True wenn Stilbeispiel als Schablone dient."""
    spec = WORKFLOW_BY_KEY.get(key)
    return bool(spec and spec.is_structural)


def all_keys() -> list[str]:
    """Liste aller Workflow-Keys (kopierbar, mutierbar)."""
    return list(WORKFLOW_KEYS)


def to_manifest() -> list[dict]:
    """Serialisiert WORKFLOWS fuer den /api/workflows-Endpoint.
    JSON-fertig (keine dataclass, keine Tupel)."""
    return [
        {
            "key": w.key,
            "label": w.label,
            "short_label": w.short_label,
            "is_structural": w.is_structural,
            "word_limit": list(w.word_limit),  # JSON: kein Tupel
            "max_tokens": w.max_tokens,
            "expected_tokens": w.expected_tokens,
            "color_hex": w.color_hex,
        }
        for w in WORKFLOWS
    ]
