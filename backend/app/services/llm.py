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


def deduplicate_paragraphs(text: str) -> str:
    """
    Entfernt wiederholte Absätze aus dem LLM-Output.
    qwen3:32b neigt bei zu langem Kontext dazu denselben Absatz
    mehrfach zu generieren – dieser Filter erkennt und entfernt Duplikate.
    Überschriften (**Fett** oder Zeile mit nur Grossbuchstaben) bleiben erhalten.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    seen: set[str] = set()
    result = []
    duplicates = 0
    for p in paragraphs:
        # Normalisierter Vergleichsschlüssel: Leerzeichen zusammenfassen, lowercase
        key = " ".join(p.lower().split())
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        result.append(p)
    if duplicates:
        logger.warning(
            "LLM-Output: %d doppelte Absätze entfernt (Wiederholungsloop erkannt)",
            duplicates,
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


def clean_verlauf_text(text: str) -> str:
    """
    Bereinigt extrahierten Verlaufsdokumentationstext vor dem LLM-Aufruf:
    1. Entfernt wiederkehrende PDF-Seitenheader (Name, Zimmernummer, Datum, Seite X von Y)
    2. Entfernt reine Teilnahmeeintraege ohne inhaltliche Information
       ("hat teilgenommen", "nicht teilgenommen", "entschuldigt" allein in einem Block)
    3. Entfernt leere Zeitblock-Eintraege (nur Datum + Uhrzeit + Therapiename + Teilnahme)
    4. Entfernt Therapeutennamen-Header (nur Name ohne Inhalt)
    5. Entfernt reine Terminplanungs-Zeilen und Administratives
    6. Komprimiert wiederholte Leerzeilen
    """
    import re

    lines = text.split("\n")
    cleaned = []
    i = 0

    # Pattern fuer PDF-Seitenheader
    header_patterns = [
        re.compile(r"Verlaufsdokumentation\s*[-–]\s*Stand:", re.IGNORECASE),
        re.compile(r"Seite\s+\d+\s+von\s+\d+", re.IGNORECASE),
        re.compile(r"\(A\d+\)\s+Zi\.\s+\d+"),   # (A12345) Zi. 123
        re.compile(r"^[A-ZÄÖÜ][a-zäöüß]+,\s+[A-ZÄÖÜ][a-zäöüß-]+\s+\(A\d+\)"),  # Name (Anummer)
    ]

    # Pattern fuer inhaltsleere Teilnahmezeilen
    participation_only = re.compile(
        r"^(hat teilgenommen|nicht teilgenommen|entschuldigt fehlt|"
        r"hat nicht teilgenommen|teilgenommen|abgebrochen|krankgemeldet"
        r"|unentschuldigt gefehlt|entschuldigt|ausgefallen|fiel aus"
        r"|fand nicht statt|wurde abgesagt|Termin entfaellt"
        r"|Pat\.\s+(hat\s+)?teilgenommen)[\.\s]*$",
        re.IGNORECASE,
    )

    # Pattern fuer reine Zeiteintraege (HH:MM - HH:MM Therapiename)
    time_entry = re.compile(r"^\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}\s+\S.*$")

    # Pattern fuer Datumszeilen allein (DD.MM.YYYY)
    date_only = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")

    # Pattern fuer reine Therapeutennamen-Header
    # z.B. "Dr. Müller" oder "Therapeutin Schmidt:" allein auf einer Zeile
    therapist_header = re.compile(
        r"^(Dr\.\s+|Dipl\.\s*-?\s*Psych\.\s+|M\.?A\.?\s+)?"
        r"[A-ZÄÖÜ][a-zäöüß]+([-\s][A-ZÄÖÜ][a-zäöüß]+)*:?\s*$"
    )

    # Pattern fuer administrative Zeilen ohne therapeutischen Inhalt
    admin_patterns = [
        re.compile(r"^\s*Termin(e|planung)?\s*(am|fuer|:)", re.IGNORECASE),
        re.compile(r"^\s*Raum\s*\d+", re.IGNORECASE),
        re.compile(r"^\s*Tel\.\s*\d+", re.IGNORECASE),
        re.compile(r"^\s*Rezept\s*(ausgestellt|verlängert|erneuert)", re.IGNORECASE),
        re.compile(r"^\s*Ueberweisung\s*(an|ausgestellt)", re.IGNORECASE),
        re.compile(r"^\s*Krankmeldung\s*(bis|ausgestellt|verlängert)", re.IGNORECASE),
        re.compile(r"^\s*AU[-\s]?(Bescheinigung|bis)", re.IGNORECASE),
    ]

    while i < len(lines):
        line = lines[i].strip()

        # Leere Zeilen durchlassen (werden spaeter komprimiert)
        if not line:
            cleaned.append("")
            i += 1
            continue

        # Seitenheader ueberspringen
        if any(p.search(line) for p in header_patterns):
            i += 1
            continue

        # Administrative Zeilen ueberspringen
        if any(p.match(line) for p in admin_patterns):
            i += 1
            continue

        # Reinen Teilnahme-Block erkennen und ueberspringen:
        # Muster: [Datum] / [Zeit - Therapiename] / hat teilgenommen
        if participation_only.match(line):
            # Pruefen ob vorherige Zeilen nur Datum/Zeit waren → rueckwirkend entfernen
            while cleaned and (
                date_only.match(cleaned[-1].strip())
                or time_entry.match(cleaned[-1].strip())
                or cleaned[-1].strip() == ""
            ):
                cleaned.pop()
            i += 1
            continue

        # Reine Zeiteintraege ohne nachfolgenden Inhalt ueberspringen
        # (Zeile mit nur "09:00 - 10:00 Einzeltherapie" gefolgt von Teilnahme oder Leerzeile)
        if time_entry.match(line):
            # Vorausschauen: naechste nicht-leere Zeile
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and participation_only.match(lines[j].strip()):
                # Zeitblock + Teilnahme → beides ueberspringen
                i = j + 1
                continue
            if j >= len(lines) or date_only.match(lines[j].strip()):
                # Zeitblock ohne Inhalt → ueberspringen
                i += 1
                continue

        # Therapeutennamen allein auf einer Zeile → ueberspringen
        # (nur wenn keine inhaltliche Zeile folgt in den naechsten 2 Zeilen)
        if therapist_header.match(line) and len(line) < 40:
            j = i + 1
            while j < len(lines) and not lines[j].strip() and j - i < 3:
                j += 1
            if j < len(lines) and (
                participation_only.match(lines[j].strip())
                or date_only.match(lines[j].strip())
                or time_entry.match(lines[j].strip())
            ):
                i += 1
                continue

        cleaned.append(lines[i])
        i += 1

    result = "\n".join(cleaned)

    # Mehr als 2 aufeinanderfolgende Leerzeilen auf 1 reduzieren
    result = re.sub(r"\n{3,}", "\n\n", result)

    original_len = len(text)
    cleaned_len = len(result)
    if original_len > cleaned_len:
        reduction = int((1 - cleaned_len / original_len) * 100)
        logger.info(
            "Verlaufsdoku bereinigt: %d → %d Zeichen (-%d%% Overhead entfernt)",
            original_len, cleaned_len, reduction,
        )

    return result.strip()


async def generate_text(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 2048,
    model: Optional[str] = None,
    workflow: Optional[str] = None,
    on_progress=None,
    max_words: Optional[int] = None,
    expected_keywords: Optional[list[str]] = None,
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
    """
    if len(user_content) > MAX_USER_CONTENT_CHARS:
        user_content = _sample_uniformly(user_content, MAX_USER_CONTENT_CHARS)

    # Sicherheitscheck: wenn Input + Output > MAX_SAFE_CTX, User-Content kuerzen.
    # VRAM-Budget haengt von der GPU ab:
    #   RTX Pro 4500 (32GB): 32GB - 19GB Modell - 1GB Overhead = ~12GB fuer KV-Cache
    #     → 12GB / ~0.75GB pro 1024 Tokens ≈ 16384 Token sicher
    #   RTX 4090 (24GB):     24GB - 18GB Modell(q4_K_S) - 1GB = ~5GB fuer KV-Cache
    #     → 5GB / ~0.75GB pro 1024 Tokens ≈ 6144 Token sicher
    #   Mit OLLAMA_KV_CACHE_TYPE=q8_0: KV-Cache halbiert → doppelte Kontextlaenge
    #
    # Konservativ: 16384 – passt auf RTX Pro 4500 (32GB) mit FP16 KV-Cache.
    # Fuer RTX 4090 (24GB): auf 8192 reduzieren oder OLLAMA_KV_CACHE_TYPE=q8_0 setzen.
    MAX_SAFE_CTX = 16384
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
    )

    # Primer war Teil des Outputs – wieder voranstellen damit der Text vollständig ist
    if assistant_primer and result.get("text"):
        result["text"] = assistant_primer + result["text"]

    # Output-Postprocessing
    if result.get("text"):
        import re as _re
        text = result["text"]

        # Qwen3 Think-Blöcke entfernen – alle Varianten:
        # 1. Vollstaendige <think>...</think> Blöcke
        # 2. Orphan </think> ohne oeffnendes Tag (Primer hat <think> abgeschnitten)
        # 3. Orphan <think> ohne schliessendes Tag (Output abgebrochen)
        if "<think>" in text or "</think>" in text:
            # Erst vollstaendige Bloecke
            text = _re.sub(r"<think>.*?</think>\s*", "", text, flags=_re.DOTALL)
            # Dann orphan </think> und alles davor bis zum letzten Absatzumbruch
            # (der Think-Content steht vor dem </think>)
            text = _re.sub(r"^.*?</think>\s*", "", text, flags=_re.DOTALL)
            # Orphan <think> am Ende (unvollstaendiger Block)
            text = _re.sub(r"<think>.*$", "", text, flags=_re.DOTALL)
            logger.info("Qwen3 Think-Block aus Output entfernt")

        # Doppelten Text erkennen und entfernen:
        # Manchmal generiert Qwen3 den Text zweimal (vor und nach </think>).
        # Wenn der Text die Anamnese-Primer-Phrase doppelt enthaelt,
        # nur den zweiten (besseren) Teil behalten.
        primer_phrases = [
            "stellt sich vor", "stellt sich zur", "Vorstellungsanlass",
            "Zu Beginn des stationären", "Im bisherigen Verlauf",
        ]
        for phrase in primer_phrases:
            parts = text.split(phrase)
            if len(parts) >= 3:
                # Phrase kommt mindestens 2x vor → Text ist doppelt
                # Zweiten Teil behalten (nach dem zweiten Vorkommen)
                second_start = text.index(phrase, text.index(phrase) + 1)
                text = text[second_start:]
                logger.warning("Doppelten Text erkannt und bereinigt (Phrase: '%s')", phrase)
                break

        result["text"] = strip_markdown_formatting(deduplicate_paragraphs(text))

    # ── v16: Output-Postprocessing ──────────────────────────────────────────
    # Drei Schritte als Verteidigungslinie gegen Modell-Quirks die per
    # Prompt-Engineering nicht zuverlaessig abzufangen sind:
    #   1. Komposita-Klebebugs reparieren ("Aufenthaltszeigte" -> "Aufenthaltes zeigte")
    #   2. Loop-Repetition am Output-Ende erkennen und abschneiden
    #   3. Optional: Hard-Cap auf Wortzahl wenn vom Caller (jobs.py) angegeben
    #   4. Optional: Keyword-Presence-Check (nur Logging)
    if result.get("text"):
        try:
            from app.services.postprocessing import postprocess_output
            result["text"] = postprocess_output(
                result["text"],
                workflow=workflow,
                max_words=max_words,
                expected_keywords=expected_keywords,
            )
        except ImportError:
            logger.debug("postprocessing-Modul nicht verfuegbar - v16-Cleanups uebersprungen")
        except Exception as e:
            logger.warning("Postprocessing-Fehler (Output bleibt unveraendert): %s", e)

    result["duration_s"] = round(time.time() - t0, 1)
    logger.info(
        "Generierung: %d Tokens in %.1fs (Modell: %s)",
        result.get("token_count", 0),
        result["duration_s"],
        result["model_used"],
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


async def _generate_ollama(
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    model: Optional[str] = None,
    assistant_primer: str = "",
) -> dict:
    """
    Ollama REST API (lokal, kein externer Aufruf).

    assistant_primer: Optionaler Einstiegstext der als Assistant-Nachricht
    vorangestellt wird. Zwingt das Modell den Text direkt fortzusetzen
    statt sich zu weigern oder Erklaerungen voranzustellen.
    """
    effective_model = model or settings.OLLAMA_MODEL

    profile = _get_model_profile(effective_model)
    num_ctx = _estimate_num_ctx(system_prompt, user_content, max_tokens)
    # Für Reasoning-Modelle: mindestens deren Profil-Minimum respektieren
    num_ctx = max(num_ctx, profile.get("min_ctx", 2048))
    logger.debug(
        "Modell '%s': num_ctx=%d (dynamisch berechnet, min_ctx: %d)",
        effective_model, num_ctx, profile.get("min_ctx", 2048)
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
                "temperature": profile["temperature"],
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
    if not text and msg.get("thinking"):
        logger.warning(
            "Ollama content leer, verwende thinking-Feld als Fallback (%d Zeichen)",
            len(msg["thinking"]),
        )
        text = msg["thinking"].strip()
    return {
        "text": text,
        "model_used": f"ollama/{effective_model}",
        "token_count": data.get("eval_count"),
    }
