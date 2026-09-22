"""
Verfahrensregister (v19.27).

Ausloeser: Job 89276fb6 (22.09.2026) - der Therapeut gab als Fokus
"IRRT Traumasitzung ..." an, der System-Prompt sagte gleichzeitig "Benenne
KEIN Therapieverfahren namentlich" (neutrales Glossar, weil der Glossar-
Schalter nur Transkript/Unterlagen sah und "IRRT" nicht kannte). Das Modell
folgte der Systemregel, der Fokus wurde ignoriert.

Intention der alten Regel (c.wittenberg): Verfahren aus Glossar/Prompt
tauchten in Dokus auf, obwohl sie im Gespraech nicht vorkamen (Beispiel
Anteilearbeit). Woertlich in Transkript ODER Stichpunkten genannte Verfahren
sollen dagegen benannt und strukturgerecht dokumentiert werden.

Loesung: statt eines binaeren Schalters (volles Glossar / Verbot) ein
Register. Pro Verfahren: Erkennungsstaemme, Phasenstruktur, Doku- und
Stage-1-Hinweise. Eingeblendet wird NUR, was in den Quellen woertlich
vorkommt (kein Priming). Die Phasen sind ein BEOBACHTUNGSRASTER, kein
Soll-Verlauf: das Modell beschreibt, was geschah, und bewertet nicht
(im Ausloeserfall war die "nicht gelungene Taeterentmachtung" fachlich der
Erfolg - der Taeter war der lebensrettende Arzt; die Einordnung gibt der
Therapeut ueber die Stichpunkte vor).

REGELBASIERT, KEIN LLM. Reine Substring-Erkennung auf lowercase.

Gilt fuer alle Workflows ausser anamnese/befund (D11) - dort bleibt die
bisherige binaere Logik (prompts.source_mentions_parts_work) unveraendert.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Verfahren:
    key: str
    label: str
    # Erkennung (lowercase, Substring). Bewusst breit, aber ohne
    # Alltagswoerter, die faelschlich anschlagen wuerden.
    stems: tuple[str, ...]
    # Phasen/Struktur - je Eintrag ein Satz. Beobachtungsraster.
    phasen: tuple[str, ...]
    # Was in der Gespraechsdokumentation erhalten bleiben muss.
    doku_hinweise: str
    # Was die Stage-1-Verdichtung nicht glaetten darf.
    stage1_hinweise: str
    # Marker-Gruppen fuer den QC-Phasen-Check (je Phase ein Tupel von
    # lowercase-Substrings; Phase gilt als "im Text", wenn EIN Marker trifft).
    phasen_marker: tuple[tuple[str, ...], ...]
    # True -> zusaetzlich das Anteile-Glossar (IFS-Vokabular) einblenden.
    parts_work: bool = False
    # Optional: Sitzungsvarianten, bei denen Phasen legitim fehlen.
    varianten: str = ""


VERFAHREN_REGISTER: tuple[Verfahren, ...] = (
    Verfahren(
        key="irrt",
        label="IRRT (Imagery Rescripting & Reprocessing Therapy, Schmucker/Köster)",
        stems=("irrt", "imagery rescripting", "rescripting"),
        phasen=(
            "Vorbereitung: Ausgangsszene und Auslöser werden geklärt.",
            "Phase 1 – Wiedererleben: die belastende Szene wird in der "
            "Ich-Perspektive und im Präsens imaginiert (1a vollständig von "
            "Anfang bis Ende, 1b Wiederholung bis zum Hot Spot).",
            "Phase 2 – Täterkonfrontation: am Hot Spot tritt das Heutige Ich "
            "in die Imagination; es handelt gegenüber dem Täter "
            "(Entmachtung/Neutralisierung) – oder entscheidet sich bewusst "
            "anders.",
            "Phase 3 – Zuwendung zum Damaligen Ich: das Heutige Ich wendet sich "
            "dem verletzten damaligen Ich zu (Empathie, authentische "
            "Kommunikation, Übernahme von Zuständigkeit); Abschlussbild.",
            "Nachbesprechung: kurze standardisierte Besprechung des Erlebten, "
            "Absprachen für die Folgetage.",
        ),
        doku_hinweise=(
            "Gib die Sitzung entlang dieser Phasen wieder: Ausgangsszene; "
            "was in der Ich-Perspektive erlebt wurde und wo der Hot Spot lag; "
            "was das Heutige Ich in der Szene tat und wie der Täter darauf "
            "reagierte; wie die Zuwendung zum Damaligen Ich verlief und wie "
            "das Abschlussbild aussah; was in der Nachbesprechung vereinbart "
            "wurde. Beschreibe NUR, was geschah – keine Bewertung, ob eine "
            "Phase 'gelungen' ist; die fachliche Einordnung (Trauma-, "
            "Innere-Kind- oder Trauer-Sitzung) gibt der Therapeut in den "
            "Stichpunkten vor und hat Vorrang."
        ),
        stage1_hinweise=(
            "Erhalte die Phasenabfolge (Wiedererleben – Heutiges Ich tritt "
            "ein – Zuwendung zum Damaligen Ich – Abschlussbild – "
            "Nachbesprechung) und die Ich-Perspektive der Imagination als "
            "solche erkennbar. Der Hauptteil der Sitzung ist die imaginative "
            "Arbeit; Themen vor der Imagination sind Nebenthemen und werden "
            "nicht zum Hauptanliegen."
        ),
        phasen_marker=(
            ("ausgangsszene", "auslöser", "ausloeser", "vorbereit", "situation"),
            ("hot spot", "hotspot", "ich-perspektive", "ich perspektive",
             "wiedererleb", "imagination", "imaginier"),
            ("heutige ich", "heutiges ich", "täter", "taeter", "konfront",
             "entmacht"),
            ("damalige ich", "damaliges ich", "innere kind", "inneres kind",
             "verletzte", "zuwendung", "tröst", "troest", "abschlussbild"),
            ("nachbesprech", "nachbesprochen", "vereinbart", "eingeladen"),
        ),
        varianten=(
            "Trauma-Sitzung, Innere-Kind-Sitzung, Trauer-Sitzung – Phase 2 "
            "kann entfallen oder bewusst nicht in eine Entmachtung münden."
        ),
    ),
    Verfahren(
        key="emdr",
        label="EMDR (Eye Movement Desensitization and Reprocessing)",
        stems=("emdr", "bilaterale stimulation", "bilateral stimul"),
        phasen=(
            "Zielerinnerung mit negativer und positiver Kognition, "
            "Belastung (SUD) und Stimmigkeit (VoC) werden bestimmt.",
            "Sets bilateraler Stimulation mit assoziativem Prozess.",
            "Installation der positiven Kognition, Körpertest, Abschluss.",
        ),
        doku_hinweise=(
            "Gib Zielerinnerung, Kognitionen, den Verlauf der Belastung und "
            "den Abschlusszustand wieder – nur, was im Gespräch benannt wurde."
        ),
        stage1_hinweise=(
            "Erhalte Zielerinnerung, Kognitionen und Belastungsverlauf; "
            "Sets nicht zu einer einzigen Aussage glätten."
        ),
        phasen_marker=(
            ("zielerinnerung", "kognition", "sud", "belastung"),
            ("set", "bilateral", "augenbeweg", "assoziation"),
            ("installation", "körpertest", "koerpertest", "abschluss"),
        ),
    ),
    Verfahren(
        key="ifs",
        label="IFS / Anteilearbeit",
        stems=(
            "anteil", "ifs", "teilearbeit", "manager", "antreiber", "verbannt",
            "inneres kind", "innere kind", "feuerbekämpf", "feuerbekaempf",
            "schutzschild", "im selbst", "das selbst", "türsteher", "tuersteher",
            "wächter", "waechter", "exil", "seitenmodell",
        ),
        phasen=(
            "Ein Anteil wird identifiziert und in seiner Funktion gewürdigt.",
            "Beziehung zwischen Selbst und Anteil; Absicht/Schutzfunktion.",
            "Entlastung oder Vereinbarung mit dem Anteil.",
        ),
        doku_hinweise=(
            "Benenne die Anteile mit ihrer Funktion, die Haltung des Selbst "
            "und die getroffene Vereinbarung – in der Sprache, die im "
            "Gespräch tatsächlich verwendet wurde."
        ),
        stage1_hinweise=(
            "Erhalte die Namen/Bezeichnungen der Anteile und was mit ihnen "
            "vereinbart wurde."
        ),
        phasen_marker=(
            ("anteil", "manager", "antreiber", "verbannt", "innere kind",
             "inneres kind"),
            ("selbst", "schutz", "absicht", "funktion"),
            ("vereinbar", "entlast", "erlaub"),
        ),
        parts_work=True,
    ),
    Verfahren(
        key="ego_state",
        label="Ego-State-Therapie / Stuhlarbeit",
        stems=("ego-state", "ego state", "ich-zustand", "ich-zustände",
               "stuhlarbeit", "leerer stuhl", "leeren stuhl"),
        phasen=(
            "Zustände bzw. Stühle werden benannt und positioniert.",
            "Dialog und Perspektivwechsel zwischen den Zuständen.",
            "Integration / Vereinbarung.",
        ),
        doku_hinweise=(
            "Gib wieder, welche Zustände/Stühle beteiligt waren, wer mit wem "
            "sprach und mit welchem Ergebnis."
        ),
        stage1_hinweise="Erhalte die beteiligten Zustände und den Dialogverlauf.",
        phasen_marker=(
            ("stuhl", "zustand", "ego-state", "ego state"),
            ("dialog", "sprach", "perspektiv"),
            ("integration", "vereinbar", "ergebnis"),
        ),
        parts_work=True,
    ),
    Verfahren(
        key="schematherapie",
        label="Schematherapie (Modusarbeit, Imagination mit Umschreiben)",
        stems=("schematherap", "schema-modus", "schemamodus", "modusarbeit",
               "gesunder erwachsener", "gesunde erwachsene"),
        phasen=(
            "Aktiver Modus wird benannt.",
            "Imagination mit Umschreiben; der gesunde Erwachsene greift ein.",
            "Abschluss und Transfer.",
        ),
        doku_hinweise=(
            "Benenne den Modus und die Umschreibung nur, wie im Gespräch "
            "verwendet."
        ),
        stage1_hinweise="Erhalte Modus und Umschreibung.",
        phasen_marker=(
            ("modus",),
            ("imagination", "umschreib", "gesunde erwachsene", "gesunder erwachsener"),
            ("abschluss", "transfer"),
        ),
        parts_work=True,
    ),
    Verfahren(
        key="hypnose",
        label="Hypnose / hypnosystemische Trancearbeit",
        stems=("hypnose", "trance", "utilisation", "induktion"),
        phasen=(
            "Induktion.",
            "Utilisation / Arbeit in der Trance.",
            "Reorientierung, ggf. posthypnotischer Auftrag.",
        ),
        doku_hinweise=(
            "Gib Trance-Inhalt und Ressourcenanker wieder, ohne den Ablauf "
            "zu bewerten."
        ),
        stage1_hinweise="Erhalte Trance-Inhalt und Ressourcenanker.",
        phasen_marker=(
            ("induktion", "trance"),
            ("utilis", "ressource", "anker"),
            ("reorientier", "posthypnot", "zurück"),
        ),
    ),
    Verfahren(
        key="konfrontation",
        label="Traumakonfrontation / Exposition (allgemein)",
        stems=("traumakonfrontation", "exposition", "screen-technik",
               "screentechnik", "bildschirmtechnik"),
        phasen=(
            "Stabilisierung und Vorbereitung.",
            "Konfrontation mit dem belastenden Material.",
            "Nachbereitung.",
        ),
        doku_hinweise=(
            "Gib den Belastungsverlauf und den Abschluss wieder – nur, was "
            "im Gespräch benannt wurde."
        ),
        stage1_hinweise="Erhalte Belastungsverlauf und Abschluss.",
        phasen_marker=(
            ("stabilis", "vorbereit"),
            ("konfront", "exposition", "belast"),
            ("nachbereit", "abschluss"),
        ),
    ),
)

VERFAHREN_BY_KEY: dict[str, Verfahren] = {v.key: v for v in VERFAHREN_REGISTER}

# Workflows, fuer die das Register NICHT gilt (D11): Anamnese und ihr
# Befund-Subcall behalten die bisherige binaere Glossar-Logik.
REGISTER_EXCLUDED_WORKFLOWS: frozenset[str] = frozenset({"anamnese", "befund"})


def register_applies(workflow: str | None) -> bool:
    return bool(workflow) and workflow not in REGISTER_EXCLUDED_WORKFLOWS


def _norm(text: str) -> str:
    return (text or "").lower()


def erkannte_verfahren(source_text: str | None) -> list[Verfahren]:
    """Alle Verfahren, deren Stamm in den Quellen (lowercase, Substring)
    vorkommt - in Registerreihenfolge. Leere Quelle -> []."""
    if not source_text or not source_text.strip():
        return []
    low = _norm(source_text)
    return [v for v in VERFAHREN_REGISTER if any(s in low for s in v.stems)]


def verfahren_keys(source_text: str | None) -> list[str]:
    return [v.key for v in erkannte_verfahren(source_text)]


def mentions_parts_work(source_text: str | None) -> bool:
    """True, wenn ein parts_work-Verfahren in den Quellen belegt ist."""
    return any(v.parts_work for v in erkannte_verfahren(source_text))


# ── Prompt-Bausteine ─────────────────────────────────────────────────────────

_NAMES_ONLY_HEADER = (
    "QUELLENTREUE-FESTSTELLUNG FUER DIESEN AUFTRAG (wichtigste Regel):\n"
    "In den Quellen dieses Auftrags (Transkript, Unterlagen oder Stichpunkte "
    "des Therapeuten) wird folgendes Verfahren wörtlich genannt: {names}.\n"
    "Benenne AUSSCHLIESSLICH dieses Verfahren namentlich und verwende nur "
    "dessen Fachbegriffe, soweit sie im Gespräch tatsächlich vorkamen. "
    "Andere Therapieverfahren und deren Vokabular NICHT verwenden. "
    "Im Übrigen schreibe deskriptiv-systemisch: Erlebensmuster, "
    "Schutzreaktionen, innere Bewegungen und Beziehungsdynamiken so, wie sie "
    "im Gespräch sichtbar wurden."
)


def render_verfahren_feststellung(verfahren: list[Verfahren]) -> str:
    """Ersatz fuer die neutrale QUELLENTREUE-FESTSTELLUNG, wenn Verfahren
    woertlich belegt sind. Leere Liste -> ''."""
    if not verfahren:
        return ""
    names = ", ".join(v.label for v in verfahren)
    text = _NAMES_ONLY_HEADER.format(names=names)
    if len(verfahren) > 1:
        text = text.replace("folgendes Verfahren wörtlich genannt",
                            "folgende Verfahren wörtlich genannt")
        text = text.replace("AUSSCHLIESSLICH dieses Verfahren", "AUSSCHLIESSLICH diese Verfahren")
        text = text.replace("nur dessen Fachbegriffe", "nur deren Fachbegriffe")
    return text


def render_verfahren_phasenblock(verfahren: list[Verfahren]) -> str:
    """Phasen- und Doku-Hinweise (nur P1). Leere Liste -> ''."""
    if not verfahren:
        return ""
    blocks = []
    for v in verfahren:
        phasen = "\n".join(f"  {i}. {p}" for i, p in enumerate(v.phasen, 1))
        block = (
            f"VERFAHRENSSTRUKTUR {v.label}\n"
            f"Beobachtungsraster der Sitzung (kein Soll-Verlauf):\n{phasen}\n"
            f"Dokumentation: {v.doku_hinweise}"
        )
        if v.varianten:
            block += f"\nVarianten: {v.varianten}"
        blocks.append(block)
    return "\n\n".join(blocks)


def render_verfahren_stage1_hinweis(verfahren: list[Verfahren]) -> str:
    """Zusatzblock fuer die Stage-1-Verdichtung. Leere Liste -> ''."""
    if not verfahren:
        return ""
    lines = [
        "ANGEWENDETES VERFAHREN (in Quellen/Stichpunkten wörtlich genannt): "
        + ", ".join(v.label for v in verfahren) + "."
    ]
    for v in verfahren:
        lines.append(f"- {v.key.upper()}: {v.stage1_hinweise}")
    return "\n".join(lines)


# ── QC-Hilfen ────────────────────────────────────────────────────────────────

def _word_re(stem: str) -> re.Pattern:
    return re.compile(r"(?<![a-zäöüß])" + re.escape(stem), re.IGNORECASE)


def verfahren_benannt(text: str, verfahren: Verfahren) -> bool:
    """True, wenn der Verfahrensname (ein Stamm an Wortanfang) im Output
    vorkommt. Wortanfang-Grenze, damit 'ifs' nicht in 'Beifall' trifft."""
    if not text:
        return False
    return any(_word_re(s).search(text) for s in verfahren.stems)


def fehlende_phasen(text: str, verfahren: Verfahren) -> list[int]:
    """1-basierte Indizes der Phasen-Markergruppen, die im Text nicht
    vorkommen. Nur Beobachtung - keine Bewertung."""
    low = _norm(text)
    fehlend: list[int] = []
    for i, gruppe in enumerate(verfahren.phasen_marker, 1):
        if not any(m in low for m in gruppe):
            fehlend.append(i)
    return fehlend
