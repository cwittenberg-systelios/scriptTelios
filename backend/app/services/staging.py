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

import re
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
# DRY-Fix 2026-07-01: config.TRANSCRIPT_STAGE1_MIN_WORDS ist die EINZIGE
# Quelle (env-overridebar); vorher wurde der Wert hier UND in config manuell
# "deckungsgleich" gehalten (Drift-Falle). Der Re-Export behaelt den
# etablierten Namen fuer Funktions-Defaults, Tests und jobs.py bei.
# Liegt unter der _sample_uniformly-Schwelle in llm.py, damit die
# Verdichtung VORHER greift.
from app.core.config import settings as _settings

STAGE1_TRANSCRIPT_MIN_WORDS = _settings.TRANSCRIPT_STAGE1_MIN_WORDS


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


# ── v19.4: Kombiniertes Input-Budget ──────────────────────────────────────────
# Reine Planungs-Helfer fuer den Input-Budget-Guard in jobs.py. KEINE LLM-Calls.
#
# Hintergrund: Bis v19.3 wurde jede Quelle isoliert gegen ihre eigene Rohgroesse
# verdichtet (transcript_summary / verlauf_summary). Die SUMME aller Quellen
# wurde nie geprueft. Bei Anamnese (Transkript + Selbstauskunft + Vorbefunde)
# oder Folgeverlaengerung (langer Verlauf + Vorantrag) sprengte der kombinierte
# Input MAX_SAFE_CTX, worauf _sample_uniformly in llm.py das gesamte
# User-Content verlustbehaftet zerhackte UND das Output-Budget kollabierte
# (-> "kein Output" / "abgeschnitten").
#
# Diese Helfer berechnen ein Wort-Budget fuer den Input (Output zuerst
# reserviert) und planen pro Quelle ein budget-bewusstes Verdichtungsziel.

# Spiegelt MAX_SAFE_CTX aus llm.generate_text. Bewusst hier dupliziert statt
# importiert, weil llm.py den Wert lokal in der Funktion haelt; bei Aenderung
# beide Stellen angleichen (Sync-Test test_staging deckt den Default ab).
MAX_SAFE_CTX = 20480

# Token<->Zeichen wie in llm._estimate_num_ctx (len/3.5).
CHARS_PER_TOKEN = 3.5
# Deutsche Klinik-Texte: ~6 Zeichen/Wort + Space -> ~2 Token/Wort. Konservativ.
TOKENS_PER_WORD = 2.0


def compute_input_word_budget(
    workflow: str,
    *,
    system_prompt_chars: int,
    max_safe_ctx: int = MAX_SAFE_CTX,
    safety_tokens: int = 512,
) -> int:
    """
    Maximale Wortzahl die der GESAMTE User-Content (alle Quellen zusammen)
    haben darf, damit nach Output-Reservierung + System-Prompt noch alles
    sicher in MAX_SAFE_CTX passt.

    Reserviert das volle max_tokens_for(workflow) als Output (konservativ —
    deckt auch den Anamnese-Befund-Zweitcall mit ab, der den Anamnese-Text
    als Zusatz-Input fuehrt).

    Untergrenze 1500 Woerter, damit der Guard bei riesigem System-Prompt nicht
    absurd klein wird (dann greift im Zweifel _sample_uniformly als Notbremse).
    """
    from app.core.workflows import max_tokens_for

    reserved_output = max_tokens_for(workflow)
    system_tokens = int(system_prompt_chars / CHARS_PER_TOKEN)
    input_token_budget = max_safe_ctx - reserved_output - system_tokens - safety_tokens
    input_word_budget = int(input_token_budget / TOKENS_PER_WORD)
    return max(1500, input_word_budget)


def plan_source_compression(
    sources: list[dict],
    budget_words: int,
    *,
    raw_floor_ratio: float = 0.25,
    raw_floor_min: int = 300,
    compressed_floor_ratio: float = 0.45,
) -> dict[str, int]:
    """
    Plant, welche Quellen auf welches Wortziel verdichtet werden muessen, damit
    die Summe aller Quellen <= budget_words liegt.

    sources: Liste von dicts mit:
        label        str   — eindeutiger Quellen-Name (z.B. "selbstauskunft")
        words        int   — aktuelle Wortzahl
        compressed   bool  — wurde schon per Stage-1 verdichtet?
        compressible bool  — darf ueberhaupt verdichtet werden?

    Strategie:
      1. Noch nicht verdichtete, verdichtbare Quellen zuerst — groesste zuerst.
         Floor: nie unter raw_floor_ratio (bzw. raw_floor_min) der Rohgroesse.
      2. Reicht das nicht, als letzte Reserve schon verdichtete Quellen
         nochmals straffen — Floor compressed_floor_ratio.

    Returns: {label: target_words} nur fuer Quellen die verdichtet werden
    sollen. Leeres Dict = alles passt bereits.
    """
    total = sum(int(s.get("words", 0)) for s in sources)
    if total <= budget_words:
        return {}

    overshoot = total - budget_words
    plan: dict[str, int] = {}

    def _shave(pool: list[dict], floor_ratio: float, floor_min: int) -> None:
        nonlocal overshoot
        for s in sorted(pool, key=lambda x: -int(x.get("words", 0))):
            if overshoot <= 0:
                break
            words = int(s.get("words", 0))
            floor = max(floor_min, int(words * floor_ratio))
            removable = words - floor
            if removable <= 0:
                continue
            remove = min(removable, overshoot)
            plan[s["label"]] = words - remove
            overshoot -= remove

    uncompressed = [
        s for s in sources
        if s.get("compressible") and not s.get("compressed")
    ]
    _shave(uncompressed, raw_floor_ratio, raw_floor_min)

    if overshoot > 0:
        compressed = [
            s for s in sources
            if s.get("compressible") and s.get("compressed")
            and s["label"] not in plan
        ]
        _shave(compressed, compressed_floor_ratio, raw_floor_min)

    return plan


