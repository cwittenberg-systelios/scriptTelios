"""
Interview-Fragen-Sets — die EINZIGE Quelle der Wahrheit fuer den
Interview-Modus der Gespraechsdokumentation (v19.23).

Fachlicher Hintergrund (Featurerequest 2026-09-16):
Nicht jede Sitzung liefert ein Transkript - nonverbale Verfahren (Kunst,
Musik, Koerperarbeit, Koerpertherapie) haben keinen sprachlichen Kern, und
ohne Aufzeichnungseinwilligung des Klienten gibt es keine Aufnahme. Statt
Transkript fuehrt das System nach der Sitzung einen kurzen Dialog mit dem
Behandler: Frage -> Antwort -> ggf. EINE Rueckfrage -> Weiter. Das
Protokoll dieses Dialogs ist die Quelle fuer die Dokumentation.

Entscheidungen (Sprintplan v19.23, vom Nutzer bestaetigt 2026-09-16):
  E3   Sets: Kunst, Koerpertherapie (analog Physiotherapie+), Musik,
       Koerperarbeit, Gespraech. Ein Behandler nutzt genau ein Set.
  E4   Set "Gespraech" = die vier Gliederungsabschnitte als Fragen.
  E7   Kein neuer Workflow-Key: das Protokoll ist ein dritter Quelltyp
       neben Transkript und Stichpunkten.
  E8   Frage zur Selbstgefaehrdung ist in jedem Set Pflicht; ihre Antwort
       wird Teil der Quellen fuer den Suizidalitaets-Pflichthinweis (v19.22).
  D1=C Rueckfrage nur, wenn das LLM einen Pflichtaspekt der Frage in der
       Antwort nicht abgedeckt sieht (max. eine Rueckfrage je Frage).
  D5=A Koerpertherapie und Koerperarbeit sind getrennte Sets.

Konsumenten:
  - app/api/interview.py          -> GET /api/interview/sets (Manifest)
  - app/services/interview_dialog -> Pflichtaspekte fuer den Rueckfrage-Check
  - app/services/prompts.py       -> render_protokoll() baut den Quellblock
  - Frontend (interview.jsx)      -> Fragen editierbar, Defaults vom Manifest

Die Fragen sind in der Du-Form gehalten, weil das System den Behandler
anspricht (Teamsprache der Klinik) - nicht den Klienten.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Zielabschnitte ────────────────────────────────────────────────────────────
# Entsprechen der Gliederung in WORKFLOW_INSTRUCTIONS_DEFAULT["dokumentation"]
# (E2: Gliederung bleibt vorerst die Hospidea-Gliederung; sie ist ueber den
# Prompt-Editor je Nutzer aenderbar). "schluss" = freier Schlussabsatz,
# in dem der Suizidalitaets-Hinweis steht (v19.22 D3=A).
ABSCHNITTE: dict[str, str] = {
    "auftragsklaerung": "Auftragsklärung",
    "inhalte":          "Relevante Gesprächsinhalte",
    "hypothesen":       "Hypothesen und Entwicklungsperspektiven",
    "einladungen":      "Einladungen",
    "schluss":          "Schlussabsatz (Hinweis zur Suizidalität)",
    # v19.24 (B2): Meta-Fragen (Klient) - werden NICHT in den Prompt gerendert.
    "meta":             "Organisatorisch (nicht in der Dokumentation)",
}

SELBSTGEFAEHRDUNG_KEY = "selbstgefaehrdung"
KLIENT_KEY = "klient"


@dataclass(frozen=True)
class InterviewFrage:
    key: str                          # stabiler Schluessel innerhalb des Sets
    text: str                         # Frage an den Behandler (Du-Form)
    ziel_abschnitt: str               # Key aus ABSCHNITTE
    pflicht: bool = False             # darf nicht leer bleiben
    pflichtaspekte: tuple[str, ...] = field(default_factory=tuple)
                                      # was die Antwort abdecken soll (D1=C)
    hinweis: str = ""                 # kurze UI-Hilfe unter der Frage


@dataclass(frozen=True)
class InterviewSet:
    key: str
    label: str
    beschreibung: str
    fragen: tuple[InterviewFrage, ...]


# Die Pflichtfrage ist in allen Sets identisch (E8). Bewusst nicht per
# Set editierbar: der Pflichthinweis v19.22 haengt an ihr.
FRAGE_SELBSTGEFAEHRDUNG = InterviewFrage(
    key=SELBSTGEFAEHRDUNG_KEY,
    text=("Gab es in der Stunde Hinweise auf Selbstgefährdung – Suizidalität, "
          "Selbstverletzung oder eine akute Krise? Falls nein, sag bitte kurz, "
          "dass es keine Hinweise gab."),
    ziel_abschnitt="schluss",
    pflicht=True,
    pflichtaspekte=("eine klare Aussage, ob Hinweise auf Suizidalität vorlagen",),
    hinweis="Pflichtfrage – die Doku enthält immer einen Satz dazu.",
)

# v19.24 (B2): erste Frage jedes Sets. Antwort fuellt Kuerzel und Geschlecht
# des Jobs; Rueckfragen duerfen danach die Anrede verwenden. Nicht editierbar.
FRAGE_KLIENT = InterviewFrage(
    key=KLIENT_KEY,
    text="Um wen geht es? Bitte Anrede und Kürzel, zum Beispiel „Frau K.“.",
    ziel_abschnitt="meta",
    pflicht=True,
    hinweis="Nur Anrede und Anfangsbuchstabe des Nachnamens – kein voller Name.",
)

_FRAGE_VEREINBARUNG = InterviewFrage(
    key="vereinbarung",
    text="Was habt ihr vereinbart, wie es weitergeht? Gab es Einladungen, "
         "Aufgaben oder Impulse für die Zeit bis zur nächsten Stunde?",
    ziel_abschnitt="einladungen",
    hinweis="Nur was tatsächlich ausgesprochen wurde – 'nichts vereinbart' ist eine gültige Antwort.",
)


def _nonverbal_set(key: str, label: str, beschreibung: str, *,
                   methode_text: str, beobachtung_text: str) -> InterviewSet:
    """Gemeinsames Grundgeruest der nonverbalen Sets - nur Methoden- und
    Beobachtungsfrage sind verfahrensspezifisch formuliert."""
    return InterviewSet(
        key=key, label=label, beschreibung=beschreibung,
        fragen=(
            FRAGE_KLIENT,
            InterviewFrage(
                key="anliegen",
                text="Was war das erarbeitete Anliegen? Womit kam die Person in die Stunde?",
                ziel_abschnitt="auftragsklaerung",
                pflichtaspekte=("das Anliegen oder Thema der Person",),
            ),
            InterviewFrage(
                key="methode",
                text=methode_text,
                ziel_abschnitt="inhalte",
                pflichtaspekte=("die eingesetzte Methode oder Intervention",),
            ),
            InterviewFrage(
                key="beobachtung",
                text=beobachtung_text,
                ziel_abschnitt="inhalte",
                pflichtaspekte=("Emotionen oder Ausdruck der Person",
                                "Beziehungsgestaltung oder Kontakt"),
            ),
            InterviewFrage(
                key="ergebnis",
                text="Was war das Ergebnis der Stunde, und in welchem Zustand "
                     "geht die Person? Wie schätzt du den Prozess und die "
                     "Entwicklungsperspektive ein?",
                ziel_abschnitt="hypothesen",
                pflichtaspekte=("der Zustand der Person am Ende der Stunde",),
            ),
            _FRAGE_VEREINBARUNG,
            FRAGE_SELBSTGEFAEHRDUNG,
        ),
    )


INTERVIEW_SETS: tuple[InterviewSet, ...] = (
    InterviewSet(
        key="gespraech",
        label="Gespräch (ohne Aufzeichnung)",
        beschreibung="Einzelgespräch ohne Aufnahme, z.B. ohne Einwilligung zur Aufzeichnung.",
        fragen=(
            FRAGE_KLIENT,
            InterviewFrage(
                key="anliegen",
                text="Worum ging es im Gespräch? Was war das Anliegen der Person "
                     "und das gemeinsame Ziel?",
                ziel_abschnitt="auftragsklaerung",
                pflichtaspekte=("das Anliegen oder Ziel des Gesprächs",),
            ),
            InterviewFrage(
                key="inhalte",
                text="Was waren die wesentlichen Inhalte? Was hat die Person "
                     "berichtet, welche Muster, Anteile oder Ressourcen wurden sichtbar?",
                ziel_abschnitt="inhalte",
                pflichtaspekte=("konkrete Inhalte des Gesprächs",),
            ),
            InterviewFrage(
                key="hypothesen",
                text="Welche Hypothesen, Reframings oder Sinnzuschreibungen habt "
                     "ihr erarbeitet? Was wird möglich, wenn …?",
                ziel_abschnitt="hypothesen",
            ),
            _FRAGE_VEREINBARUNG,
            FRAGE_SELBSTGEFAEHRDUNG,
        ),
    ),
    _nonverbal_set(
        "kunst", "Kunsttherapie",
        "Gestalterische Einzel- oder Gruppenarbeit.",
        methode_text="Welche Methode, welches Material oder welche Aufgabe habt "
                     "ihr verwendet, und was ist entstanden?",
        beobachtung_text="Welche Emotionen, welchen Ausdruck und welche "
                         "Beziehungsgestaltung hast du im nichtsprachlichen Teil "
                         "wahrgenommen – im Gestalten, im Bild, im Kontakt?",
    ),
    _nonverbal_set(
        "musik", "Musiktherapie",
        "Aktive oder rezeptive musiktherapeutische Arbeit.",
        methode_text="Welche Methode habt ihr verwendet – Improvisation, Instrumente, "
                     "Stimme, rezeptives Hören – und was ist dabei entstanden?",
        beobachtung_text="Welche Emotionen, welchen Ausdruck und welche "
                         "Beziehungsgestaltung hast du im Spiel und im Klang "
                         "wahrgenommen – Tempo, Dynamik, Kontakt, Pausen?",
    ),
    _nonverbal_set(
        "koerperarbeit", "Körperarbeit",
        "Körperorientierte psychotherapeutische Arbeit.",
        methode_text="Welche körperorientierte Intervention oder Übung habt ihr "
                     "gemacht – Wahrnehmung, Atem, Bewegung, Berührung, Aufstellung?",
        beobachtung_text="Was hast du an Körperwahrnehmung, Regulation, Emotion "
                         "und Kontakt beobachtet – Anspannung, Atem, Halt, Nähe und Distanz?",
    ),
    _nonverbal_set(
        "koerpertherapie", "Körpertherapie",
        "Körpertherapeutische Behandlung (physiotherapienah).",
        methode_text="Welche Beschwerden standen im Vordergrund, was hast du "
                     "befundet und welche Maßnahmen oder Techniken hast du angewendet?",
        beobachtung_text="Wie hat die Person die Behandlung erlebt und vertragen? "
                         "Was hast du an Körperspannung, Bewegung, Schmerzverhalten "
                         "und Kontakt beobachtet?",
    ),
)

INTERVIEW_SET_KEYS: frozenset[str] = frozenset(s.key for s in INTERVIEW_SETS)
DEFAULT_SET_KEY = "gespraech"


def get_set(key: str) -> InterviewSet | None:
    for s in INTERVIEW_SETS:
        if s.key == key:
            return s
    return None


def get_frage(set_key: str, frage_key: str) -> InterviewFrage | None:
    s = get_set(set_key)
    if not s:
        return None
    for f in s.fragen:
        if f.key == frage_key:
            return f
    return None


def to_manifest() -> dict:
    """Serialisierbares Manifest fuer GET /api/interview/sets."""
    return {
        "default_set": DEFAULT_SET_KEY,
        "abschnitte": dict(ABSCHNITTE),
        "sets": [
            {
                "key": s.key,
                "label": s.label,
                "beschreibung": s.beschreibung,
                "fragen": [
                    {
                        "key": f.key,
                        "text": f.text,
                        "ziel_abschnitt": f.ziel_abschnitt,
                        "pflicht": f.pflicht,
                        "pflichtaspekte": list(f.pflichtaspekte),
                        "hinweis": f.hinweis,
                    }
                    for f in s.fragen
                ],
            }
            for s in INTERVIEW_SETS
        ],
    }
