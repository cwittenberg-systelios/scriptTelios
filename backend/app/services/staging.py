"""
Stage-1-Pipeline-Steuerung (v19.2 / v19.3).

Entscheidet pro Job ob die Verlauf- bzw. Transkript-Verdichtung laeuft.
Die eigentliche Verdichtung steht in verlauf_summary.py / transcript_summary.py;
diese Datei kapselt nur das WANN.

Vor dem Auszug lebte die Logik inline in jobs.py:_run() — testbar nur durch
Reproduktion in test_jobs_logic.py. Mit dem Auszug koennen alle Bedingungen
isoliert getestet werden.
"""
from __future__ import annotations

from typing import Iterable, Optional


# Workflows mit grossem Verlaufs-Input, die Stage 1 brauchen koennen.
# Anamnese/Akutantrag haben typischerweise keinen langen Verlauf -> nicht hier.
STAGE1_VERLAUF_WORKFLOWS: frozenset[str] = frozenset({
    "verlaengerung",
    "folgeverlaengerung",
    "entlassbericht",
})

# Untergrenze: kuerzere Verlaeufe passen ohne Verdichtung in Stage 2.
STAGE1_VERLAUF_MIN_WORDS = 1500

# Workflows die in Produktion ein Transkript bekommen.
STAGE1_TRANSCRIPT_WORKFLOWS: frozenset[str] = frozenset({
    "dokumentation",
    "anamnese",
})

# Schwelle ab der Transkript-Verdichtung lohnt.
# Liegt bewusst UNTER der _sample_uniformly-Schwelle in llm.py (~5000-6000w
# nach Stilvorlage+System), damit die Verdichtung VORHER greift.
STAGE1_TRANSCRIPT_MIN_WORDS = 3500


def _word_count(text: Optional[str]) -> int:
    """Robust gegen None/Whitespace-only."""
    if not text:
        return 0
    return len(text.split())


def should_run_verlauf_stage1(
    workflow: str,
    verlauf_text: Optional[str],
    *,
    flag_enabled: bool = True,
    min_words: int = STAGE1_VERLAUF_MIN_WORDS,
    workflows: Iterable[str] = STAGE1_VERLAUF_WORKFLOWS,
) -> bool:
    """
    True wenn Stage 1 fuer den Verlauf laufen soll.

    Drei Bedingungen muessen gelten:
      1. Feature-Flag (settings.STAGE1_ENABLED, default True)
      2. Workflow in der Whitelist
      3. Verlauf hat >= min_words Woerter
    """
    if not flag_enabled:
        return False
    if workflow not in workflows:
        return False
    return _word_count(verlauf_text) >= min_words


def verlauf_stage1_skip_reason(
    workflow: str,
    verlauf_text: Optional[str],
    *,
    flag_enabled: bool = True,
    min_words: int = STAGE1_VERLAUF_MIN_WORDS,
    workflows: Iterable[str] = STAGE1_VERLAUF_WORKFLOWS,
) -> Optional[str]:
    """
    Gibt die Begruendung zurueck, warum Stage 1 NICHT laeuft, oder None.

    Wird fuer den Audit-Eintrag in jobs.py verwendet, damit man im
    Performance-Log nachvollziehen kann warum Stage 1 ausblieb. Nur fuer
    Workflows in der Stage-1-Whitelist sinnvoll — andere haben gar keinen
    Audit-Eintrag.
    """
    if workflow not in workflows:
        return None  # gar nicht relevant
    if not flag_enabled:
        return "stage1_disabled"
    wc = _word_count(verlauf_text)
    if wc < min_words:
        return f"verlauf_kurz_{wc}w"
    return None  # laeuft


def should_run_transcript_stage1(
    workflow: str,
    transcript_text: Optional[str],
    *,
    flag_enabled: bool = True,
    min_words: int = STAGE1_TRANSCRIPT_MIN_WORDS,
    workflows: Iterable[str] = STAGE1_TRANSCRIPT_WORKFLOWS,
) -> bool:
    """
    True wenn Stage 1 fuer das Transkript laufen soll.
    Analog zu should_run_verlauf_stage1.
    """
    if not flag_enabled:
        return False
    if workflow not in workflows:
        return False
    return _word_count(transcript_text) >= min_words


def transcript_stage1_skip_reason(
    workflow: str,
    transcript_text: Optional[str],
    *,
    flag_enabled: bool = True,
    min_words: int = STAGE1_TRANSCRIPT_MIN_WORDS,
    workflows: Iterable[str] = STAGE1_TRANSCRIPT_WORKFLOWS,
) -> Optional[str]:
    """Analog zu verlauf_stage1_skip_reason."""
    if workflow not in workflows:
        return None
    if not flag_enabled:
        return "transcript_stage1_disabled"
    wc = _word_count(transcript_text)
    if wc < min_words:
        return f"transkript_kurz_{wc}w_min_{min_words}w"
    return None


# ── Ziel-/Min-Wortzahl-Berechnung ─────────────────────────────────────────────
# Aus verlauf_summary.py / transcript_summary.py extrahiert, damit reine Funktion.

def compute_verlauf_target_words(
    raw_words: int,
    *,
    floor: int = 800,
    ratio: float = 0.12,
) -> int:
    """
    Berechnet die Ziel-Wortzahl fuer die Verlaufs-Verdichtung.

    Proportional zum Input, mit Floor. Beispiele:
      raw=12962w  -> target=1555w
      raw= 6789w  -> target=814w
      raw= 3000w  -> target=800w (Floor)

    Hintergrund (v19.2.1): Fixe 4000w fuehrten zu 95% Failure-Rate, weil
    Qwen3 bei 6k-13k-Inputs konsistent 500-1500w produziert.
    """
    return max(floor, int(raw_words * ratio))


def compute_verlauf_min_acceptable(
    target_words: int,
    *,
    floor: int = 400,
    ratio: float = 0.30,
) -> int:
    """
    Untergrenze fuer plausible Output-Laenge der Verlaufs-Verdichtung.

    v19.3.2: Threshold 50% -> 30% gelockert (Eval-Run 15.05.2026 zeigte
    bei grossen Verlaeufen Outputs 543-743w, alle < 50% von target=1555w).
    """
    return max(floor, int(target_words * ratio))


def compute_transcript_target_words(
    raw_words: int,
    *,
    floor: int = 600,
    cap: int = 1500,
    ratio: float = 0.20,
) -> int:
    """
    Ziel-Wortzahl fuer die Transkript-Verdichtung.

    Proportional, mit Floor und Hard-Cap. Beispiele:
      raw= 5000w -> target=1000w
      raw= 7000w -> target=1400w
      raw=10000w -> target=1500w  (Cap)
    """
    return min(cap, max(floor, int(raw_words * ratio)))


def compute_transcript_min_acceptable(
    target_words: int,
    *,
    floor: int = 300,
    ratio: float = 0.40,
) -> int:
    """
    Untergrenze fuer plausible Output-Laenge der Transkript-Verdichtung.

    40% niedriger als Verlauf (50% urspruenglich), weil Transkripte
    unstrukturiert sind und der Kompressionsgrad pro Sitzung stark variiert.
    """
    return max(floor, int(target_words * ratio))