# ── v19.19 (S2): Chunking fuer ueberlange Stage-1-Inputs ─────────────────────
#
# Hintergrund (Log-Analyse 13.08.-09.09.): Die beiden groessten Entlassberichte
# (99k/111k Zeichen Verlauf) hatten KEINEN Stage-1-Lauf - der Stage-1-Input
# selbst sprengte das Kontextbudget (31k Tokens > 16k), generate_text kuerzte
# ihn, die Zusammenfassung fiel unter min_acceptable -> RuntimeError ->
# Fallback Rohtext -> nochmals gekuerzt. Bei P1 dasselbe Muster mit 75k-
# Transkripten. Loesung: ueberlange Inputs an Block-/Datumsgrenzen in Teile
# schneiden, jeden Teil separat verdichten, Ergebnisse chronologisch
# zusammenfuegen.

# Ab dieser Zeichenzahl wird gechunkt (~17k Tokens - passt mit System-Prompt
# und Output sicher in 32k, und auch in 16k noch knapp).
STAGE1_CHUNK_CHARS_DEFAULT = 55_000

_BLOCK_BOUNDARY_RE = re.compile(
    r"(?m)^(?="
    r"\s*(?:###\s*)?\d{1,2}\.\d{1,2}\.\d{2,4}"      # Datumszeile 12.05.2026 / ### 12.05.
    r"|\s*#{1,3}\s+\S"                                # Markdown-Ueberschrift
    r"|\s*\*\*[^*]{3,60}\*\*"                          # **fette Sektionszeile**
    r"|\s*\d{2}:\d{2}:\d{2}"                          # Timestamp 00:12:05 (Transkript)
    r"|\s*\[[AB]\]:"                                  # Sprecherzeile [A]:/[B]:
    r")"
)


def chunk_text_by_blocks(text: str, max_chars: int) -> list[str]:
    """Teilt text in Stuecke <= max_chars, geschnitten an Block-Grenzen
    (Datumszeilen, Ueberschriften, Sprecher-/Timestamp-Zeilen, sonst
    Leerzeilen). Ein einzelner Block, der groesser als max_chars ist, wird
    an Zeilengrenzen hart geteilt. Passt der Text, kommt [text] zurueck."""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Kandidaten-Grenzen: Block-Starts + Absatzgrenzen (Leerzeilen)
    starts = {0}
    for m in _BLOCK_BOUNDARY_RE.finditer(text):
        starts.add(m.start())
    for m in re.finditer(r"\n\s*\n", text):
        starts.add(m.end())
    starts = sorted(s for s in starts if s < len(text))
    blocks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]

    chunks: list[str] = []
    cur = ""
    for blk in blocks:
        if len(blk) > max_chars:
            # Ueberlanger Einzelblock: erst cur wegschreiben, dann hart teilen
            if cur:
                chunks.append(cur)
                cur = ""
            lines = blk.splitlines(keepends=True)
            piece = ""
            for ln in lines:
                # Einzelzeile laenger als max_chars: an Wortgrenzen teilen
                while len(ln) > max_chars:
                    cut = ln.rfind(" ", 0, max_chars)
                    cut = cut if cut > max_chars // 2 else max_chars
                    if piece:
                        chunks.append(piece)
                        piece = ""
                    chunks.append(ln[:cut])
                    ln = ln[cut:]
                if len(piece) + len(ln) > max_chars and piece:
                    chunks.append(piece)
                    piece = ""
                piece += ln
            if piece:
                cur = piece
            continue
        if len(cur) + len(blk) > max_chars and cur:
            chunks.append(cur)
            cur = blk
        else:
            cur += blk
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def stage1_chunk_chars() -> int:
    """Konfigurierbare Chunk-Grenze (STAGE1_CHUNK_CHARS in settings/.env)."""
    try:
        from app.core.config import settings
        return int(getattr(settings, "STAGE1_CHUNK_CHARS", STAGE1_CHUNK_CHARS_DEFAULT))
    except Exception:
        return STAGE1_CHUNK_CHARS_DEFAULT
