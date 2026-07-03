"""
Output-Postprocessing fuer LLM-Generierungen (v16).

Hier landen alle Bereinigungs-Schritte, die NACH der LLM-Generierung
angewendet werden – als robuste Verteidigungslinie gegen
Modell-Quirks die per Prompt-Engineering nicht zuverlaessig
abzufangen sind.

Anwendung in llm.py::generate_text:
    from app.services.postprocessing import postprocess_output
    result["text"] = postprocess_output(result["text"], workflow=workflow)

Bestehende Funktionen in llm.py bleiben unveraendert
(deduplicate_paragraphs, think-Block-Removal etc.) – dieses Modul
ergaenzt sie um drei neue Schritte:

1. fix_kompositum_klebebugs: Qwen3-Tokenizer-Quirk reparieren
2. detect_loop_repetition: wiederholten Block am Ende abschneiden
3. enforce_keyword_presence: warnen wenn wichtige Source-Keywords fehlen
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── 1. Klebebug-Reparatur (Bug 5 / Bug 10 v16) ────────────────────────────────

# Bekannte Komposita-Klebebugs aus Qwen3-Outputs (v12-v15).
# Format: (regex_pattern, replacement). Pattern muss exakt matchen,
# um keine False-Positives zu erzeugen. Alle Pattern hier sind reine
# Wortgrenze-zu-Wortgrenze-Substitutionen ohne Kontext.
#
# Wenn neue Klebebugs auftauchen: hier ergaenzen und Test in
# tests/test_postprocessing.py schreiben.
_KOMPOSITUM_KLEBEBUGS: list[tuple[re.Pattern, str]] = [
    # "Schweresowie" -> "Schwere sowie"
    (re.compile(r"\bSchweresowie\b"), "Schwere sowie"),
    # "Aufenthaltszeigte" -> "Aufenthaltes zeigte"
    (re.compile(r"\bAufenthaltszeigte\b"), "Aufenthaltes zeigte"),
    # "Verlängerungsantraghat" / "Verlaengerungsantraghat"
    (re.compile(r"\bVerlängerungsantraghat\b"), "Verlängerungsantrag hat"),
    (re.compile(r"\bVerlaengerungsantraghat\b"), "Verlaengerungsantrag hat"),
    # Weitere Komposita-Klebebugs die im Log auftauchten:
    (re.compile(r"\bAufnahmegespraechszeigte\b"), "Aufnahmegespraechs zeigte"),
    (re.compile(r"\bBehandlungsverlaufzeigte\b"), "Behandlungsverlauf zeigte"),
    (re.compile(r"\bBerichtszeitraumzeigte\b"), "Berichtszeitraum zeigte"),
]

# Zusaetzliche generische Heuristik fuer "Wort-Genitiv-S + Verb"-Klebebugs:
# erkennt Muster wie "Aufenthaltszeigte" generisch via Suffix-Liste.
# Wird nur angewendet wenn die spezifische Liste oben nicht gegriffen hat.
_VERB_SUFFIXES = (
    "zeigte", "zeigen", "gelang", "gelangen", "machte",
    "hatte", "hatten", "bestand", "bestanden",
    "erfolgte", "erfolgten", "kam", "kamen",
)
# Substantiv-Stammwoerter die haeufig betroffen sind (Genitiv-S am Ende)
_NOUN_STEMS_GENITIVE_S = (
    "Aufenthalt", "Behandlung", "Verlauf", "Antrag",
    "Termin", "Gespraech", "Gespräch", "Bericht",
    "Verlängerungsantrag", "Verlaengerungsantrag",
)


def fix_kompositum_klebebugs(text: str) -> str:
    """
    Repariert Komposita-Klebebugs: Faelle wo das Modell zwei deutsche
    Woerter ohne Leerzeichen zusammengeklebt hat (z.B. "Schweresowie"
    statt "Schwere sowie", "Aufenthaltszeigte" statt "Aufenthaltes zeigte").

    Tritt auf weil der Qwen3-BPE-Tokenizer fuer manche deutschen
    Komposita Tokens hat die ueber Wortgrenzen reichen.

    Wendet zuerst die spezifische Pattern-Liste an, dann generische
    Heuristik fuer "Substantiv+Verb"-Klebung.
    """
    if not text:
        return text

    fixed_count = 0
    fixed = text

    # 1. Spezifische Pattern (sicher, keine False-Positives)
    for pattern, replacement in _KOMPOSITUM_KLEBEBUGS:
        new_fixed, n = pattern.subn(replacement, fixed)
        if n > 0:
            fixed = new_fixed
            fixed_count += n

    # 2. Generische Heuristik: "Substantiv+Genitiv-s+Verb"
    # Pattern: <Stammwort>s<Verb> -> <Stammwort>es <Verb>  (oder s<Verb>)
    # Beispiel: "Verlaufszeigte" -> "Verlaufs zeigte"
    for stem in _NOUN_STEMS_GENITIVE_S:
        for verb in _VERB_SUFFIXES:
            # "Aufenthaltszeigte" -> "Aufenthaltes zeigte" oder "Aufenthalts zeigte"
            # Wir nehmen "<stem>s <verb>" als Default (Genitiv-s).
            pattern = re.compile(rf"\b{stem}s{verb}\b")
            replacement = f"{stem}s {verb}"
            new_fixed, n = pattern.subn(replacement, fixed)
            if n > 0:
                fixed = new_fixed
                fixed_count += n

    if fixed_count > 0:
        logger.warning(
            "Postprocessing: %d Komposita-Klebebugs repariert (Qwen3-Tokenizer-Quirk)",
            fixed_count,
        )
    return fixed


# ── 1b. Zentraler deutscher Satz-Splitter ─────────────────────────────────────
#
# Single Source of Truth fuer Satz-Splitting (R1-Refactoring).
# Hintergrund (Issue 1, prompts.log 2026-06-30): das naive
# re.split(r"(?<=[.!?])\s+") behandelt Abkuerzungspunkte (z.B., u.a., d.h.)
# und Namens-Initialen (Herr Z.) als Satzenden. Folge im Hard-Cap: Kuerzung
# landet mitten in der Abkuerzung ("... wie z.B."), in der Loop-Detection:
# falsche Satzbloecke.
#
# Merge-Heuristik ist bewusst grosszuegig ("im Zweifel KEIN Satzende"):
# fuer Hard-Cap und Loop-Detection ist Uebermergen sicher (es wird hoechstens
# an einer spaeteren, echten Grenze geschnitten), Untermergen ist der Bug.

# Abkuerzungen (lowercase, mit Schlusspunkt), nach denen KEIN Satzende folgt.
_DE_ABBREVIATIONS = frozenset({
    # mehrteilig (ohne Binnen-Leerzeichen geschrieben)
    "z.b.", "u.a.", "d.h.", "o.ä.", "o.a.", "u.ä.", "u.u.", "i.d.r.",
    "u.v.m.", "s.o.", "s.u.", "v.a.", "z.t.", "o.g.", "u.g.", "i.s.",
    # einteilig
    "ggf.", "bzw.", "bspw.", "etc.", "evtl.", "inkl.", "exkl.", "ca.",
    "vgl.", "sog.", "usw.", "max.", "min.", "mind.", "tel.", "nr.",
    "abs.", "kap.", "bd.", "str.", "geb.", "verh.", "led.", "gesch.",
    # Titel/Anreden
    "dr.", "prof.", "dipl.", "med.", "psych.", "hr.", "fr.",
})

# Einzelbuchstabe + Punkt: Namens-Initial ("Herr Z.") oder Teil einer
# mit Leerzeichen gesetzten Abkuerzung ("z. B." -> Segmente enden "z." / "B.").
_SINGLE_LETTER_DOT_RE = re.compile(r"^[A-Za-zÄÖÜäöü]\.$")

# Ordinalzahl 1-3-stellig ("am 3. Mai", "Kap. 12."). Bewusst NICHT 4-stellig,
# damit Jahreszahlen am Satzende ("... seit 2013.") weiter als Ende gelten.
_ORDINAL_DOT_RE = re.compile(r"^\d{1,3}\.$")

# Folgesegment beginnt kleingeschrieben -> kann kein Satzanfang sein.
_LOWERCASE_START_RE = re.compile(r"^[a-zäöüß]")


def split_sentences_de(text: str) -> list[str]:
    """Splittet deutschen Text in Saetze, abkuerzungs- und initialenfest.

    Verwendet von hard_cap_word_count() und detect_loop_repetition().
    (Die Stilanalyse in prompts.py nutzt noch ihre eigene Variante –
    Umstellung dort ist Generierungs-Baseline und laeuft mit der
    Issue-2/Prompt-Revision, nicht hier.)
    """
    if not text:
        return []

    raw_segments = re.split(r"(?<=[.!?])\s+", text)
    merged: list[str] = []
    for seg in raw_segments:
        if merged:
            prev = merged[-1].rstrip()
            prev_tokens = prev.split()
            last_token = prev_tokens[-1] if prev_tokens else ""
            if (
                last_token.lower() in _DE_ABBREVIATIONS
                or _SINGLE_LETTER_DOT_RE.match(last_token)
                or _ORDINAL_DOT_RE.match(last_token)
                or _LOWERCASE_START_RE.match(seg)
            ):
                merged[-1] = merged[-1] + " " + seg
                continue
        merged.append(seg)
    return merged


# ── 2. Loop-Repetition-Detector (Bug 11 v16) ───────────────────────────────────

# Mindestlaenge eines wiederholten Blocks der als Loop gilt (Zeichen).
# Kuerzere Wiederholungen koennen legitime Wiederholungen sein
# (z.B. "Wir nehmen Frau M. auf. Frau M. zeigt sich..." -> Frau M. okay).
_MIN_LOOP_BLOCK_CHARS = 200

# Mindestueberlappung in Zeichen zwischen Block am Ende und seinem
# vorherigen Vorkommen, damit es als Loop gilt.
_MIN_LOOP_OVERLAP = 150


def detect_loop_repetition(text: str) -> str:
    """
    Erkennt LLM-Loop-Hallucinationen: wenn ein zusammenhaengender Block
    am Output-Ende eine fast-woertliche Wiederholung von schon zuvor
    geschriebenem Text ist.

    Beispiel aus Eval an-02-schulangst (v15):
        ... [normaler Anamnese-Text] ...
        Sie hat Erfahrungen mit Einzelgespraechen und Gruppenangeboten,
        was sie als hilfreich empfindet. Sie lebt noch zu Hause...
        [WIEDERHOLT 1:1 am Ende:]
        Sie hat Erfahrungen mit Einzelgespraechen und Gruppenangeboten,
        was sie als hilfreich empfindet. Sie lebt noch zu Hause...

    Strategie:
      - Text in Saetze splitten.
      - Letzte N Saetze (N=3,4,5,...) als Suchblock nehmen.
      - Pruefen ob dieser Block (normalisiert) bereits FRUEHER im Text steht.
      - Bei Treffer: alles ab der ersten Wiederholung am Ende abschneiden.

    Greift sowohl bei Absatz-grossen Loops (mehrere Saetze)
    als auch bei kleineren wenn sie deutlich genug wiederholen.

    Ergaenzt deduplicate_paragraphs (das matcht nur exakte Absatz-Duplikate).
    """
    if not text or len(text) < 2 * _MIN_LOOP_BLOCK_CHARS:
        return text

    # Saetze splitten (abkuerzungsfest, siehe split_sentences_de)
    sentences = split_sentences_de(text.strip())
    if len(sentences) < 6:
        # Zu wenige Saetze fuer Loop-Erkennung
        return text

    # Versuche verschiedene Block-Groessen am Ende: 6, 5, 4, 3 Saetze
    # Groesster Block zuerst (am sichersten), kleinerer als Fallback.
    for n_sentences in (6, 5, 4, 3):
        if len(sentences) < n_sentences * 2:
            continue

        last_block = " ".join(sentences[-n_sentences:])
        if len(last_block) < _MIN_LOOP_BLOCK_CHARS:
            continue

        # Vergleichs-Snippet (normalisiert)
        snippet = last_block[: _MIN_LOOP_OVERLAP]
        snippet_norm = " ".join(snippet.lower().split())

        # Suche im vorherigen Text (alles vor diesen N Saetzen)
        prefix = " ".join(sentences[:-n_sentences])
        prefix_norm = " ".join(prefix.lower().split())

        if snippet_norm in prefix_norm:
            # Loop erkannt - alles ab dem letzten Block abschneiden
            # Wir suchen wo last_block im Original beginnt
            # (mit Toleranz fuer Whitespace-Unterschiede)
            cut_at = text.rfind(sentences[-n_sentences])
            if cut_at > 0:
                cleaned = text[:cut_at].rstrip()
                # Trailing punctuation sicherstellen
                if cleaned and cleaned[-1] not in ".!?":
                    cleaned += "."
                logger.warning(
                    "Postprocessing: Loop-Repetition am Output-Ende erkannt "
                    "(%d Saetze / %d Zeichen abgeschnitten)",
                    n_sentences, len(text) - len(cleaned),
                )
                return cleaned

    return text


# ── 3. Keyword-Presence-Check (Bug 14 v16) ─────────────────────────────────────

def detect_missing_keywords(
    text: str,
    expected_keywords: list[str],
    *,
    case_insensitive: bool = True,
) -> list[str]:
    """
    Prueft ob bestimmte Keywords im Output vorkommen. Wird verwendet um
    LLM-Outputs zu validieren die patientenspezifische Begriffe (Diagnosen,
    Ereignisse) NICHT verlieren duerfen.

    Beispiel aus Eval eb-02-ads-trennung:
        Quelldokument enthaelt: "Trennung", "ADS", "Methylphenidat"
        Output (v15): erwaehnt nur "Beziehungserfahrungen", "familiaere Dynamik"
        => "Trennung" fehlt!

    Returns:
        Liste der fehlenden Keywords. Leere Liste = alle vorhanden.

    Anwendung in llm.py::generate_text NACH der Generierung:
        missing = detect_missing_keywords(result["text"], required_keywords)
        if missing:
            logger.warning("Output verloren: %s", missing)
            # optional: zweiter LLM-Pass mit explizitem Keyword-Hinweis
    """
    if not text or not expected_keywords:
        return []

    text_check = text.lower() if case_insensitive else text
    missing = []
    for kw in expected_keywords:
        kw_check = kw.lower() if case_insensitive else kw
        if kw_check not in text_check:
            missing.append(kw)
    return missing


def extract_likely_keywords(source_text: str, *, max_keywords: int = 10) -> list[str]:
    """
    Extrahiert wahrscheinlich-wichtige Keywords aus einem Quelltext
    (Verlaufsdoku, Anamnese etc.). Heuristik:

    - Substantive die >2x vorkommen
    - ICD-Codes (F33.1, F43.1, etc.)
    - Bestimmte semantisch-wichtige Woerter (Liste unten)

    Wird in jobs.py vor dem LLM-Call aufgerufen, an llm.generate_text
    durchgereicht und dort nach der Generierung geprueft.
    """
    if not source_text:
        return []

    # Always-relevant Begriffe wenn sie im Source vorkommen
    SEMANTIC_KEYWORDS = (
        "Trennung", "Scheidung", "Mobbing", "Tod", "Todesfall",
        "Suizid", "Suizidalitaet", "Suizidalität", "Selbstverletzung",
        "Trauma", "Vergewaltigung", "Missbrauch",
        "Geburt", "Schwangerschaft", "Fehlgeburt",
        "ADS", "ADHS", "ADHD", "Autismus",
        "Methylphenidat", "Lithium", "Quetiapin",
        "EMDR", "Anteilearbeit", "IFS",
    )

    found = set()
    for kw in SEMANTIC_KEYWORDS:
        if kw.lower() in source_text.lower():
            found.add(kw)

    # ICD-Codes
    icd_pattern = re.compile(r"\b(F\d{2}\.\d+|Z\d{2}\.\d+)\b")
    for m in icd_pattern.findall(source_text):
        found.add(m)

    return sorted(found)[:max_keywords]


# ── 4. Hard-Cap fuer Output-Laenge (Bug 13 v16) ───────────────────────────────

# Ab welchem Faktor ueber max_words der Hard-Cap ueberhaupt eingreift.
#
# Entscheidung 2026-07-01 (Nutzerfeedback): laengere Texte sind fuer die
# Therapeut*innen KEIN Problem ("etwas rausloeschen ist schnell gemacht"),
# abgeschnittener Output ist IMMER unbefriedigend - insbesondere weil der
# Schnitt am Textende die Pflichtsektion 'Einladungen' opfert. Der Cap ist
# deshalb keine Stil-Disziplin mehr, sondern nur noch Notbremse gegen
# pathologische Ueberlaenge (Degeneration). Gegen Wiederholungs-Loops
# schuetzen unabhaengig davon detect_loop_repetition() und
# deduplicate_paragraphs() (laufen VOR dem Cap), gegen Token-Amoklauf
# max_tokens auf API-Ebene.
HARD_CAP_RUNAWAY_FACTOR = 2.0


def hard_cap_word_count(text: str, max_words: int) -> str:
    """
    Schneidet den Output an einer Satzgrenze ab, wenn er max_words
    ueberschreitet. Nur als harte Verteidigungslinie gegen Faelle wo
    das Prompt-Engineering das Limit ignoriert hat.

    Beispiel: dok-02-paarthematik (v15)
        Stilvorlage: 110 Woerter Stichwort-Stil
        Limit:       143 Woerter
        Output:      323 Woerter (Faktor 2.3)
    => abschneiden auf 143 Woerter, an Satzgrenze.

    Strategie (seit 2026-07-01, siehe HARD_CAP_RUNAWAY_FACTOR):
      - Wenn word_count <= max_words * HARD_CAP_RUNAWAY_FACTOR: nichts tun
        (auch deutlich uebers Limit hinaus - Ueberlaenge ist akzeptiert,
        nur Degeneration wird gekappt)
      - Sonst: Saetze sammeln bis max_words erreicht, am letzten ganzen
        Satz abschneiden (abkuerzungsfest via split_sentences_de)
      - Logging als Warnung
    """
    if not text or max_words <= 0:
        return text

    words = text.split()
    if len(words) <= int(max_words * HARD_CAP_RUNAWAY_FACTOR):
        return text

    # Saetze finden und auswaehlen bis Limit erreicht (abkuerzungsfest,
    # Issue 1: naiver Split schnitt nach "z.B." ab und warf den Rest weg)
    sentences = split_sentences_de(text)
    accumulated_words = 0
    accumulated_sentences = []
    for s in sentences:
        s_words = len(s.split())
        if accumulated_words + s_words > max_words:
            break
        accumulated_sentences.append(s)
        accumulated_words += s_words

    if not accumulated_sentences:
        # Selbst der erste Satz ist schon zu lang - nicht abschneiden
        return text

    capped = " ".join(accumulated_sentences)
    logger.warning(
        "Postprocessing: Output gekuerzt von %d auf %d Woerter "
        "(harte Obergrenze, max=%d)",
        len(words), accumulated_words, max_words,
    )
    return capped


# ── 5. Master-Postprocessor ───────────────────────────────────────────────────

def postprocess_output(
    text: str,
    *,
    workflow: Optional[str] = None,
    max_words: Optional[int] = None,
    expected_keywords: Optional[list[str]] = None,
) -> str:
    """
    Wendet alle Postprocessing-Schritte in der richtigen Reihenfolge an.

    Reihenfolge ist wichtig:
      1. Klebebug-Fix (vor anderen Schritten - sonst werden gekuerzte
         Outputs mit Klebebugs ausgeliefert)
      2. Loop-Repetition-Detection (entfernt redundanten Text am Ende)
      3. Hard-Cap (nur falls max_words gesetzt; nach Loop-Detection
         damit nicht im Loop-Bereich abgeschnitten wird)
      4. Keyword-Check (nur Logging, keine Modifikation)

    Args:
        text:              LLM-Output
        workflow:          Workflow-Name (fuer kontextbezogenes Logging)
        max_words:         Optionale harte Obergrenze (vom Caller berechnet)
        expected_keywords: Keywords die im Output vorhanden sein muessten

    Returns:
        Bereinigter Text. Bei missing keywords: Original-Text mit Warnung im Log.
    """
    if not text:
        return text

    original_len = len(text)

    # 1. Klebebugs reparieren
    text = fix_kompositum_klebebugs(text)

    # 2. Loop-Detection
    text = detect_loop_repetition(text)

    # 3. Hard-Cap (nur wenn max_words gesetzt)
    if max_words is not None and max_words > 0:
        text = hard_cap_word_count(text, max_words)

    # 4. Keyword-Check (nur Logging)
    if expected_keywords:
        missing = detect_missing_keywords(text, expected_keywords)
        if missing:
            logger.warning(
                "Workflow=%s: Output erwaehnt %d/%d erwartete Keywords nicht: %s",
                workflow or "?", len(missing), len(expected_keywords), missing,
            )

    if len(text) != original_len:
        logger.info(
            "Postprocessing total: %d -> %d Zeichen (Workflow=%s)",
            original_len, len(text), workflow or "?",
        )

    return text
