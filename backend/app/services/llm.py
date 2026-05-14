"""
LLM-Generierungs-Service.

Ausschliesslich Ollama (lokales Modell, On-Premise).
Kein externer API-Aufruf – alle Daten bleiben im internen Netz.
"""
import logging
import time

from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Persistenter HTTP-Client fuer Ollama-Anfragen.
# Vermeidet TCP-Handshake-Overhead bei jedem Request (Connection Pooling).
# Timeout 600s fuer langsame Generierungen (grosse Modelle, lange Texte).
_ollama_client: Optional[httpx.AsyncClient] = None


def _get_ollama_client() -> httpx.AsyncClient:
    """Gibt den persistenten Ollama-HTTP-Client zurueck, erstellt ihn bei Bedarf."""
    global _ollama_client
    if _ollama_client is None or _ollama_client.is_closed:
        _ollama_client = httpx.AsyncClient(
            base_url=settings.OLLAMA_HOST,
            timeout=httpx.Timeout(600.0, connect=10.0),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _ollama_client

# Maximale Zeichen im User-Content – nur noch als harte Notbremse
# fuer extrem lange Audio-Transkripte (>500k Zeichen).
# Fuer Verlaufsdokus greift stattdessen clean_verlauf_text() als Preprocessing.
# num_ctx wird dynamisch berechnet, qwen2.5:32b hat 128k Token Kontextfenster.
MAX_USER_CONTENT_CHARS = 500_000

# Modell-spezifische Generierungsparameter.
# Match erfolgt auf Modellname-Prefix (case-insensitive).
# Quantisierungssuffixe (:q6_K, :q4_K_M etc.) werden ignoriert – Prefix reicht.
#
# ── GPU-Empfehlungen (Stand März 2026) ──────────────────────────
#
# RTX Pro 4500 Blackwell (32GB):
#   qwen3:32b-q4_K_M  ~19GB Modell + ~4GB KV-Cache = ~23GB    <- EMPFEHLUNG
#   HINWEIS: Blackwell-GPUs (Compute 12.0, CUDA 13) benoetigen Ollama >= 0.17.
#   Aeltere Ollama-Versionen fallen stillschweigend auf CPU zurueck!
#   Pruefe mit: ollama ps → Processor-Spalte muss "100% GPU" zeigen.
#   Bei Problemen: Ollama aktualisieren (curl -fsSL https://ollama.com/install.sh | sh)
#
# RTX 4090 (24GB):
#   qwen3:32b-q4_K_S  ~18GB Modell, passt mit kleinem KV-Cache (num_ctx ≤ 6144)
#   WICHTIG: q4_K_M (~19GB) + KV-Cache (~4.8GB bei 8k ctx) = 23.8GB → 100MB zu viel!
#   Daher: q4_K_S verwenden (~1GB kleiner) ODER KV-Cache-Quantisierung aktivieren:
#     OLLAMA_FLASH_ATTENTION=true + OLLAMA_KV_CACHE_TYPE=q8_0 (halbiert KV-Cache)
#   Alternativ: qwen3:14b-q5_K_M (~10GB) – deutlich mehr Headroom, gute Qualitaet
#
# Fallback (beide GPUs):
#   qwen3:14b          ~8.3GB  Sehr gute Qualitaet, grosszuegiger Kontext moeglich
#   qwen3:30b-a3b      ~17GB   MoE, nur 3B aktive Params – schnell aber duennerer Fliesstext
#
MODEL_PROFILES: dict[str, dict] = {
    # Reasoning-Modelle: groesserer Mindest-Kontext, niedrigere Temperatur
    "deepseek-r1": {"min_ctx": 8192, "temperature": 0.2, "top_p": 0.85},
    "deepseek":    {"min_ctx": 8192, "temperature": 0.2, "top_p": 0.85},
    # Standard-Modelle: dynamischer Kontext
    "qwen2.5":     {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    # Qwen3: temperature 0.4 fuer klinische Dokumentation (niedrig = faktentreu,
    # hoch = kreativer). Qwen3-Dokumentation empfiehlt 0.7 fuer Chat, aber fuer
    # medizinische Berichte ist 0.3-0.4 der Sweet Spot.
    "qwen3":       {"min_ctx": 2048, "temperature": 0.4, "top_p": 0.85},
    "llama":       {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    "gemma":       {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    "mistral":     {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
    "_default":    {"min_ctx": 2048, "temperature": 0.3, "top_p": 0.9},
}

def _get_model_profile(model_name: str) -> dict:
    """Gibt modellspezifische Generierungsparameter zurück."""
    name_lower = model_name.lower()
    for prefix, profile in MODEL_PROFILES.items():
        if prefix != "_default" and name_lower.startswith(prefix):
            return profile
    return MODEL_PROFILES["_default"]

# Maximale Zeichen für Stilvorlagen im System-Prompt.
# Durch explizite "nur Stil"-Rahmung bei C&P-Texten ist das Looping-Risiko
# deutlich reduziert – 2000 Zeichen reichen für eine vollständige Beispieldoku.
MAX_STYLE_CONTEXT_CHARS = 2_000


def truncate_style_context(text: str) -> str:
    """
    Kürzt Stilvorlagen auf MAX_STYLE_CONTEXT_CHARS.
    Schneidet an Satzgrenze damit der letzte Satz vollständig bleibt.
    """
    if len(text) <= MAX_STYLE_CONTEXT_CHARS:
        return text
    truncated = text[:MAX_STYLE_CONTEXT_CHARS]
    # Sauber am letzten Satzende abschneiden
    last_stop = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if last_stop > MAX_STYLE_CONTEXT_CHARS // 2:
        truncated = truncated[:last_stop + 1]
    logger.info(
        "Stilvorlage gekürzt: %d → %d Zeichen (verhindert LLM-Wiederholungsloop)",
        len(text), len(truncated),
    )
    return truncated


def deduplicate_paragraphs(text: str, *, strict_mode: bool = False) -> str:
    """
    Entfernt wiederholte Absätze aus dem LLM-Output.
    qwen3:32b neigt bei zu langem Kontext dazu denselben Absatz
    mehrfach zu generieren – dieser Filter erkennt und entfernt Duplikate.
    Überschriften (**Fett** oder Zeile mit nur Grossbuchstaben) bleiben erhalten.

    Args:
        text: Der zu deduplizierende Text.
        strict_mode: Wenn True, nur byte-identische Absätze entfernen
                     (für Stage-1-Synthesen, wo thematische Wiederholungen
                     mit leicht anderer Formulierung legitim sind).
                     Default False = bisheriges Verhalten (case-insensitive,
                     whitespace-normalisiert).
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    seen: set[str] = set()
    result = []
    duplicates = 0
    for p in paragraphs:
        if strict_mode:
            # Nur byte-identische Absätze entfernen (kein lower, kein split-join)
            key = p
        else:
            # Bisheriges Verhalten: normalisierter Vergleichsschlüssel
            key = " ".join(p.lower().split())
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        result.append(p)
    if duplicates:
        mode_label = "strict" if strict_mode else "normal"
        logger.warning(
            "LLM-Output: %d doppelte Absätze entfernt (Wiederholungsloop erkannt, mode=%s)",
            duplicates, mode_label,
        )
    return "\n\n".join(result)


def strip_markdown_formatting(text: str) -> str:
    """
    Entfernt Markdown-Formatierungen aus dem LLM-Output.

    Die Prompts verbieten Markdown ausdruecklich, aber das Modell generiert
    trotzdem gelegentlich **bold**, ## Headers und aehnliches. Dieser
    Post-Processing-Filter strippt diese Zeichen zuverlaessig.

    Was entfernt wird:
    - **text** → text  (Bold)
    - __text__ → text  (Bold alternative)
    - *text* → text    (nur als Paar, nicht einzelne Sternchen)
    - ## Header → Header (Ueberschriften-Marker)
    - --- → leere Zeile (horizontale Trennlinien)
    """
    if not text:
        return text

    import re as _re

    # **bold** -> bold
    text = _re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", text)
    # __bold__ -> bold
    text = _re.sub(r"__([^_\n]+?)__", r"\1", text)
    # *italic* -> italic (nur Paare, nicht einzelne Sternchen)
    text = _re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", text)
    # Heading-Marker am Zeilenanfang: "## Text" / "# Text" / "### Text"
    text = _re.sub(r"(?m)^#{1,6}\s+", "", text)
    # Horizontale Trennlinien
    text = _re.sub(r"(?m)^[-_*]{3,}\s*$", "", text)
    # Doppelte Leerzeilen konsolidieren
    text = _re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def substitute_patient_placeholders(text: str, patient_name: dict | None) -> str:
    """
    Ersetzt verbleibende "[Patient/in]"-Platzhalter im LLM-Output durch den
    echten Namen. Das Modell sollte eigentlich die Initiale selbst einsetzen,
    aber manchmal kopiert es den Platzhalter aus dem Beispiel-Prompt.

    patient_name: Dict aus extract_patient_name() oder None.

    v16 Audit-Patch (A1): Sanity-Check als Defense-in-Depth gegen Müll-Werte
    wie "die Klientin/der Klient". Wenn `initial` nicht plausibel kurz ist
    oder Klient/Patient-Substrings enthält: Replace ueberspringen, lieber
    den Platzhalter im Output stehen lassen als mit Müll zu kontaminieren.
    Spiegelt den entsprechenden Check in prompts.py::build_system_prompt.
    """
    if not text or not patient_name or not patient_name.get("initial"):
        return text

    anrede = patient_name.get("anrede") or ""
    initial = patient_name["initial"]
    full_ref = f"{anrede} {initial}".strip()

    # v16 A1 Sanity-Check: Replace nur ausführen wenn full_ref plausibel ist
    full_ref_low = full_ref.lower()
    is_safe_ref = (
        len(full_ref) <= 12
        and "klient" not in full_ref_low
        and "patient" not in full_ref_low
        and full_ref not in ("", ".", "Frau .", "Herr .")
    )
    if not is_safe_ref:
        logger.warning(
            "substitute_patient_placeholders: full_ref %r unplausibel "
            "(zu lang oder Klient/Patient enthalten) - Replace uebersprungen",
            full_ref,
        )
        return text

    replacements = [
        ("Herr/[Patient/in]", full_ref),
        ("Frau/[Patient/in]", full_ref),
        ("[Patient/in]", full_ref),
        ("[Patientin]", full_ref),
        ("[Patient]", full_ref),
        ("[Name]", full_ref),
        ("[Initiale]", initial),
        # "Frau X." / "Herr X." als explizite Fehl-Platzhalter
        ("Frau X.", full_ref if anrede == "Frau" else f"Frau {initial}"),
        ("Herr X.", full_ref if anrede == "Herr" else f"Herr {initial}"),
    ]

    for placeholder, replacement in replacements:
        text = text.replace(placeholder, replacement)

    return text


# ─────────────────────────────────────────────────────────────────────────────
# v19.2 Schritt 0: clean_verlauf_text - erweiterte Pattern-Erkennung.
#
# Hintergrund: Die ursprüngliche Implementierung war auf ein älteres PDF-Layout
# zugeschnitten (Sprechende Header wie "Seite X von Y", "Verlaufsdokumentation
# Stand:", Teilnahmezeilen "hat teilgenommen"). Die aktuelle Produktions-PDF-
# Extraktion liefert ein anderes Format:
#   --- Seite N ---
#   DD.MM.YYYY
#   Sitzungstyp[(Therapeut)]HH:MM - HH:MM     <- OCR-Klebebug, keine Trenner
#   [optionaler Folgeinhalt ODER direkt nächster Header]
#
# Ergebnis: bei diesem Format griff *keiner* der alten Filter -> 0% Reduktion.
#
# v19.2 erweitert die Funktion ADDITIV:
#   - Alte Patterns bleiben (für ältere PDF-Layouts und Eval-Regressionsschutz)
#   - Neue Patterns für aktuelles Format (Klebebug-Repair, Seiten-Marker,
#     Sitzungs-Header mit/ohne Inhalt-Lookahead, Datums-Normalisierung)
#
# Wichtig: keine semantische Änderung an alten Tests. Die Testklassen
# TestCleanVerlaufTextHeaders/Participation/Admin/Leerzeilen müssen alle
# weiter grün laufen.
# ─────────────────────────────────────────────────────────────────────────────

# Sitzungs-Typen aus der aktuellen sysTelios-Verlaufsdoku.
# Werden als Header erkannt sobald ein Klebebug (Typ+Zeit ohne Trenner)
# oder ein normaler "Typ Zeit"-Header gefunden wird.
SITZUNGS_TYPEN = (
    "Aufwecken, Anregen",
    "Bahnen, Verankern",
    "Beobachten, Integrieren",
    "Prozessreflexion",
    "Bezugsgruppe",
    "Gruppe non-verbal 1",
    "Gruppe non-verbal 2",
    "Gruppe non-verbal",
    "Einzelgespräch",
    "Einzeldoku",
    "Beratung - ausführlich",
    "Beratung",
    "Sprechstunde",
    "Chefärztliche Wahlleistungsgruppe",
    "Abschlusskontakt",
    "Abschlussgespräch",
    "Aufnahmegespräch",
    "Kunsttherapie",
    "Musiktherapie",
    "Körpertherapie",
    "Bewegungstherapie",
    "Visite",
    "Übergangsgruppe",
)


def clean_verlauf_text(text: str) -> str:
    """
    Bereinigt extrahierten Verlaufsdokumentationstext vor dem LLM-Aufruf.

    Operationen (in dieser Reihenfolge):
    1. OCR-Klebebug-Repair: "Anregen09:30" -> "Anregen 09:30" (global)
    2. PDF-Seitenheader entfernen:
       - "Verlaufsdokumentation - Stand: ..."
       - "Seite X von Y"
       - "(A12345) Zi. 123"
       - "Name, Vorname (A12345)"
       - "--- Seite N ---" / "[Pseudonymisiertes Dokument ...]"  (NEU v19.2)
    3. Administrative Zeilen entfernen (Termin, Raum, AU-Bescheinigung, ...)
    4. Reine Teilnahmezeilen ("hat teilgenommen", "entschuldigt", ...) inkl.
       rueckwirkender Entfernung des zugehoerigen Datums-/Zeit-Blocks
    5. Inhaltslose Sitzungs-Header (Typ+Zeit ohne folgenden Inhalt - direkt
       naechster Header oder Datum) entfernen   (NEU v19.2)
    6. Reine Zeiteintraege ohne nachfolgenden Inhalt entfernen
    7. Reine Therapeutennamen-Header entfernen
    8. Datums-Zeilen (DD.MM.YYYY allein) zu "### DD.MM.YYYY" normalisieren,
       Doppel-Datumeintraege an Seitengrenzen deduplizieren  (NEU v19.2)
    9. Mehrfache Leerzeilen kollabieren

    Erhalten bleiben:
    - Echte Sitzungs-Inhalte (alles nach einem Header das nicht selbst Header ist)
    - Sitzungs-Header MIT Folge-Inhalt (als Zeit-/Methoden-Anker fuer das LLM)
    - Datums-Marker (Chronologie wichtig fuer die Synthese)
    """
    import re

    if not text or not text.strip():
        return text

    orig_chars = len(text)
    orig_words = len(text.split())

    # v19.2: Klebebug-Repair GLOBAL (auch im Inhalt nuetzlich, nicht nur Headern).
    # "Anregen09:30" -> "Anregen 09:30"
    # "Wolf)11:45"   -> "Wolf) 11:45"
    klebe_re = re.compile(r"([a-zäöüß\)])(\d{1,2}:\d{2})")
    text = klebe_re.sub(r"\1 \2", text)

    lines = text.split("\n")
    cleaned: list[str] = []
    i = 0
    leerer_header_removed = 0
    seiten_marker_removed = 0

    # Pattern fuer PDF-Seitenheader (alt, Kompatibilitaet)
    header_patterns = [
        re.compile(r"Verlaufsdokumentation\s*[-–]\s*Stand:", re.IGNORECASE),
        re.compile(r"Seite\s+\d+\s+von\s+\d+", re.IGNORECASE),
        re.compile(r"\(A\d+\)\s+Zi\.\s+\d+"),   # (A12345) Zi. 123
        re.compile(r"^[A-ZÄÖÜ][a-zäöüß]+,\s+[A-ZÄÖÜ][a-zäöüß-]+\s+\(A\d+\)"),  # Name (Anummer)
    ]

    # v19.2: Seiten-Marker aus aktueller PDF-Extraktion
    seite_marker_re = re.compile(
        r"^---\s*Seite\s+\d+\s*---\s*$"
        r"|^\[Pseudonymisiertes Dokument.*\]\s*$",
        re.IGNORECASE,
    )

    # Pattern fuer inhaltsleere Teilnahmezeilen (alt)
    participation_only = re.compile(
        r"^(hat teilgenommen|nicht teilgenommen|entschuldigt fehlt|"
        r"hat nicht teilgenommen|teilgenommen|abgebrochen|krankgemeldet"
        r"|unentschuldigt gefehlt|entschuldigt|ausgefallen|fiel aus"
        r"|fand nicht statt|wurde abgesagt|Termin entfaellt"
        r"|Pat\.\s+(hat\s+)?teilgenommen)[\.\s]*$",
        re.IGNORECASE,
    )

    # Pattern fuer reine Zeiteintraege (HH:MM - HH:MM Therapiename), alt
    time_entry = re.compile(r"^\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}\s+\S.*$")

    # Pattern fuer Datumszeilen allein
    # Alt-Pattern (DD.MM.YYYY 4-stellig) bleibt fuer alte Tests erhalten,
    # v19.2 erweitert das Akzept-Pattern auf 2- und 4-stellige Jahreszahlen.
    date_only_strict = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")            # alt
    date_only_loose  = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}\s*$")   # v19.2

    # Pattern fuer reine Therapeutennamen-Header (alt)
    therapist_header = re.compile(
        r"^(Dr\.\s+|Dipl\.\s*-?\s*Psych\.\s+|M\.?A\.?\s+)?"
        r"[A-ZÄÖÜ][a-zäöüß]+([-\s][A-ZÄÖÜ][a-zäöüß]+)*:?\s*$"
    )

    # Pattern fuer administrative Zeilen ohne therapeutischen Inhalt (alt)
    admin_patterns = [
        re.compile(r"^\s*Termin(e|planung)?\s*(am|fuer|:)", re.IGNORECASE),
        re.compile(r"^\s*Raum\s*\d+", re.IGNORECASE),
        re.compile(r"^\s*Tel\.\s*\d+", re.IGNORECASE),
        re.compile(r"^\s*Rezept\s*(ausgestellt|verlängert|erneuert)", re.IGNORECASE),
        re.compile(r"^\s*Ueberweisung\s*(an|ausgestellt)", re.IGNORECASE),
        re.compile(r"^\s*Krankmeldung\s*(bis|ausgestellt|verlängert)", re.IGNORECASE),
        re.compile(r"^\s*AU[-\s]?(Bescheinigung|bis)", re.IGNORECASE),
    ]

    # v19.2: Sitzungs-Header (Therapietyp + optional Therapeut + Zeit).
    # Beispiele nach Klebebug-Repair:
    #   "Aufwecken, Anregen 09:30 - 11:10"
    #   "Einzelgespräch (J.Wolf) 11:00 - 11:50"
    #   "Abschlusskontakt (J.Wolf) 11:45 - 11:55"
    sitzungs_header_re = re.compile(
        r"^(?P<typ>" + "|".join(re.escape(t) for t in SITZUNGS_TYPEN) + r")"
        r"(?P<therapeut>\s*\([A-Za-zäöüÄÖÜß.\s\-/]+\))?"
        r"\s*(?P<zeit>\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2})\s*$",
        re.IGNORECASE,
    )

    def _is_structural_marker(line_stripped: str) -> bool:
        """Erkennt 'leere' Marker-Zeilen die als Folge fuer einen Header nicht
        als Inhalt zaehlen (naechster Sitzungs-Header, Datum, Seiten-Marker)."""
        if not line_stripped:
            return True
        if sitzungs_header_re.match(line_stripped):
            return True
        if date_only_loose.match(line_stripped):
            return True
        if seite_marker_re.match(line_stripped):
            return True
        if any(p.search(line_stripped) for p in header_patterns):
            return True
        return False

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        # Leere Zeilen durchlassen (werden spaeter komprimiert)
        if not stripped:
            cleaned.append("")
            i += 1
            continue

        # v19.2: Seiten-Marker aus aktueller PDF-Extraktion entfernen
        if seite_marker_re.match(stripped):
            seiten_marker_removed += 1
            i += 1
            continue

        # Alte Seitenheader entfernen
        if any(p.search(stripped) for p in header_patterns):
            i += 1
            continue

        # Administrative Zeilen entfernen
        if any(p.match(stripped) for p in admin_patterns):
            i += 1
            continue

        # Teilnahmezeilen + zugehoerigen Block rueckwirkend entfernen
        if participation_only.match(stripped):
            while cleaned and (
                date_only_strict.match(cleaned[-1].strip())
                or date_only_loose.match(cleaned[-1].strip())
                or time_entry.match(cleaned[-1].strip())
                or sitzungs_header_re.match(cleaned[-1].strip())
                or cleaned[-1].strip() == ""
            ):
                cleaned.pop()
            i += 1
            continue

        # v19.2: Datums-Zeile (allein) -> Tagestrenner "### DD.MM.YYYY"
        if date_only_loose.match(stripped):
            normalized = f"### {stripped}"
            # Doppel-Datum deduplizieren: wenn das LETZTE gesehene Tagestrenner-
            # Datum dasselbe Datum war (z.B. Datum als Footer am Seiten-Ende
            # und als Header am Anfang der Folgeseite — gleicher Tag, doppelt),
            # ueberspringen wir das zweite Vorkommen.
            last_date_trenner = next(
                (c for c in reversed(cleaned)
                 if c.startswith("### ") and date_only_loose.match(c[4:].strip())),
                None,
            )
            if last_date_trenner is not None and last_date_trenner.strip() == normalized:
                i += 1
                continue
            cleaned.append(normalized)
            i += 1
            continue

        # v19.2: Sitzungs-Header (Typ+Zeit). Mit Lookahead pruefen ob Inhalt folgt.
        sh_match = sitzungs_header_re.match(stripped)
        if sh_match:
            # Lookahead: naechste nicht-leere Zeile
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            # Dokument-Ende ohne Folge-Inhalt -> Header weg
            if j >= len(lines):
                leerer_header_removed += 1
                i = j
                continue
            next_line = lines[j].strip()
            # Wenn die naechste nicht-leere Zeile selbst ein Marker ist:
            # dieser Header hat keinen Inhalt -> entfernen
            if _is_structural_marker(next_line):
                leerer_header_removed += 1
                i = j
                continue
            # Header behalten, Zeit-Trennung normalisieren
            typ = sh_match["typ"]
            therapeut = sh_match["therapeut"] or ""
            zeit = sh_match["zeit"]
            cleaned.append(f"{typ}{therapeut} {zeit}".strip())
            i += 1
            continue

        # Reine Zeiteintraege ohne nachfolgenden Inhalt ueberspringen (alt)
        if time_entry.match(stripped):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and participation_only.match(lines[j].strip()):
                i = j + 1
                continue
            if j >= len(lines) or date_only_strict.match(lines[j].strip()):
                i += 1
                continue

        # Therapeutennamen allein auf einer Zeile ueberspringen (alt)
        if therapist_header.match(stripped) and len(stripped) < 40:
            j = i + 1
            while j < len(lines) and not lines[j].strip() and j - i < 3:
                j += 1
            if j < len(lines) and (
                participation_only.match(lines[j].strip())
                or date_only_strict.match(lines[j].strip())
                or time_entry.match(lines[j].strip())
            ):
                i += 1
                continue

        cleaned.append(line)
        i += 1

    result = "\n".join(cleaned)

    # Mehr als 2 aufeinanderfolgende Leerzeilen auf 1 reduzieren
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()

    new_chars = len(result)
    new_words = len(result.split()) if result else 0
    reduction_chars_pct = (
        (1 - new_chars / orig_chars) * 100 if orig_chars > 0 else 0.0
    )
    reduction_words_pct = (
        (1 - new_words / orig_words) * 100 if orig_words > 0 else 0.0
    )
    if orig_chars > new_chars:
        logger.info(
            "Verlaufsdoku bereinigt: %d→%d Zeichen (-%.0f%%), %d→%d Wörter (-%.1f%%), "
            "%d leere Sitzungs-Header, %d Seiten-Marker entfernt",
            orig_chars, new_chars, reduction_chars_pct,
            orig_words, new_words, reduction_words_pct,
            leerer_header_removed, seiten_marker_removed,
        )

    return result


async def generate_text(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 2048,
    model: Optional[str] = None,
    workflow: Optional[str] = None,
    on_progress=None,
    max_words: Optional[int] = None,
    expected_keywords: Optional[list[str]] = None,
    temperature_override: Optional[float] = None,
    skip_aggressive_dedup: bool = False,
) -> dict:
    """
    Generiert Text ausschliesslich via lokalem Ollama-Modell.

    num_ctx wird dynamisch berechnet: Input-Tokens * 1.2 + max_tokens,
    auf das nächste Vielfache von 1024 aufgerundet.
    Das vermeidet unnötige KV-Cache-Reallokierungen bei jedem Request.

    v16-Parameter (optional, durchgereicht an postprocess_output):
      max_words:         Hartes Wort-Limit fuer den Output (style-derived
                         Max aus prompts.derive_word_limits). Wenn der
                         LLM-Output das Limit ueberschreitet, wird er an
                         einer Satzgrenze abgeschnitten.
      expected_keywords: Liste von Keywords die im Output vorhanden sein
                         muessten (z.B. "Trennung", "F33.1"). Fehlende
                         werden als Warnung geloggt.

    v19.2-Parameter:
      temperature_override: Wenn gesetzt, ueberschreibt die Modell-Profil-
                         Temperatur fuer diesen Call. Wird primaer von der
                         Stage-1-Pipeline (verlauf_summary) genutzt, die
                         mit 0.2 / 0.1 (Retry) maximale Quellentreue
                         erzwingt. None = Profil-Default (qwen3: 0.4).

    v19.2.1-Parameter:
      skip_aggressive_dedup: Wenn True, nutzt deduplicate_paragraphs den
                         strict_mode (nur byte-identische Duplikate). Fuer
                         Stage-1-Synthesen, wo thematische Wiederholungen
                         strukturell vorkommen (eine Sitzung kann zu mehreren
                         Themen-Buckets gehoeren). Default False = bisheriges
                         Verhalten (case-insensitive Dedup).
    """
    if len(user_content) > MAX_USER_CONTENT_CHARS:
        user_content = _sample_uniformly(user_content, MAX_USER_CONTENT_CHARS)

    # Sicherheitscheck: wenn Input + Output > MAX_SAFE_CTX, User-Content kuerzen.
    # VRAM-Budget haengt von der GPU ab:
    #   RTX Pro 4500 (32GB): 32GB - 19GB Modell - 1GB Overhead = ~12GB fuer KV-Cache
    #     → bei FP16 KV-Cache: 16384 Token sicher
    #     → bei q8_0 KV-Cache: ~32000 Token sicher (KV halbiert)
    #   RTX 4090 (24GB):     24GB - 18GB Modell(q4_K_S) - 1GB = ~5GB fuer KV-Cache
    #     → 5GB / ~0.75GB pro 1024 Tokens ≈ 6144 Token sicher
    #
    # v19.1: Auf 20480 angehoben damit grosse Inputs wie eb-01 (~13.300 Input-
    # Tokens) noch genug Headroom fuer Entlassbericht-Output (~5500 Tokens
    # max_tokens) haben. ZWINGEND erforderlich am Pod:
    #   OLLAMA_FLASH_ATTENTION=true
    #   OLLAMA_KV_CACHE_TYPE=q8_0
    # Beide werden in runpod-start.sh gesetzt. Ohne diese Settings besteht
    # bei Inputs > 17k Tokens akutes VRAM-OOM-Risiko. Der OOM-Fallback in
    # _is_vram_error() faengt das ab (Retry mit num_ctx=8192), reduziert
    # aber die Generierungsqualitaet bei langen Inputs deutlich.
    MAX_SAFE_CTX = 20480
    # Workflow-spezifische Mindest-Output-Tokens:
    # Der Output darf nie unter dieses Minimum fallen, sonst wird der Input gekuerzt.
    MIN_OUTPUT_TOKENS = {
        "entlassbericht":       2000,  # mind. 600 Woerter → ~2000 Tokens
        "verlaengerung":        1500,  # mind. 400 Woerter → ~1500 Tokens
        "folgeverlaengerung":   1500,  # mind. 400 Woerter → ~1500 Tokens
        "akutantrag":           800,   # mind. 200 Woerter → ~800 Tokens
        "anamnese":             1500,  # mind. 350 Woerter + Befund → ~1500 Tokens
        "dokumentation":        1000,  # mind. 250 Woerter → ~1000 Tokens
    }
    min_output = MIN_OUTPUT_TOKENS.get(workflow, 1000) if workflow else 1000

    estimated_input_tokens = int((len(system_prompt) + len(user_content)) / 3.5)

    # max_tokens dynamisch anpassen: so viel wie moeglich, aber mindestens min_output
    if estimated_input_tokens + max_tokens > MAX_SAFE_CTX:
        # Zuerst: max_tokens auf das Maximum setzen das nach Input noch passt
        available_for_output = MAX_SAFE_CTX - estimated_input_tokens - 200
        if available_for_output >= min_output:
            # Genug Platz → max_tokens auf verfuegbaren Raum anpassen
            max_tokens = min(max_tokens, available_for_output)
            logger.info(
                "max_tokens dynamisch angepasst: %d (Input: ~%d Tokens, Budget: %d)",
                max_tokens, estimated_input_tokens, MAX_SAFE_CTX,
            )
        else:
            # Nicht genug Platz → Input kuerzen um min_output zu garantieren
            max_tokens = max(max_tokens, min_output)
            available_input = MAX_SAFE_CTX - max_tokens - int(len(system_prompt) / 3.5) - 200
            max_user_chars = int(available_input * 3.5)
            if max_user_chars > 0 and len(user_content) > max_user_chars:
                original_len = len(user_content)
                user_content = _sample_uniformly(user_content, max_user_chars)
                logger.warning(
                    "User-Content gekuerzt um min. %d Output-Tokens zu garantieren: "
                    "%d → %d Zeichen",
                    min_output, original_len, max_user_chars,
                )

    # Qwen3: Thinking-Mode deaktivieren via /no_think am Ende des User-Content.
    # Verhindert <think>...</think> Blöcke im Output die nicht in klinische Dokumente gehören.
    effective_model_for_nothink = model or settings.OLLAMA_MODEL
    if effective_model_for_nothink.lower().startswith("qwen3"):
        user_content = user_content.rstrip() + "\n\n/no_think"

    # Workflow-spezifischer Assistant-Primer: zwingt Modell direkt in den Text
    # ohne Verweigerung oder Erklaerungen. Primer wird dem Output vorangestellt
    # und ist fuer den Therapeuten nicht sichtbar.
    PRIMERS = {
        "entlassbericht":       "Zu Beginn des stationären Aufenthalts",
        "verlaengerung":        "Im bisherigen Verlauf des stationären Aufenthalts",
        "folgeverlaengerung":   "Im weiteren Verlauf seit dem letzten Verlängerungsantrag",
        "akutantrag":           "Folgende Krankheitssymptomatik macht in der Art und Schwere",
        "anamnese":             "",  # Kein Primer – Modell soll selbst mit Patientenname beginnen
        "dokumentation":        "Auftragsklärung\n\n",
    }
    assistant_primer = PRIMERS.get(workflow or "", "")

    t0 = time.time()
    result = await _generate_ollama(
        system_prompt, user_content, max_tokens,
        model=model, assistant_primer=assistant_primer,
        temperature_override=temperature_override,
    )

    # ── Postprocessing (v19.1: ausgelagert in _postprocess_text, damit
    # ein etwaiger Retry den exakt gleichen Pfad durchlaeuft) ──────────────
    raw_first_pass = result.get("text") or ""
    result["text"] = _postprocess_text(
        text=raw_first_pass,
        assistant_primer=assistant_primer,
        workflow=workflow,
        max_words=max_words,
        expected_keywords=expected_keywords,
        strict_dedup=skip_aggressive_dedup,
    )

    # ── v19.1: Think-Block-Detection + ggf. Retry ────────────────────────
    # Wenn der erste Pass durch dominanten Think-Block faktisch gescheitert
    # ist (zu kurzer Output UND Think-Indikatoren), starten wir EINEN
    # haerteren Retry. Wenn auch der scheitert, geht der schlechte Output
    # zurueck - aber mit degraded=True markiert.
    is_too_short, reason = _is_output_implausibly_short(
        workflow=workflow,
        final_text=result.get("text", ""),
        telemetry=result.get("telemetry", {}),
    )

    if is_too_short:
        logger.warning(
            "Output-Plausibilitaetspruefung fehlgeschlagen: %s - Retry startet",
            reason,
        )
        retry_result = await _retry_without_thinking(
            system_prompt=system_prompt,
            user_content=user_content,
            max_tokens=max_tokens,
            model=model,
            assistant_primer=assistant_primer,
            original_telemetry=result.get("telemetry", {}),
        )

        # Retry-Output ebenfalls durch Postprocessing
        retry_raw = retry_result.get("text") or ""
        retry_processed = _postprocess_text(
            text=retry_raw,
            assistant_primer=assistant_primer,
            workflow=workflow,
            max_words=max_words,
            expected_keywords=expected_keywords,
            strict_dedup=skip_aggressive_dedup,
        )

        retry_words = len(retry_processed.split()) if retry_processed else 0
        first_words = len(result.get("text", "").split()) if result.get("text") else 0

        if retry_processed and retry_words > first_words:
            # Retry hat geliefert - nehmen, Original-Telemetrie als
            # original_telemetry mitgeben fuer Audit
            logger.info(
                "Retry erfolgreich: Output %d -> %d Woerter (Modell hat im Retry "
                "den Think-Block vermieden)",
                first_words, retry_words,
            )
            original_telemetry = result.get("telemetry", {})
            result = {
                "text":                retry_result["text"],  # Roh, fuer Konsistenz im result
                "model_used":          retry_result["model_used"],
                "token_count":         retry_result.get("token_count"),
                "telemetry":           retry_result.get("telemetry", {}),
                "retry_used":          True,
                "original_too_short_reason": reason,
                "original_telemetry":  original_telemetry,
            }
            result["text"] = retry_processed
        else:
            logger.error(
                "Retry hat das Problem NICHT geloest - beide Versuche zu kurz "
                "(first=%d Woerter, retry=%d Woerter). Output wird trotzdem "
                "zurueckgegeben, aber als degraded markiert.",
                first_words, retry_words,
            )
            result["degraded"] = True
            result["degraded_reason"] = reason
            result["retry_used"] = True
            if retry_result.get("retry_failed"):
                result["retry_failed"] = True

    result["duration_s"] = round(time.time() - t0, 1)
    logger.info(
        "Generierung: %d Tokens in %.1fs (Modell: %s)%s",
        result.get("token_count", 0),
        result["duration_s"],
        result["model_used"],
        " [DEGRADED]" if result.get("degraded") else (
            " [RETRY]" if result.get("retry_used") else ""
        ),
    )
    return result


def _estimate_num_ctx(system_prompt: str, user_content: str, max_tokens: int) -> int:
    """
    Schätzt den benötigten Kontext basierend auf der tatsächlichen Input-Länge.

    Faustregel: 1 Token ≈ 3.5 Zeichen (Deutsch, klinischer Text).
    Puffer: 20% Sicherheitsmarge + max_tokens für den Output.
    Rundet auf das nächste Vielfache von 512 für Ollama-Effizienz.

    VRAM-Budget fuer KV-Cache (nach Modell-Gewichten):
      RTX Pro 4500 (32GB): ~12GB frei → 16384 Tokens sicher (FP16 KV)
                                        32768 mit OLLAMA_KV_CACHE_TYPE=q8_0
      RTX 4090 (24GB):     ~5GB frei  → 6144 Tokens sicher (FP16 KV)
                                        12288 mit OLLAMA_KV_CACHE_TYPE=q8_0

    Hartes Maximum: 12288 Tokens – konservativer Wert der auf beiden GPUs
    funktioniert (RTX 4090 mit q8_0 KV-Cache, RTX Pro 4500 ohne).
    Fuer RTX Pro 4500 mit mehr Headroom: MAX_SAFE_CTX in generate_text() erhoehen.
    """
    total_chars = len(system_prompt) + len(user_content)
    estimated_tokens = int(total_chars / 3.5)
    needed = int(estimated_tokens * 1.2) + max_tokens
    # Auf nächstes Vielfaches von 512 aufrunden, Minimum 2048
    rounded = max(2048, ((needed + 511) // 512) * 512)
    # Hartes Maximum: 16384 Tokens – sicher auf RTX Pro 4500 (q4_K_M + FP16 KV-Cache).
    # Fuer RTX 4090: auf 8192 reduzieren oder OLLAMA_KV_CACHE_TYPE=q8_0 verwenden.
    return min(rounded, 16384)


def _sample_uniformly(text: str, max_chars: int, n_windows: int = 10) -> str:
    """
    Kuerzt einen langen Text durch gleichmaessiges Sampling.

    Das Transkript wird in n_windows gleich grosse Abschnitte aufgeteilt.
    Aus jedem Abschnitt wird ein proportionaler Teil an Zeilengrenzen
    entnommen. So bleibt jeder Gespraechsabschnitt anteilig erhalten –
    kein Teil des Gespraechs wird komplett uebersprungen.

    Schnitte erfolgen immer an Zeilengrenzen ([A]:/[B]: Sprecher-Marker)
    damit keine Saetze mitten im Wort abgebrochen werden.
    """
    lines = text.splitlines(keepends=True)
    total = len(lines)
    window_size = max(1, total // n_windows)
    chars_per_window = max_chars // n_windows

    sampled = []

    for i in range(n_windows):
        start_line = i * window_size
        end_line   = (i + 1) * window_size if i < n_windows - 1 else total
        window_lines = lines[start_line:end_line]
        window_text  = "".join(window_lines)

        if len(window_text) <= chars_per_window:
            sampled.append(window_text)
        else:
            # An naechster Zeilengrenze kuerzen
            truncated = window_text[:chars_per_window]
            last_newline = truncated.rfind("\n")
            if last_newline > chars_per_window // 2:
                truncated = truncated[:last_newline + 1]
            sampled.append(truncated)

        if i < n_windows - 1:
            sampled.append("\n")

    result = "".join(sampled)
    logger.warning(
        "Transkript gekuerzt: %d → %d Zeichen (%d Abschnitte gleichmaessig gesampelt)",
        len(text), len(result), n_windows,
    )
    return result


def _is_vram_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(k in msg for k in (
        "cuda out of memory", "out of memory", "cublas",
        "illegal memory access", "vram", "oom",
    ))


def _classify_ollama_error(status_code: int, body: str) -> RuntimeError:
    """Wandelt Ollama-HTTP-Fehler in sprechende RuntimeErrors um."""
    if "EOF" in body or "completion" in body:
        return RuntimeError(
            f"Ollama Kontext-Fehler (Eingabe zu lang). Details: {body[:200]}"
        )
    if any(k in body.lower() for k in ("out of memory", "cuda", "cublas", "oom")):
        return RuntimeError(f"cuda out of memory: {body[:200]}")
    return RuntimeError(f"Ollama Fehler {status_code}: {body}")


async def _ollama_unload(model: Optional[str] = None) -> None:
    """Entlädt das angegebene Ollama-Modell aus dem VRAM (keep_alive=0)."""
    target = model or settings.OLLAMA_MODEL
    try:
        client = _get_ollama_client()
        await client.post(
            "/api/chat",
            json={"model": target, "keep_alive": 0,
                  "messages": [{"role": "user", "content": ""}]},
        )
        logger.info("Ollama-Modell '%s' aus VRAM entladen (OOM-Recovery)", target)
    except Exception as e:
        logger.debug("Ollama-Entladen nicht moeglich: %s", e)


# =============================================================================
# v19.1: Think-Block-Detection & Retry-Layer
# =============================================================================
# Hintergrund (eval_report2 vom 12.05.2026):
#
#   Qwen3 verbraucht bei sehr langen, anweisungsdichten Prompts (z.B.
#   Entlassbericht mit ~10.600 Woerter Input) gelegentlich das gesamte
#   num_predict-Budget im <think>...</think>-Block. Nach Stripping
#   bleibt ein leerer oder fast leerer Output. /no_think + think:false
#   reichen als Schutz nicht zuverlaessig.
#
# Strategie: Defense in Depth.
#   1. Telemetrie (_compute_telemetry) -> misst was passiert
#   2. Detection  (_is_output_implausibly_short) -> erkennt den Bug
#   3. Retry      (_retry_without_thinking) -> haerterer 2. Versuch
#
# Wenn auch der Retry scheitert, geht der schlechte Output trotzdem
# zurueck, aber mit degraded=True. Eval-Framework macht daraus dann
# einen Hard-Fail (Schritt 7).
# =============================================================================


def _compute_telemetry(
    raw_text: str,
    eval_count: int,
    max_tokens: int,
    used_thinking_fallback: bool = False,
) -> dict:
    """
    Misst Indikatoren fuer Think-Block-Dominanz im LLM-Output.

    Wird DIREKT auf dem Rohtext aus Ollama berechnet (vor Postprocessing),
    damit <think>-Anteile noch sichtbar sind.

    Returns:
        dict mit Feldern:
          raw_length, think_length, think_ratio,
          had_orphan_think_open, had_orphan_think_close,
          tokens_hit_cap, used_thinking_fallback, eval_count
    """
    raw_length = len(raw_text)
    has_open  = "<think>" in raw_text
    has_close = "</think>" in raw_text
    think_length = 0
    if has_close:
        # Inhalt vor dem ersten </think> = Think-Block
        think_part = raw_text.split("</think>", 1)[0]
        if has_open:
            think_part = think_part.split("<think>", 1)[-1]
        think_length = len(think_part)
    elif has_open:
        # <think> ohne </think> = abgebrochener Block, alles dahinter ist Think
        think_part = raw_text.split("<think>", 1)[-1]
        think_length = len(think_part)

    return {
        "raw_length":             raw_length,
        "think_length":           think_length,
        "think_ratio":            round(think_length / raw_length, 3) if raw_length else 0.0,
        "had_orphan_think_open":  has_open and not has_close,
        "had_orphan_think_close": has_close and not has_open,
        "tokens_hit_cap":         eval_count >= max(0, max_tokens - 50),  # 50-Token-Toleranz
        "used_thinking_fallback": used_thinking_fallback,
        "eval_count":             eval_count,
    }


# Untergrenzen pro Workflow fuer plausible Output-Laenge (Wortzahl).
# Werte sind grob 50% der min_words aus MIN_OUTPUT_TOKENS in generate_text():
# unter dieser Schwelle ist eine legitime Generierung unwahrscheinlich.
_MIN_PLAUSIBLE_WORDS = {
    "entlassbericht":     300,
    "verlaengerung":      200,
    "folgeverlaengerung": 200,
    "akutantrag":         100,
    "anamnese":           175,
    "befund":             80,
    "dokumentation":      125,
}


def _is_output_implausibly_short(
    workflow: Optional[str],
    final_text: str,
    telemetry: dict,
) -> tuple[bool, str]:
    """
    Pruef ob der finale Output (nach Postprocessing) eindeutig zu kurz
    ist um eine legitime Generierung zu sein UND Think-Block-Indikatoren
    vorhanden sind.

    Beide Bedingungen muessen erfuellt sein - kurze Outputs ohne
    Think-Indikatoren sind moeglicherweise legitim (z.B. sehr kurze
    Aufnahme) und werden NICHT als degradiert klassifiziert.

    Returns:
        (is_too_short, reason). reason ist leer wenn alles ok.
    """
    if not final_text:
        return True, "Output komplett leer nach Postprocessing"

    word_count = len(final_text.split())
    threshold = _MIN_PLAUSIBLE_WORDS.get(workflow or "", 100)

    if word_count < threshold:
        suspicious = (
            telemetry.get("think_ratio", 0) > 0.3
            or telemetry.get("tokens_hit_cap", False)
            or telemetry.get("had_orphan_think_open", False)
            or telemetry.get("had_orphan_think_close", False)
            or telemetry.get("used_thinking_fallback", False)
        )
        if suspicious:
            return True, (
                f"Output zu kurz ({word_count}w < {threshold}w threshold) "
                f"und Think-Block-Indikatoren: ratio={telemetry.get('think_ratio'):.0%}, "
                f"tokens_cap={telemetry.get('tokens_hit_cap')}, "
                f"thinking_fallback={telemetry.get('used_thinking_fallback')}"
            )

    return False, ""


def _postprocess_text(
    text: str,
    assistant_primer: str,
    workflow: Optional[str],
    max_words: Optional[int],
    expected_keywords: Optional[list[str]],
    *,
    strict_dedup: bool = False,
) -> str:
    """
    Wendet den gesamten Postprocessing-Pfad auf einen Rohtext an.
    Gemeinsame Implementierung fuer den ersten Pass UND den Retry-Pass
    in generate_text(); verhindert Code-Duplikation.

    Args:
        strict_dedup: Wenn True, nutzt deduplicate_paragraphs den strict_mode
                      (nur byte-identische Duplikate entfernen). Fuer Stage-1.
    """
    import re as _re

    # Primer wieder voranstellen (war Teil des Outputs, vom LLM nicht
    # nochmal mitgeneriert)
    if assistant_primer and text:
        text = assistant_primer + text

    # Qwen3 Think-Bloecke entfernen - alle Varianten
    if "<think>" in text or "</think>" in text:
        text = _re.sub(r"<think>.*?</think>\s*", "", text, flags=_re.DOTALL)
        text = _re.sub(r"^.*?</think>\s*", "", text, flags=_re.DOTALL)
        text = _re.sub(r"<think>.*$", "", text, flags=_re.DOTALL)
        logger.info("Qwen3 Think-Block aus Output entfernt")

    # Doppelten Text erkennen (Qwen3 generiert manchmal zweimal)
    primer_phrases = [
        "stellt sich vor", "stellt sich zur", "Vorstellungsanlass",
        "Zu Beginn des stationären", "Im bisherigen Verlauf",
    ]
    for phrase in primer_phrases:
        parts = text.split(phrase)
        if len(parts) >= 3:
            second_start = text.index(phrase, text.index(phrase) + 1)
            text = text[second_start:]
            logger.warning("Doppelten Text erkannt und bereinigt (Phrase: '%s')", phrase)
            break

    text = strip_markdown_formatting(deduplicate_paragraphs(text, strict_mode=strict_dedup))

    # v16: Komposita, Loop-Repetition, Hard-Cap, Keyword-Check
    if text:
        try:
            from app.services.postprocessing import postprocess_output
            text = postprocess_output(
                text,
                workflow=workflow,
                max_words=max_words,
                expected_keywords=expected_keywords,
            )
        except ImportError:
            logger.debug("postprocessing-Modul nicht verfuegbar - v16-Cleanups uebersprungen")
        except Exception as e:
            logger.warning("Postprocessing-Fehler (Output bleibt unveraendert): %s", e)

    return text


async def _retry_without_thinking(
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    model: Optional[str],
    assistant_primer: str,
    original_telemetry: dict,
) -> dict:
    """
    Zweiter Versuch wenn der erste durch Think-Block scheiterte.

    Haertere Anti-Think-Massnahmen:
      1. /no_think am ANFANG UND Ende des user_content (doppelt)
      2. System-Prompt-Append: explizite Anti-Think-Anweisung
      3. Leicht erhoehte Temperature (+0.2, max 0.6) bricht
         deterministische Reasoning-Loops
      4. Maximal EIN Retry - kein endloser Loop

    Returns dict mit text, model_used, token_count, telemetry, retry_used=True.
    """
    effective_model = model or settings.OLLAMA_MODEL

    # /no_think doppelt - bestehende /no_think am Ende erst entfernen,
    # dann am Anfang UND Ende neu setzen
    hard_no_think = (
        "/no_think\n\n"
        + user_content.rstrip().removesuffix("/no_think").rstrip()
        + "\n\n/no_think"
    )

    # Strikter System-Prompt-Append
    hard_system = system_prompt + (
        "\n\nWICHTIG: KEIN INNERES NACHDENKEN. "
        "Schreibe direkt den finalen Text. "
        "KEINE <think>-Tags, KEINE Meta-Reflexion, KEINE Vorbemerkungen. "
        "Beginne sofort mit dem Bericht-Text."
    )

    profile = _get_model_profile(effective_model)
    num_ctx = _estimate_num_ctx(hard_system, hard_no_think, max_tokens)
    num_ctx = max(num_ctx, profile.get("min_ctx", 2048))

    payload = {
        "model":      effective_model,
        "stream":     False,
        "think":      False,
        "keep_alive": -1,
        "options": {
            "num_predict": max_tokens,
            "num_ctx":     num_ctx,
            "temperature": min(0.6, profile["temperature"] + 0.2),  # leicht hoeher
            "top_p":       profile["top_p"],
        },
        "messages": [
            {"role": "system",    "content": hard_system},
            {"role": "user",      "content": hard_no_think},
            *([{"role": "assistant", "content": assistant_primer}] if assistant_primer else []),
        ],
    }

    logger.warning(
        "Retry ohne Thinking gestartet (Original: think_ratio=%.0f%%, "
        "tokens_cap=%s, thinking_fallback=%s)",
        original_telemetry.get("think_ratio", 0) * 100,
        original_telemetry.get("tokens_hit_cap"),
        original_telemetry.get("used_thinking_fallback"),
    )

    client = _get_ollama_client()
    try:
        r = await client.post("/api/chat", json=payload)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        # Retry-Fehler: keine zweite Eskalation, einfach leeres Result
        # zurueckgeben. Aufrufer (generate_text) markiert degradiert.
        logger.error("Retry-Call fehlgeschlagen (HTTP %s): %s", e.response.status_code, e.response.text[:200])
        return {"text": "", "model_used": f"ollama/{effective_model}",
                "token_count": 0, "telemetry": original_telemetry,
                "retry_used": True, "retry_failed": True}
    except Exception as e:
        logger.error("Retry-Call fehlgeschlagen (%s)", e)
        return {"text": "", "model_used": f"ollama/{effective_model}",
                "token_count": 0, "telemetry": original_telemetry,
                "retry_used": True, "retry_failed": True}

    data = r.json()
    msg = data.get("message", {})
    text = (msg.get("content", "") or data.get("response", "") or "").strip()
    used_thinking_fallback = False
    if not text and msg.get("thinking"):
        text = msg["thinking"].strip()
        used_thinking_fallback = True

    eval_count = data.get("eval_count") or 0
    telemetry = _compute_telemetry(
        raw_text=text,
        eval_count=eval_count,
        max_tokens=max_tokens,
        used_thinking_fallback=used_thinking_fallback,
    )

    return {
        "text":        text,
        "model_used":  f"ollama/{effective_model}",
        "token_count": eval_count,
        "telemetry":   telemetry,
        "retry_used":  True,
    }


async def _generate_ollama(
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    model: Optional[str] = None,
    assistant_primer: str = "",
    temperature_override: Optional[float] = None,
) -> dict:
    """
    Ollama REST API (lokal, kein externer Aufruf).

    assistant_primer: Optionaler Einstiegstext der als Assistant-Nachricht
    vorangestellt wird. Zwingt das Modell den Text direkt fortzusetzen
    statt sich zu weigern oder Erklaerungen voranzustellen.

    temperature_override: Wenn gesetzt, ueberschreibt die Modell-Profil-
    Temperatur. Wird von Stage-1 (verlauf_summary) genutzt fuer maximal
    quellentreue Verdichtung (0.2, beim Retry 0.1).
    """
    effective_model = model or settings.OLLAMA_MODEL

    profile = _get_model_profile(effective_model)
    effective_temperature = (
        temperature_override
        if temperature_override is not None
        else profile["temperature"]
    )
    num_ctx = _estimate_num_ctx(system_prompt, user_content, max_tokens)
    # Für Reasoning-Modelle: mindestens deren Profil-Minimum respektieren
    num_ctx = max(num_ctx, profile.get("min_ctx", 2048))
    logger.debug(
        "Modell '%s': num_ctx=%d (dynamisch berechnet, min_ctx: %d), temperature=%.2f%s",
        effective_model, num_ctx, profile.get("min_ctx", 2048),
        effective_temperature,
        " [override]" if temperature_override is not None else "",
    )

    async def _call(num_ctx: int) -> httpx.Response:
        payload = {
            "model":      effective_model,
            "stream":     False,
            "think":      False,
            "keep_alive": -1,
            "options": {
                "num_predict": max_tokens,
                "num_ctx":     num_ctx,
                "temperature": effective_temperature,
                "top_p":       profile["top_p"],
            },
            "messages": [
                {"role": "system",    "content": system_prompt},
                {"role": "user",      "content": user_content},
                # Assistant-Primer: nur wenn nicht-leer (leerer Primer kann bei
                # Qwen3 dazu fuehren dass Thinking alle Tokens verbraucht)
                *([{"role": "assistant", "content": assistant_primer}] if assistant_primer else []),
            ],
        }
        client = _get_ollama_client()
        try:
            r = await client.post("/api/chat", json=payload)
            r.raise_for_status()
            return r
        except httpx.ConnectError:
            raise RuntimeError(
                f"Ollama nicht erreichbar unter {settings.OLLAMA_HOST}. "
                "Bitte sicherstellen, dass Ollama laeuft."
            )
        except httpx.HTTPStatusError as e:
            body = e.response.text
            raise _classify_ollama_error(e.response.status_code, body)

    # Versuch 1: Normal mit dynamisch berechnetem Kontext
    try:
        r = await _call(num_ctx=num_ctx)
    except Exception as e:
        if not _is_vram_error(e):
            raise
        logger.warning(
            "Ollama VRAM-OOM – entlade Modell und versuche erneut "
            "mit reduziertem Kontext (8192 tokens). Fehler: %s", e
        )
        await _ollama_unload(effective_model)
        # Versuch 2: reduzierter Kontext
        try:
            r = await _call(num_ctx=8192)
            logger.info("Ollama-Generierung erfolgreich mit reduziertem Kontext (8192)")
        except Exception as e2:
            if not _is_vram_error(e2):
                raise
            raise RuntimeError(
                "Ollama: Nicht genug VRAM auch mit reduziertem Kontext (8192 tokens). "
                "Bitte WHISPER_FREE_OLLAMA_VRAM=true in .env setzen "
                "oder das Gespraech kuerzen."
            ) from e2

    data = r.json()
    # /api/chat gibt message.content zurück (statt response bei /api/generate)
    msg = data.get("message", {})
    text = (
        msg.get("content", "")
        or data.get("response", "")  # Fallback fuer aeltere Versionen
    ).strip()
    # Qwen3 Thinking-Fallback: wenn content leer aber thinking gefuellt,
    # hat das Modell alle Tokens im Thinking verbraucht.
    used_thinking_fallback = False
    if not text and msg.get("thinking"):
        logger.warning(
            "Ollama content leer, verwende thinking-Feld als Fallback (%d Zeichen)",
            len(msg["thinking"]),
        )
        text = msg["thinking"].strip()
        used_thinking_fallback = True

    # v19.1: Telemetrie fuer Think-Block-Diagnose.
    # Wird in generate_text() ausgewertet (Detection + ggf. Retry) und
    # spaeter ueber job_queue/jobs an die DB und das Performance-Log
    # weitergereicht.
    eval_count = data.get("eval_count") or 0
    telemetry = _compute_telemetry(
        raw_text=text,
        eval_count=eval_count,
        max_tokens=max_tokens,
        used_thinking_fallback=used_thinking_fallback,
    )
    if telemetry["think_ratio"] > 0.5 or telemetry["tokens_hit_cap"]:
        logger.warning(
            "Think-Block-Verdacht: think_ratio=%.0f%%, tokens_hit_cap=%s, "
            "raw=%d think_chars=%d thinking_fallback=%s (Modell=%s)",
            telemetry["think_ratio"] * 100, telemetry["tokens_hit_cap"],
            telemetry["raw_length"], telemetry["think_length"],
            telemetry["used_thinking_fallback"], effective_model,
        )

    return {
        "text": text,
        "model_used": f"ollama/{effective_model}",
        "token_count": data.get("eval_count"),
        "telemetry": telemetry,
    }
