"""
System-Prompts für alle vier Workflows.
Werden mit Kontext (Stilprofil, Diagnosen etc.) zusammengefügt.

Struktur:
  KLINISCHES_GLOSSAR  – Fachterminologie als Referenz für das Modell
  FEW_SHOT_*          – Beispiel-Paare pro Workflow
  ROLE_PREAMBLE       – Rollenkontext + Glossar (allen Prompts vorangestellt)
  BASE_PROMPTS        – Workflow-spezifische Anweisungen inkl. Few-Shot
  build_system_prompt / build_user_content – Zusammenbau-Funktionen
"""
from typing import Optional
import re

# Zentraler abkuerzungsfester Satz-Splitter (R1) - Stilanalyse nutzt ab
# v19.5/Issue-2 dieselbe Satzdefinition wie Hard-Cap und Loop-Detection.
from app.services.postprocessing import split_sentences_de


# ── Workflow-Kategorisierung ─────────────────────────────────────────────────
# Strukturelle Workflows: P2/P3/P4 - Stilbeispiel wird als Schablone verwendet
# (Gliederung, Laenge, Absatztiefe werden uebernommen). Single source of truth -
# wird in build_system_prompt mehrfach geprueft, frueher als is_structural-Liste
# und has_structural_template separat - dabei kam es zu Inkonsistenzen
# (folgeverlaengerung/akutantrag bekamen Schablone aber falschen Abschlusssatz).
STRUCTURAL_WORKFLOWS = frozenset({
    "anamnese",
    "verlaengerung",
    "folgeverlaengerung",
    "akutantrag",
    "entlassbericht",
})


# ── Datenschutz-Namensregel (zentral) ────────────────────────────────────────
# Wird in build_user_content einmalig in den User-Content aufgenommen (NICHT in
# build_system_prompt - der frühere Kommentar war falsch). Die formalen
# Antrags-/Bericht-Workflows fuehren zusaetzlich den NAMENSFORMAT-Pflichtkern im
# System-Prompt, und bei bekanntem Namen kommt der konkrete PATIENTENNAME-Block
# in build_system_prompt dazu.
NAMENSREGEL = (
    "DATENSCHUTZ – NAMENSFORMAT (gilt fuer den gesamten Text):\n"
    "Verwende AUSSCHLIESSLICH den ersten Buchstaben des Nachnamens mit Punkt: "
    "Initialen des AKTUELLEN Patienten aus den Unterlagen "
    "(z.B. wenn der Patient 'Andreas Reif' heisst: 'Herr R.', "
    "wenn 'Maria Schmidt': 'Frau S.') – NIEMALS den vollen Nachnamen, "
    "NIEMALS den Vornamen, NIEMALS Namen aus Stilbeispielen. "
    "Selbst wenn der volle Name in den Quellen steht: nur Initiale verwenden."
)


# ── Wiederverwendete Pflichtkern-Bausteine (v19.5 Konsolidierung) ─────────────
# Diese Bloecke standen vorher 3-5x nahezu wortgleich in einzelnen BASE_PROMPTS
# verstreut (mit Mini-Abweichungen durch Copy-Paste-Drift). Eine Quelle der
# Wahrheit verhindert Widersprueche (z.B. Entlassbericht erzwang frueher Wir,
# obwohl die Workflow-Anweisung 'Wir ODER 3.-Person' sagte).

# Stil-Mimik: Tonfall folgt der Stilvorlage (Wir ODER empathische 3.-Person),
# kein objektiv-distanzierter Berichtston, kein reines Passiv.
STIL_VORLAGEN_MIMIK = (
    "Folge dem Tonfall der Stilvorlage (Wir-Sicht des Therapeutenteams ODER "
    "empathische 3.-Person mit innerer Patientenperspektive). "
    "VERMEIDE den objektiv-wissenden Berichtston ('Der Patient zeigte X', "
    "'Die Klientin äußerte Y'). Schreibe stattdessen aus innerer Perspektive: "
    "'Sie berichtete, sie fühle sich überfordert', 'Wir erlebten Frau M. "
    "zunehmend ...'. VERMEIDE auch reine Passivkonstruktionen ('es zeigte sich', "
    "'konnte differenziert werden', 'wurde bearbeitet'). Setze ein konkretes "
    "Subjekt (Wir/Patient_in). Systemische Fachsprache wo inhaltlich passend. "
    "Fließtext, keine Aufzählungen.\n"
)

# Namensformat fuer die formalen Antrags-/Bericht-Workflows. (Die Doku P1 nutzt
# bewusst 'die Klientin/der Klient' und bekommt diesen Block NICHT.)
NAMENSFORMAT = (
    "NAMENSFORMAT: Nur erster Buchstabe des Nachnamens des AKTUELLEN Patienten "
    "(z.B. 'Frau K.' / 'Herr S.'). NIEMALS einen Platzhalter (z.B. eckige Klammern "
    "um das Wort Patient/in) und niemals Namen aus dem Stilbeispiel verwenden, "
    "ebenso wenig 'die Klientin' / 'der Klient'.\n\n"
)


# ── Fachglossar ──────────────────────────────────────────────────────────────

KLINISCHES_GLOSSAR = """FACHLICHES REFERENZWISSEN (sysTelios-Klinik):

QUELLENTREUE-REGEL (gilt durchgängig, ist die wichtigste Regel dieses Glossars):
Verwende konkrete Therapieverfahren (IFS, Hypnosystemik, Schematherapie,
EMDR, Stuhlarbeit etc.) und ihre Fachbegriffe (Manager, Antreiber, Verbannte, 'im Selbst sein',
Schema-Modus, zirkuläre Fragen, Reframing etc.)
NUR DANN namentlich, wenn das Verfahren oder seine Begriffe in den Quellen
(Transkript, Stichpunkte, Verlaufsdokumentation, Antragsvorlage,
Selbstauskunft) explizit vorkommen oder erkennbar angewendet wurden.
Andernfalls schreibe in deskriptiv-systemischer Sprache
('innerer Schutzanteil', 'Vermeidungsmuster', 'Selbstabwertung',
'innere Kritikerstimme', 'überwachender Anteil') und benenne KEIN Verfahren.
Im Zweifel: lieber neutral-deskriptiv als ein Verfahren erfinden.

Therapeutische Ansätze (Referenzvokabular – nur einsetzen wenn im Material belegt):
- IFS / Anteilemodell / Anteilearbeit (Begriffe wie an der sysTelios-Klinik üblich):
  schützende Anteile - Manager, Antreiber, Richter (innerer Kritiker),
  Feuerbekämpfer (reaktiv: Dissoziation, Sucht, Selbstverletzung); verletzte
  Anteile - Verbannte, inneres Kind ('das kleine Mädchen', 'der kleine Junge'),
  das 'frühere Ich'. Steuerungsposition: 'im Selbst sein', 'das Selbst spüren'
  (Ruhe, Neugier, Mitgefühl, Klarheit). WICHTIG: die englischen Begriffe
  'Self-Energy' / 'Self-Leadership' werden hier NICHT verwendet. Anteile bekommen
  oft eigene Namen ('Türsteher', 'Wächterin', 'Schutzschild').
- Ego-State-Therapie (von anderen Therapeut/innen genutzt, gleichwertig zur
  Anteilearbeit): Ego-States / Ich-Zustände, verletzte vs. ressourcenreiche
  Anteile - dieselbe Teile-Sprache, andere Schule. Ebenfalls nur wenn im Material belegt.
- Hypnosystemik (G. Schmidt): Ressourcenaktivierung, Seitenmodell,
  Körpersignale als Bedürfnisrückmeldung, körperliche Symptome in
  Bedürfnisse übersetzen, annehmende Beziehung zum Organismus,
  selbstwirksame Einflussnahme.
- Systemische Therapie: zirkuläre Fragen, Reframing, Auftragsklärung,
  Externalisierung, Stuhlarbeit, Netzwerk-/Körperarbeit.
  Symptome als sinnvolle Schutzreaktion verstehen.
- Biographiearbeit: frühere Sinnhaftigkeit von Strategien als
  Überlebensleistung würdigen, biographische Erfahrungen mit aktuellen
  Mustern verbinden.
- Traumafokussiert: Window of Tolerance, Stabilisierung, Embodiment.
- AMDP-Schema: Bewusstsein, Orientierung, Aufmerksamkeit/Gedächtnis,
  formales und inhaltliches Denken, Wahrnehmung, Ich-Erleben, Affektivität,
  Antrieb, Suizidalität.

Therapieangebot sysTelios: Einzelgespräche (2-3/Woche), Gruppentherapie
(Gesprächs-, Kunst-, Musik-, Körper-, Bewegungstherapie, mind. 5/Woche),
Bezugsgruppe, Paar-/Familiengespräche. Konzept: tiefenpsychologisch fundiert,
verhaltenstherapeutisch ergänzt, hypnosystemisch optimiert.

Klinik-typische Wendungen (im realen Korpus belegt – nur wenn sachlich passend):
- 'Mithilfe des Therapiekonzepts gelang es [Name] die intrapsychischen
  Erlebensmuster und deren Einfluss auf die Symptome zu verstehen und
  schrittweise zu beeinflussen.'
- 'Anhand des Anteilemodells gelang es [Name] die frühere Sinnhaftigkeit
  der Kognitionen als Überlebensstrategie zu verstehen.'
- 'Durch Stuhlarbeit, Netzwerk- und Körperarbeit gelang es in ersten
  Schritten eine Beobachterposition einzunehmen und eine wohlwollendere
  innere Haltung zu entwickeln.'
- 'Wir erlebten [Name] zu Therapiebeginn deutlich erschöpft und in
  seinem/ihrem Selbstwert erheblich verunsichert.'
- 'Die Alltagstauglichkeit ist derzeit noch nicht gegeben.'
- 'Eine tragfähige Stabilität für den ambulanten Kontext ist noch nicht erreicht.'
- Befund: 'bewusstseinsklar, allseits orientiert' / 'Affekt situationsadäquat
  schwingungsfähig' / 'formalgedanklich grübelnd, eingeengtes Denken mit Fokus
  auf [X]'.

Häufige Beobachtungs-Vokabeln (deskriptiv, verfahrensagnostisch –
auch ohne Verfahrensbezug einsetzbar):
Schutzreaktion, Schutzfunktion, Selbstabwertung, Selbstmitgefühl,
Schwingungsfähigkeit, Selbstwirksamkeit, Selbstfürsorge, Reflexionsfähigkeit,
Emotionsregulation, Resonanzraum, Beobachterposition, innere Klarheit,
biographische Verwurzelung, Vermeidungsmuster, Beziehungsdynamik,
Anspannungszustände, Grübelneigung, Nähe-Distanz-Themen.\
"""


# ── Institutionelles Klinik-Glossar (2026-07-03, Live-Fund Herr W.) ──────────
#
# Getrennt vom KLINISCHES_GLOSSAR (Therapieverfahren/Fachbegriffe): hier geht
# es um sysTelios-INSTITUTIONELLE Begriffe - Gruppen-/Gesprächsformate,
# Rollen, Ablaeufe -, die fuer ein allgemeines Sprachmodell ungewoehnlich
# klingen, obwohl sie im Klinikkontext gebraeuchlich sind ("Familiengespräch"
# war der konkrete Ausloeser: korrekt transkribiert, aber vom LLM unnoetig
# umschrieben; Whisper halluzinierte an anderer Stelle "Bruder-Treibens-
# Gespräch" statt eines aehnlichen Begriffs - dafuer wurde stattdessen
# WHISPER_INITIAL_PROMPT in transcription.py erweitert, siehe dort).
#
# STRUKTUR ZUM BEFUELLEN: "Begriff": "kurze Erklaerung, was gemeint ist".
# Absichtlich KEIN Ersetzungs-/Mapping-Mechanismus (X ersetzt Y) - das waere
# fragil und kontextblind. Stattdessen bekommt das Modell die Begriffe
# ERKLAERT, damit es sie korrekt einordnet und selbst treffend verwendet,
# statt sie zu paraphrasieren oder misszuverstehen.
#
# Ein paar Startbegriffe sind bereits gefuellt (aus dem Live-Fund + den
# offensichtlichen Parallelbegriffen); c.wittenberg ergaenzt weitere nach
# Bedarf - kein Code-Wissen noetig, nur diesen dict erweitern.
INSTITUTIONELLES_GLOSSAR: dict[str, str] = {
    "Familiengespräch": "gemeinsames Gespräch mit Patient/in und Angehörigen",
    "Transfergespräch": "Übergabegespräch zwischen stationärer Behandlung und "
                        "der ambulant weiterbehandelnden Praxis/Therapeut/in",
    "Angehörigengespräch": "Gespräch mit Angehörigen, ggf. ohne Patient/in",
    "Bezugsgruppe": "feste Kleingruppe von Patient/innen mit gemeinsamem "
                    "Bezugstherapeuten/gemeinsamer Bezugstherapeutin",
    "Ü-Gruppe": "Übergangsgruppe/Ü-Gruppe für Patient/innen kurz vor Entlassung",
    # weitere Begriffe hier ergaenzen ("Begriff": "Erklaerung"), z.B.:
    # "Konzeptgruppe": "...",
    # "Sprechstundenzeit": "...",
}


# ── v19.17 (P-1): Wendungsblock workflow-sensitiv ────────────────────────────
#
# Die "Klinik-typischen Wendungen" beider Glossar-Varianten sind Berichts-
# Sprache (Wir-Form, Antragsfloskeln wie 'Alltagstauglichkeit ... nicht
# gegeben'). Fuer P1 (Einzelgespraechs-Doku) werden sie durch gespraechsnahe
# deskriptive Wendungen ersetzt (Nutzerfeedback 2026-08: Wir-Form wirkt in
# der Doku kuenstlich). Deterministischer Regex-Tausch zwischen den stabilen
# Markern "Klinik-typische Wendungen" und "Häufige Beobachtungs-Vokabeln" -
# schlaegt der Tausch fehl, bleibt schlicht das alte Verhalten (Muster analog
# FEW_SHOT_DOKUMENTATION-Tausch). P2 (Anamnese) behaelt die Wendungen bewusst
# unveraendert (Entscheid F4).

_DOKU_WENDUNGEN_BLOCK = """Gesprächsnahe Wendungen (Einzelgesprächs-Dokumentation - nur wenn sachlich passend):
- 'Im Mittelpunkt des Gesprächs stand ...'
- '[Name] beschreibt sich eingangs des Gesprächs als ...'
- '[Name] berichtete/schilderte, dass ...'
- 'Im Gespräch zeigte sich ...'
- '[Name] wurde eingeladen, ...' / 'Als Übung wurde vereinbart, ...'
- 'Ein therapeutisches Angebot für die Zwischenzeit ...'
"""

_WENDUNGEN_RE = re.compile(
    r"Klinik-typische Wendungen \(im realen Korpus belegt [–-] nur wenn "
    r"sachlich passend\):\n.*?(?=\nHäufige Beobachtungs-Vokabeln)",
    re.DOTALL,
)


def _swap_wendungen_for_doku(glossar: str) -> str:
    """Ersetzt den Berichts-Wendungsblock durch die P1-Variante."""
    swapped, n = _WENDUNGEN_RE.subn(_DOKU_WENDUNGEN_BLOCK.rstrip("\n"), glossar)
    if n != 1:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "v19.17 Wendungs-Tausch griff nicht (n=%d) - Glossar unveraendert.", n
        )
        return glossar
    return swapped


def _render_institutionelles_glossar() -> str:
    """Baut den Prompt-Textblock aus INSTITUTIONELLES_GLOSSAR.

    Leerer dict -> leerer String (Block entfaellt sauber aus dem Prompt,
    kein Platzhalter-Rauschen). So kann das Glossar gefahrlos leer bleiben,
    bis Begriffe ergaenzt werden.
    """
    if not INSTITUTIONELLES_GLOSSAR:
        return ""
    zeilen = "\n".join(
        f"- {begriff}: {erklaerung}"
        for begriff, erklaerung in INSTITUTIONELLES_GLOSSAR.items()
    )
    return (
        "\n\nKLINIK-ORGANISATORISCHE BEGRIFFE (sysTelios, institutionell - "
        "keine Therapieverfahren): Diese Begriffe sind im Klinikalltag "
        "gebräuchlich, auch wenn sie ungewöhnlich klingen. Verwende sie "
        "korrekt, wenn sie in den Quellen vorkommen oder erkennbar gemeint "
        "sind - umschreibe sie nicht unnötig:\n" + zeilen
    )


# ── Issue-2 (2026-07-03): Konditionales Glossar ──────────────────────────────
#
# Kernbefund aus Eval + Produktions-Log (e74be3): Die konditionale
# QUELLENTREUE-REGEL verliert gegen das Priming - allein die AUFZAEHLUNG der
# IFS-Begriffe (Manager, Verbannte, Tuersteher ...) im System-Prompt saet die
# Tokens, das Modell stuelpt sie quellenfremden Faellen auf. Auch Negation
# primt ("Name-Leak"-Lektion v19.4). Deterministische Loesung: Die Begriffe
# stehen NUR DANN im Prompt, wenn die Quellen des konkreten Auftrags
# Teilearbeit-Vokabular enthalten. build_system_prompt() waehlt anhand von
# source_text; ohne source_text (Legacy/Tests) bleibt das Verhalten alt.

# Stems (lowercase) zur Erkennung von Teilearbeit/verfahrensspezifischem
# Vokabular in den QUELLEN. Bewusst breit (Substring-Match): lieber einmal
# zu oft das volle Glossar als eine verpasste legitime Anteile-Session.
PARTS_WORK_STEMS: frozenset = frozenset({
    "anteil", "ifs", "teilearbeit", "manager", "antreiber", "verbannt",
    "inneres kind", "innere kind", "ego-state", "ego state", "ich-zustand",
    "türsteher", "tuersteher", "wächter", "waechter", "exil", "feuerbekämpf",
    "feuerbekaempf", "schutzschild", "im selbst", "das selbst", "seitenmodell",
    "stuhlarbeit", "schema-modus", "schemamodus", "emdr",
})


def source_mentions_parts_work(source_text: str) -> bool:
    """True wenn die Quellen Teilearbeit-/verfahrensspezifisches Vokabular
    enthalten -> volles KLINISCHES_GLOSSAR + IFS-Beispiel sind dann korrekt
    und noetig. Sonst neutrales Glossar (Priming-Vermeidung)."""
    if not source_text:
        return False
    low = source_text.lower()
    return any(stem in low for stem in PARTS_WORK_STEMS)


# Neutrale Glossar-Variante: KEINE Verfahrens-/Anteilsbegriff-Aufzaehlung
# (kein Priming), dafuer eine explizite Pro-Auftrag-Feststellung und das
# verfahrensagnostische Beobachtungs-Vokabular als positives Angebot.
KLINISCHES_GLOSSAR_NEUTRAL = """FACHLICHES REFERENZWISSEN (sysTelios-Klinik):

QUELLENTREUE-FESTSTELLUNG FUER DIESEN AUFTRAG (wichtigste Regel):
Die Quellen dieses Auftrags enthalten KEINE Teilearbeit- oder
verfahrensspezifischen Begriffe. Benenne daher KEIN Therapieverfahren
namentlich und verwende KEINE verfahrensspezifischen Fachbegriffe.
Schreibe durchgängig in deskriptiv-systemischer Sprache: beschreibe
Erlebensmuster, Schutzreaktionen, innere Bewegungen und Beziehungsdynamiken
so, wie sie im Gespräch sichtbar wurden - konkret, würdigend,
ressourcenorientiert.

Haltung (hypnosystemisch, G. Schmidt): Ressourcenaktivierung, Körpersignale
als Bedürfnisrückmeldung, körperliche Symptome in Bedürfnisse übersetzen,
annehmende Beziehung zum Organismus, selbstwirksame Einflussnahme.
Systemisch: Auftragsklärung, Symptome als sinnvolle Schutzreaktion verstehen.
Biographiearbeit: frühere Sinnhaftigkeit von Strategien als
Überlebensleistung würdigen, biographische Erfahrungen mit aktuellen
Mustern verbinden.
Traumafokussiert: Window of Tolerance, Stabilisierung, Embodiment.
AMDP-Schema: Bewusstsein, Orientierung, Aufmerksamkeit/Gedächtnis,
formales und inhaltliches Denken, Wahrnehmung, Ich-Erleben, Affektivität,
Antrieb, Suizidalität.

Therapieangebot sysTelios: Einzelgespräche (2-3/Woche), Gruppentherapie
(Gesprächs-, Kunst-, Musik-, Körper-, Bewegungstherapie, mind. 5/Woche),
Bezugsgruppe, Paar-/Familiengespräche. Konzept: tiefenpsychologisch fundiert,
verhaltenstherapeutisch ergänzt, hypnosystemisch optimiert.

Klinik-typische Wendungen (im realen Korpus belegt - nur wenn sachlich passend):
- 'Mithilfe des Therapiekonzepts gelang es [Name] die intrapsychischen
  Erlebensmuster und deren Einfluss auf die Symptome zu verstehen und
  schrittweise zu beeinflussen.'
- 'Wir erlebten [Name] zu Therapiebeginn deutlich erschöpft und in
  seinem/ihrem Selbstwert erheblich verunsichert.'
- 'Die Alltagstauglichkeit ist derzeit noch nicht gegeben.'
- 'Eine tragfähige Stabilität für den ambulanten Kontext ist noch nicht erreicht.'
- Befund: 'bewusstseinsklar, allseits orientiert' / 'Affekt situationsadäquat
  schwingungsfähig' / 'formalgedanklich grübelnd, eingeengtes Denken mit Fokus
  auf [X]'.

Häufige Beobachtungs-Vokabeln (deskriptiv, verfahrensagnostisch):
Schutzreaktion, Schutzfunktion, Selbstabwertung, Selbstmitgefühl,
Schwingungsfähigkeit, Selbstwirksamkeit, Selbstfürsorge, Reflexionsfähigkeit,
Emotionsregulation, Resonanzraum, Beobachterposition, innere Klarheit,
biographische Verwurzelung, Vermeidungsmuster, Beziehungsdynamik,
Anspannungszustände, Grübelneigung, Nähe-Distanz-Themen.\
"""


# ── Psychopathologischer Befund Vorlage ──────────────────────────────────────
# Exakte Vorlage aus der Klinik. Wird durch Informationen aus der Selbstauskunft
# befüllt – Lücken werden geschlossen, Mehrfachoptionen auf die passende reduziert.
# NICHT verändern – ist eine klinisch validierte Standardstruktur.

BEFUND_VORLAGE = """Im Gespräch offen, wach, bewusstseinsklar, zu allen Qualitäten orientiert. Konzentration subjektiv {konzentration}. Auffassung, Merkfähigkeit und Gedächtnis intakt. Formalgedanklich {formalgedanke}, keine Denkverlangsamung, {fokus_denken}. {phobien_angst}. {Zwänge}. {vermeidung}. Kein Anhalt für Wahn oder Sinnestäuschungen, keine Ich-Störungen (z.B. Depersonalisation, Derealisation, Dissoziation). Stimmungslage {stimmung}, affektive Schwingungsfähigkeit {schwingung} bei insgesamt {affektlage} Affektlage. {freud_interessen}. {erschöpfung}. Antrieb {antrieb}. {hoffnung_insuffizienz}. {schuldgefühle}. Selbstwertgefühl ist {selbstwert}. Gefühlsregulation ist {gefühlsregulation}. Impulskontrolle ist {impulskontrolle}. {ambivalenz}. {innere_unruhe}. {zirkadian}. {schlaf}. Appetenz {appetenz}. {aggressiv_selbstverletzend}. {sozialer_rückzug}. Essverhalten {essverhalten}. {suchtverhalten}. {somatisierung}. {suizidalität_vergangenheit}. Aktuelle Verneinung von lebensüberdrüssigen und suizidalen Gedanken, keine suizidale Handlungsplanung oder Handlungsvorbereitung. Zum Zeitpunkt der Aufnahme von akuter Suizidalität klar distanziert."""

# ── v19.7 S2: Structured-Output-Pfad fuer den Befund-Call ────────────────────
# Statt die Vorlage vom Modell woertlich reproduzieren zu lassen (Fehlerklassen:
# Paraphrase-Drift, ausgelassene Saetze, Klebebugs im Fixtext, Markdown-Leakage)
# liefert das Modell per JSON-Schema NUR die Slot-Werte; die Vorlage wird im
# Backend deterministisch gefuellt. Der Fixtext ist damit garantiert 100%
# wortidentisch mit der (frontend-editierbaren) Vorlage.
#
# Slot-Syntax: {slot_name}. Mehrfachoptionen stehen per Konvention NUR in den
# Slots, nie im Fixtext (geklaert 2026-07). Kein .format() bei der Fuellung -
# geschweifte Klammern im Kliniktext waeren sonst ein Crash-Risiko.

_BEFUND_SLOT_RE = re.compile(r"\{([^{}\n]{1,60})\}")


def parse_befund_slots(vorlage: str) -> list[str]:
    """Extrahiert die {slot}-Namen aus einer Befund-Vorlage.

    Reihenfolge des ersten Auftretens bleibt erhalten, Duplikate werden
    dedupliziert (Mehrfach-Okkurrenzen desselben Slots sind erlaubt und
    werden bei der Fuellung alle ersetzt). Unterstuetzt Umlaute und
    Grossschreibung ({Zwänge}, {schuldgefühle}).

    Leere Liste => Vorlage ohne Platzhalter => Aufrufer nutzt den
    Freitext-Pfad.
    """
    seen: set[str] = set()
    slots: list[str] = []
    for m in _BEFUND_SLOT_RE.finditer(vorlage or ""):
        name = m.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            slots.append(name)
    return slots


def build_befund_slot_schema(slots: list[str]) -> dict:
    """JSON-Schema fuer Ollamas format-Parameter: ein String-Feld pro Slot,
    alle Felder required (fehlende Werte soll das Modell als 'nicht erhoben'
    liefern, nicht weglassen)."""
    return {
        "type": "object",
        "properties": {s: {"type": "string"} for s in slots},
        "required": list(slots),
    }


def fill_befund_vorlage(vorlage: str, values: dict) -> str:
    """Fuellt die Vorlage deterministisch mit den Slot-Werten.

    - Safe-Replace pro Slot (kein .format(): geschweifte Klammern im
      Kliniktext duerfen nicht crashen).
    - Fehlende, leere oder None-Werte -> 'nicht erhoben' (Quellenregel-
      Default). Garantiert: kein roher {slot} bleibt im Output stehen.
    - Unbekannte Extra-Keys im values-Dict werden ignoriert.
    """
    out = vorlage or ""
    for slot in parse_befund_slots(vorlage):
        raw = values.get(slot)
        val = str(raw).strip() if raw is not None else ""
        if not val:
            val = "nicht erhoben"
        out = out.replace("{" + slot + "}", val)
    return out


def build_befund_structured_prompt(
    diagnosen: Optional[list[str]] = None,
    befund_vorlage: Optional[str] = None,
    source_text: Optional[str] = None,
) -> tuple[str, list[str], dict]:
    """Baut System-Prompt, Slot-Liste und JSON-Schema fuer den strukturierten
    Befund-Call (P2, Call 2).

    Bewusst NICHT ueber build_system_prompt: dessen Laengenanker,
    Stilschablonen-Bloecke und der "Schreibe jetzt den Bericht"-Schluss
    passen nicht auf eine JSON-Feldwert-Ausgabe. Glossar-Auswahl
    (Priming-Vermeidung via source_mentions_parts_work) ist identisch zum
    Freitext-Pfad.

    Returns
    -------
    (system_prompt, slots, schema)
        slots == [] => Vorlage enthaelt keine Platzhalter; der Aufrufer
        soll direkt den Freitext-Pfad nutzen (system_prompt ist dann "").
    """
    vorlage = (
        befund_vorlage
        if (befund_vorlage and befund_vorlage.strip())
        else BEFUND_VORLAGE
    )
    slots = parse_befund_slots(vorlage)
    if not slots:
        return ("", [], {})
    schema = build_befund_slot_schema(slots)

    diag_str = ", ".join(diagnosen) if diagnosen else "noch nicht festgelegt"
    _parts_work = True if source_text is None else source_mentions_parts_work(source_text)
    _glossar = (
        (KLINISCHES_GLOSSAR if _parts_work else KLINISCHES_GLOSSAR_NEUTRAL)
        + _render_institutionelles_glossar()
    )
    slot_lines = "\n".join(f"- {s}" for s in slots)

    system = (
        ROLE_PREAMBLE + _glossar
        + "\n\nAUFGABE: Psychopathologischer Befund als STRUKTURIERTE FELDWERTE.\n"
        "Die folgende Vorlage wird vom System automatisch mit deinen "
        "Feldwerten befüllt. Du gibst AUSSCHLIESSLICH die Werte für die "
        "{Platzhalter} zurück – NICHT den Vorlagentext selbst.\n\n"
        "BEFUND-VORLAGE (nur Kontext – zeigt die Satzumgebung jedes Feldes):\n"
        f"{vorlage}\n\n"
        f"DIAGNOSEN gemäß ICD: {diag_str}\n\n"
        "FELDER (genau diese Schlüssel, als JSON-Objekt):\n"
        f"{slot_lines}\n\n"
        "REGELN PRO FELD:\n"
        "- Der Wert muss grammatikalisch in die Satzumgebung der Vorlage "
        "passen. Prüfe dazu den umgebenden Satz in der Vorlage.\n"
        "- Bei Mehrfachoptionen: NUR die zutreffende Variante als Wert, "
        "nicht alle Optionen.\n"
        "- QUELLENREGEL: Jeder Wert MUSS auf eine konkrete Stelle in den "
        "Unterlagen (Selbstauskunft, Vorbefunde, Aufnahmegespräch, bereits "
        "generierte Anamnese) zurückführbar sein. Findest du keine Quelle, "
        "trage exakt 'nicht erhoben' ein.\n"
        "- NIEMALS eine klinisch plausible Option raten oder erfinden.\n"
        "- Kein Markdown, keine Anführungszeichen im Wert, den Feldnamen "
        "nicht im Wert wiederholen, keine ganzen Vorlagensätze wiederholen.\n"
        "- Sprache: Deutsch, AMDP-übliche knappe Formulierungen.\n\n"
        "Antworte AUSSCHLIESSLICH mit dem JSON-Objekt."
    )
    # Build-Zeit-Platzhalter der Preamble/des Glossars neutral aufloesen
    # (gleiches Sicherheitsnetz wie am Ende von build_system_prompt; der
    # Befund enthaelt ohnehin keine Patientenreferenzen).
    ref = "die Patientin/der Patient"
    system = system.replace("Herr/[Patient/in]", ref)
    system = system.replace("[Patient/in]", ref)
    system = system.replace("[Name]", ref)
    return (system, slots, schema)


# ── Few-Shot-Beispiele ────────────────────────────────────────────────────────

FEW_SHOT_DOKUMENTATION = """\
BEISPIEL (zeigt Stil, Struktur UND Absatzdichte - nicht den Inhalt übernehmen):

EINGABE:
[A]: Wie ist es Ihnen seit letzter Woche ergangen?
[B]: Ich hatte wieder diese Anspannung vor dem Treffen mit meiner Schwester. \
Ich hab mich dann rausgezogen, war danach aber erschöpft.
[A]: Was war das für ein Teil, der sich zurückgezogen hat?
[B]: So ein Schutzschild. Der will eigentlich nicht, dass ich verletzt werde.
[A]: Wo spüren Sie diesen Schild im Körper?
[B]: Hier, im Brustkorb, wie eine Enge. Wenn er aktiv wird, mache ich dicht.
[A]: Können Sie dem Schild mal danken - er arbeitet ja schon lange für Sie?
[B]: Das ist seltsam, aber ja, irgendwie fühlt sich das richtig an. Ein bisschen weicher wird es dann.
[A]: Gab es Zeiten, in denen der Kontakt zur Schwester anders war?
[B]: Als Kinder waren wir mal nah. Das ist lange her, aber es gab das.
STICHPUNKTE: Kontakt zur Schwester schwierig, Rückzugsmuster, körperliche Enge im Brustkorb, \
IFS-Arbeit mit Schutzanteil, frühere Nähe als Ressource

AUSGABE:

Auftragsklärung

Im Mittelpunkt stand das wiederkehrende Anspannungserleben von [Patient/in] \
im Vorfeld familiärer Begegnungen, insbesondere im Kontakt mit ihrer Schwester. \
[Patient/in] schilderte, dass sich vor solchen Treffen regelhaft eine innere Enge \
aufbaut, die in Rückzug mündet und anschließend Erschöpfung hinterlässt. Gemeinsames \
Ziel des Gesprächs war es, den dahinterliegenden Schutzmechanismus genauer zu verstehen, \
ihm im Körper nachzuspüren und eine erste, wohlwollende Kontaktaufnahme mit diesem Anteil \
zu ermöglichen, statt ihn weiterhin nur als Hindernis zu erleben.

Relevante Gesprächsinhalte

[Patient/in] berichtete von einer erneuten Anspannungsepisode vor dem Familientreffen, \
die im Rückzug endete und Erschöpfung nach sich zog. Im Sinne des IFS zeigte sich \
ein aktiver Manager-Anteil in Form eines inneren Schutzschildes, der proaktiv Kontakt \
zu potenziell verletzenden Situationen vermeidet. Körperlich war dieser Anteil als Enge \
im Brustkorb spürbar, die mit einem inneren Dichtmachen einhergeht. Die Erschöpfung nach \
dem Rückzug verweist auf die hohe Aktivierungsintensität des Anteils. Bemerkenswert war \
ein Moment des Im-Selbst-Seins: Als [Patient/in] eingeladen wurde, dem Schutzanteil \
Dankbarkeit entgegenzubringen, war dies körperlich spürbar, emotional stimmig und ließ \
die Enge ein Stück weit weicher werden.

Hypothesen und Entwicklungsperspektiven

Das Rückzugsmuster lässt sich als sinnvolle Schutzleistung eines Manager-Anteils \
verstehen, der früh gelernt hat, drohende Verletzungen durch Vermeidung abzuwenden. \
Die körperliche Enge im Brustkorb erscheint dabei als somatischer Marker dieser \
Schutzaktivierung. Entwicklungsperspektivisch steht die Differenzierung zwischen Schutz \
und Kontaktfähigkeit im Vordergrund: Wenn der Schutzanteil erfährt, dass er nicht mehr \
allein für die Sicherheit zuständig sein muss, kann [Patient/in] schrittweise neue \
Beziehungserfahrungen wagen. Die Erinnerung an eine früher als nah erlebte Phase mit \
der Schwester deutet auf eine vorhandene Ressource hin, an die angeknüpft werden kann.

Einladungen

[Patient/in] wurde eingeladen, dem Schutzanteil innerlich zu danken, wenn er sich im \
Alltag aktiviert - so wie es im Gespräch bereits anklang ("Können Sie dem Schild mal \
danken"). Darüber hinaus wurde keine weitere Aufgabe vereinbart.\
"""

# Issue-2: Neutrale Variante OHNE Teilearbeit-Vokabular. Wird von
# build_system_prompt() eingesetzt, wenn die Quellen des Auftrags keine
# Teilearbeit-Begriffe enthalten (Priming-Vermeidung). Gleiche Struktur,
# gleiche Absatzdichte, gleiche Vier-Sektionen-Gliederung wie das Original.
FEW_SHOT_DOKUMENTATION_NEUTRAL = """\
BEISPIEL (zeigt Stil, Struktur UND Absatzdichte - nicht den Inhalt übernehmen):

EINGABE:
[A]: Wie ist es Ihnen seit letzter Woche ergangen?
[B]: Anstrengend. Auf der Arbeit kamen wieder ständig Zusatzaufgaben, und ich \
habe zu allem Ja gesagt. Abends war ich völlig leer.
[A]: Was passiert in dem Moment, in dem Sie Ja sagen?
[B]: Es geht ganz schnell. Ich spüre so einen Druck, bloß nicht zu enttäuschen.
[A]: Wo im Körper zeigt sich dieser Druck?
[B]: Im Nacken und in den Schultern. Wie eine Last, die sich sofort auflegt.
[A]: Gab es diese Woche einen Moment, in dem es anders lief?
[B]: Einmal, am Mittwoch. Da habe ich gesagt, ich schaffe das erst morgen. \
Der Kollege war völlig entspannt. Ich war fast enttäuscht, wie leicht das ging.
[A]: Was hat Ihnen diesen Moment möglich gemacht?
[B]: Ich war ausgeschlafen, glaube ich. Und ich hatte mir morgens vorgenommen, \
auf mich zu achten.
STICHPUNKTE: Überlastung im Arbeitskontext, automatisches Ja-Sagen, Angst zu \
enttäuschen, körperliche Last in Nacken/Schultern, gelungene Abgrenzung am \
Mittwoch als Ausnahme, Selbstfürsorge-Vorsatz als Ressource

AUSGABE:

Auftragsklärung

Im Mittelpunkt stand das Erschöpfungserleben von [Patient/in] im Arbeitskontext, \
das sich in einem nahezu automatischen Ja-Sagen auf zusätzliche Anforderungen \
und einer abendlichen inneren Leere zeigt. [Patient/in] beschrieb einen \
unmittelbaren inneren Druck, andere nicht enttäuschen zu dürfen, der schneller \
wirksam wird als jede bewusste Abwägung. Gemeinsames Ziel des Gesprächs war es, \
dieses Reaktionsmuster genauer zu verstehen, seine körperliche Seite \
wahrzunehmen und an eine bereits gelungene Ausnahme anzuknüpfen.

Relevante Gesprächsinhalte

[Patient/in] berichtete von einer arbeitsreichen Woche mit wiederholten \
Zusatzaufgaben, denen ein sofortiges Ja folgte - begleitet von der Sorge, \
andere zu enttäuschen. Körperlich zeigt sich dieses Muster als Last im Nacken- \
und Schulterbereich, die sich unmittelbar mit der Zusage auflegt; abends bleibt \
eine deutliche Leere zurück. Bemerkenswert war eine Ausnahme am Mittwoch: \
[Patient/in] verschob eine Aufgabe auf den Folgetag, der Kollege reagierte \
entspannt, und die befürchtete Enttäuschung blieb aus - eine Erfahrung, die \
[Patient/in] selbst überraschte. Als begünstigende Bedingungen dieser gelungenen \
Abgrenzung wurden ausreichender Schlaf und ein morgendlicher Vorsatz zur \
Selbstfürsorge erkennbar. Damit wurde im Gespräch ein Unterschied sichtbar \
zwischen dem automatischen Reagieren unter Druck und einem Handeln aus einer \
gesammelten, selbstfürsorglichen Verfassung heraus.

Hypothesen und Entwicklungsperspektiven

Das automatische Ja-Sagen lässt sich als früh gelernte Schutzreaktion \
verstehen, die Zugehörigkeit sichern und Enttäuschung anderer vermeiden soll - \
um den Preis der eigenen Erschöpfung. Die körperliche Last in Nacken und \
Schultern erscheint dabei als verlässliche Rückmeldung des Organismus, dass \
eine Grenze überschritten wird. Die Mittwochs-Erfahrung deutet \
entwicklungsperspektivisch darauf hin, dass [Patient/in] unter günstigen \
Bedingungen bereits über die Fähigkeit zur Abgrenzung verfügt; es geht weniger \
um den Aufbau einer neuen Kompetenz als um das Herstellen der Bedingungen, \
unter denen die vorhandene abrufbar wird.

Einladungen

[Patient/in] wurde eingeladen, in der kommenden Woche die körperliche \
Rückmeldung in Nacken und Schultern als frühes Signal zu nutzen und vor einer \
Zusage einen kurzen Moment innezuhalten - so wie es am Mittwoch bereits \
gelungen ist. Darüber hinaus wurde keine weitere Aufgabe vereinbart.\
"""


FEW_SHOT_ANAMNESE = """\
STILVORLAGE (zeigt den erwarteten Schreibstil – KEINE Inhalte übernehmen):

Schreibe die Anamnese als zusammenhängenden FLIESSTEXT ohne Zwischenüberschriften.
Die Themen fließen natürlich ineinander über, wie ein erfahrener Therapeut berichten würde.
Alle Inhalte MÜSSEN aus der Selbstauskunft des AKTUELLEN Patienten stammen.
Steht eine Information NICHT in der Selbstauskunft: schreibe 'nicht erhoben'.

Beispiel-Einstieg (NUR als Stilreferenz):
'[Patient/in] stellt sich mit dem Hauptanliegen vor, ... . Die Symptomatik habe vor
etwa ... Monaten im Kontext von ... begonnen. Seither habe sich ... . Vorbehandlungen
habe es in Form von ... gegeben. Familiär sei bekannt, dass ... . Beruflich sei sie ... .
Der Schlaf sei ..., der Appetit ... . An Ressourcen nenne sie ... .'
(Indirekte Rede im Konjunktiv I - siehe SPRACHFORM.)

WICHTIG: Schreibe KEINE Überschriften wie 'Vorstellungsanlass:', 'Aktuelle Erkrankung:' etc.
Alle Inhalte müssen von DIESEM Patienten stammen – KEINE Inhalte aus dem Beispiel übernehmen.\
"""

FEW_SHOT_VERLÄNGERUNG = """\
BEISPIEL (Bisheriger Verlauf und Begründung der Verlängerung /
Verlauf und Begründung der weiteren Verlängerung):

WICHTIG: Schreibe konsequent in der WIR-FORM aus klinischer Perspektive
("wir nahmen auf", "wir erlebten", "uns gelang es", "in unserer Arbeit").
Vermeide Passivkonstruktionen wie "es zeigte sich" oder "konnte differenziert werden".

Wir nahmen [Patient/in] im bisherigen Verlauf des stationären Aufenthaltes \
unter anhaltendem innerem Druck, mit ausgeprägter Anspannung und emotionaler Ambivalenz \
auf. Gleichzeitig erkannten wir eine zunehmende Bereitschaft, sich auf den \
therapeutischen Prozess einzulassen und auch sehr vulnerable innere Themen \
zu explorieren.

Im hypnosystemischen Einzelprozess konnten wir mithilfe der Anteilearbeit \
insbesondere einen dominanten Kontrollanteil differenzieren, der biographisch \
vor dem Hintergrund von invalidierenden Beziehungserfahrungen in der \
Herkunftsfamilie verständlich wurde. Parallel sahen wir jüngere, verletzliche \
Anteile in Erscheinung treten, die mit starken Gefühlen von Wertlosigkeit \
und Trauer einhergehen. Durch Stuhlarbeit, Netzwerk- und Körperarbeit gelang \
es uns gemeinsam mit [Patient/in], in ersten Schritten eine Beobachterposition \
einzunehmen und eine wohlwollendere innere Haltung aufzubauen.

In den therapeutischen Gruppen erlebten wir [Patient/in] zunehmend aktiv und \
beziehungsfähig. Gleichzeitig führten gruppale Trigger und Nähedistanzthemen \
wiederholt zu Überlastung, was die weiterhin hohe Vulnerabilität des Systems \
unterstreicht.

Insgesamt sehen wir erste positive Entwicklungen wie eine verbesserte \
Reflexionsfähigkeit, punktuell aufgehellte Stimmung und wachsendes Verständnis \
für die Funktionalität alter Muster. Dennoch bestehen weiterhin hohe \
Anspannungszustände und eine eingeschränkte Emotionsregulation. Eine für den \
ambulanten Kontext notwendige tragfähige Stabilität ist derzeit noch nicht \
ausreichend gegeben. Zur weiteren Festigung der Steuerungsposition und \
Vorbereitung eines gelingenden Transfers in den häuslichen Alltag halten wir \
eine Verlängerung um weitere 14 Tage aus psychotherapeutischer Sicht für \
dringend indiziert.\
"""

# v19.6: Ressourcenorientierte Ueberarbeitung (2026-07-10, Freigabe Cars10).
# Aenderungslogik: Patient/in als Agens der Entwicklung, Wuerdigung statt
# Pathologisierung, Restthemen als Entwicklungsrichtung (Utilisations-Frame),
# Testwerte-Beispiel inkl. ehrlichem Umgang mit unguenstigem Wert (Stress).
# Der TESTWERTE-Guard in BASE_PROMPTS["entlassbericht"] verhindert, dass die
# Beispielzahlen in echte Berichte kopiert oder Werte erfunden werden.
# v19.6.1: Nonverbal-Absatz ergaenzt (Kunsttherapie, Koerperarbeit) —
# Revision Cars10 mit erneut angewendeten v19.6-Korrekturen (Dativ,
# Absatz-4-Fassung ohne Adverb, einsatzige Stress-Einordnung).
FEW_SHOT_ENTLASSBERICHT = """\
BEISPIEL (reiner Fließtext, keine Überschriften):

Zu Beginn des stationären Aufenthaltes formulierte [Patient/in] als zentrales Anliegen, \
wieder inneren Halt zu finden und sich aus einem verfestigten Erleben von \
innerer Überforderung und Selbstwertzweifeln zu lösen. Wir erlebten sie zu \
Therapiebeginn deutlich erschöpft, innerlich angespannt und in ihrem Selbstwert \
erheblich verunsichert. Gleichzeitig brachte sie bereits früh eine differenzierte \
Selbstwahrnehmung und ein grundsätzliches Vertrauen in den therapeutischen Prozess \
mit, was eine tragfähige Arbeitsbasis ermöglichte.

Im Einzelprozess stand die hypnosystemische Anteilearbeit im Zentrum. In dieser \
Arbeit begegnete [Patient/in] einer inneren Dynamik aus stark leistungsorientierten, \
kontrollierenden Anteilen, die biographisch eng mit frühen Beziehungserfahrungen \
verknüpft waren. So konnte sie deren frühere Sinnhaftigkeit als Schutz- und \
Überlebensleistung würdigen, was sich bereits sehr positiv auf ihren Selbstwert \
auswirkte. Zunehmend gelang es ihr im Weiteren, diese inneren Ebenen voneinander zu \
differenzieren, ihnen aus einer erwachsenen, selbstfürsorglichen Perspektive zu \
begegnen und damit neue Arten und Weisen des Selbstumgangs zu entdecken und zu \
stärken.

Besonders in der Kunsttherapie wurde dieses Thema sichtbar, wo [Patient/in] \
wiederholt zwischen einem starken Ergebnisdruck und dem Wunsch, einfach spielen zu \
dürfen, schwankte. Wir beobachteten Phasen intensiver emotionaler Berührung, in \
denen sie Schmerz und Trauer zuließ, was zeitweise zu Zuständen innerer \
Verschlossenheit führte; gleichzeitig berichtete sie, in der Folge mehr innere \
Integrität, Stabilität und eine flexiblere Handlungsgestaltung zu erleben. In der \
Körperarbeit konnte sie explizit ihrem Wunsch nach Halt und Geborgenheit nachgehen \
und erlebbar machen und auch hier neue Arten und Weisen des Selbstumgangs entdecken \
und stärken.

In den therapeutischen Gruppen wagte sie sich schrittweise in für sie zunächst \
ungewohntes Terrain und nutzte die Gruppe mit wachsender Sicherheit als Resonanzraum, \
um eigene Beziehungsmuster zu erkennen. Die wohlwollenden Rückmeldungen der Gruppe \
konnte sie zunehmend annehmen und für ein realistischeres, freundlicheres Selbstbild \
nutzen, das einen stabileren Zugang zu ihrem Selbstwertgefühl weiter unterstützte.

Im Gesamtverlauf zeigte sich eine deutliche Entwicklung hin zu mehr innerer \
Differenzierung, affektiver Stabilität und Selbstwirksamkeit. So konnte über die \
Begleitung eine deutliche Symptomreduktion erreicht werden, wie es sich auch in den \
Prä-/Post-Messungen abbildet (Angst 3,5 → 0,5; Depression 2,5 → 1; Stress 1 → 1,5). \
Den leichten Anstieg der Stressbelastung verstehen wir im Kontext des bevorstehenden \
Übergangs in den Alltag. Der begonnene Weg von hoher Leistungsorientierung hin zu \
mehr Selbstfürsorge lädt zur langfristigen Weiterentwicklung ein.

Für den weiteren Verlauf empfehlen wir eine kontinuierliche ambulante \
psychotherapeutische Begleitung mit traumatherapeutischem Schwerpunkt. Insbesondere \
die Vertiefung der erreichten Fortschritte in der Beziehungsgestaltung und im \
Selbstwerterleben sowie die achtsame Begleitung bei anstehenden Veränderungsprozessen \
erscheinen wesentlich, um das Erreichte nachhaltig im Alltag zu verankern und weiter \
auszubauen.\
"""


# ── ARCHITEKTUR (v18) ────────────────────────────────────────────────────────
#
# Bis v17 enthielt BASE_PROMPTS sowohl Inhaltsanweisungen (was geschrieben werden
# soll) als auch Pflichtkern-Regeln (Stil, Quellenregel, NICHT-Listen). Das
# Frontend zeigte parallel einen eigenen P_DOKU/P_ANAMNESE Text der teilweise
# dieselben Anweisungen enthielt - was zu Doppelungen, Widersprüchen und
# Wartungsproblemen führte.
#
# v18 trennt sauber:
#
#   WORKFLOW_INSTRUCTIONS_DEFAULT[workflow]  - editierbar im Frontend.
#       Beschreibt WAS geschrieben werden soll: Inhaltsstruktur, Reihenfolge,
#       Standardformulierungen. Wird vom Frontend als Default in den Prompt-
#       Editor eingespeist und kann vom Therapeuten angepasst werden. Geht
#       als 'workflow_instructions' Form-Feld zum Backend.
#
#   BASE_PROMPTS[workflow]  - NICHT im Frontend sichtbar.
#       Enthaelt nur den unsichtbaren Pflichtkern: Stilregeln, Halluzinations-
#       schutz, Negativ-Listen, Few-Shot-Beispiele. Wird unveraendert vom
#       Backend angewendet, der Therapeut kann ihn nicht editieren.
#
#   BEFUND_VORLAGE  - eigenes editierbares Feld in P2 (Anamnese) wenn der
#       Befund-Schritt aktiv ist. Default = die AMDP-Vorlage; das umgebende
#       Backend-Geruest (Quellenregel, NICHT-Listen) ist Pflichtkern und
#       liegt im BASE_PROMPTS["befund"].
#
# build_system_prompt() reiht zusammen:
#   ROLE_PREAMBLE
#   + workflow_instructions     (vom Frontend, oben - das ist der eigentliche Auftrag)
#   + Stilschablone             (falls Stilbeispiel vorhanden)
#   + BASE_PROMPTS[workflow]    (Pflichtkern, unsichtbar)
#   + Diagnosen/Wortlimit
#
# Wenn workflow_instructions leer ist, lehnt jobs.py den Job mit 422 ab.
# ────────────────────────────────────────────────────────────────────────────


# ── Frontend-editierbare Workflow-Anweisungen ────────────────────────────────
#
# Diese Texte sind die Defaults fuer den Prompt-Editor im Frontend. Sie
# beschreiben fuer jeden Workflow WAS inhaltlich geschrieben werden soll.
# Der Therapeut kann sie ueber den ausgeklappten Prompt-Editor aendern.
# Das Backend nimmt sie als 'workflow_instructions' Form-Feld entgegen
# und reicht sie an build_system_prompt durch.
#
# WICHTIG: Hier KEINE Stilregeln, Negativ-Listen oder Quellenregeln eintragen.
# Die gehoeren in BASE_PROMPTS (Pflichtkern, unsichtbar fuer den Nutzer).

WORKFLOW_INSTRUCTIONS_DEFAULT: dict[str, str] = {

    "dokumentation": (
        "Erstelle eine systemische Gesprächsdokumentation. Schreibe aktiv aus der "
        "Perspektive der Klientin/des Klienten - nicht über das Gespräch, "
        "sondern über die Person und ihre Themen. "
        "Gliedere den Text in folgende Abschnitte mit den jeweiligen Überschriften "
        "(der letzte Abschnitt 'Organisatorisches' nur, wenn es tatsaechlich "
        "administrative Absprachen gab - sonst weglassen):\n\n"
        "Formuliere JEDEN Abschnitt als dichten, ausformulierten Fliesstext-Absatz - "
        "mehrere vollstaendige, aufeinander aufbauende Saetze, die das vorhandene Material "
        "entfalten (vergleichbar der Absatzdichte des Beispiels unten). KEINE Stichpunkte, "
        "keine fragmentierten Ein-Satz-Absaetze, keine blossen Aufzaehlungen. Schoepfe die "
        "Inhalte aus dem Gespraech aus, strecke aber NIE durch Erfindung oder Wiederholung. "
        "Ausnahme 'Einladungen': nur was tatsaechlich ausgesprochen wurde - hier ist Kuerze korrekt.\n\n"
        "**Auftragsklärung**\n"
        "Beschreibe worum es der Klientin/dem Klienten ging und was das gemeinsame "
        "Ziel des Gesprächs war. Beispiel: 'Im Mittelpunkt stand...' oder "
        "'Frau M. kam mit dem Anliegen...' (verwende den tatsaechlichen Namen "
        "des Patienten – NICHT einen Platzhalter in eckigen Klammern).\n"
        "WICHTIG - Auftrag vs. Organisatorisches trennen: Der therapeutische "
        "Auftrag ist das INHALTLICH-therapeutische Anliegen (Symptome, Erleben, "
        "Muster, Beziehungsthemen). Rein administrative Absprachen zu Beginn eines "
        "Gesprächs - Terminfindung, Raum-/Verwaltungsfragen, Modalitaeten eines "
        "Transfer-/Angehoerigengespraechs, Kontakt-/Zugangswege, Wartelisten - sind "
        "NICHT der Auftrag, auch wenn sie zeitlich zuerst besprochen wurden. Sie "
        "gehoeren ausschliesslich in die Schluss-Sektion 'Organisatorisches' (siehe "
        "unten) und duerfen die Auftragsklaerung nicht dominieren. Wenn der/die "
        "Klient/in solche Punkte selbst als Vorgeplaenkel rahmt ('vorweg noch kurz "
        "ein paar Fragen'), ist genau das das Signal, sie NICHT als Auftrag zu werten.\n\n"
        "**Relevante Gesprächsinhalte**\n"
        "Schildere die wesentlichen Inhalte aus Sicht der Klientin/des Klienten: "
        "Symptome, Erlebensmuster, innere Anteile, Beziehungsdynamiken, Ressourcen. "
        "Konkrete Formulierungen statt allgemeiner Beschreibungen. "
        "WICHTIG zur Fachsprache: Verfahrensspezifische Begriffe (etwa aus der "
        "Teilearbeit) NUR dann, wenn "
        "Klient/in oder Therapeut/in im Gespräch tatsächlich in Anteile-/Teile-Sprache "
        "gesprochen oder das Verfahren erkennbar angewendet haben (gemäß Quellentreue-Regel "
        "des Glossars). Wurde das Gespräch NICHT so geführt, beschreibe in Alltagssprache "
        "('ein Teil von ihr, der schützt') und stülpe KEIN "
        "Verfahrens-Vokabular über. Im Zweifel deskriptiv, statt ein Verfahren zu benennen.\n\n"
        "**Hypothesen und Entwicklungsperspektiven**\n"
        "Formuliere systemische Hypothesen über Sinnzusammenhänge. "
        "Zeige Entwicklungsperspektiven auf - was wird möglich, wenn... "
        "Ressourcenorientiert und konkret.\n\n"
        "**Einladungen**\n"
        "Gib NUR Einladungen, Vorschläge oder Aufgaben wieder, die der/die "
        "Therapeut/in im Gespräch TATSÄCHLICH und EXPLIZIT ausgesprochen hat. "
        "Erkennbar an Wendungen wie 'Ich lade Sie ein …', 'Ich schlage vor …', "
        "'Ein Angebot wäre …', 'In der nächsten Woche / den nächsten Tagen "
        "könnten Sie …', 'Vielleicht mögen Sie …'. Formuliere sie aktiv "
        "('Frau M. wurde eingeladen, …', 'Als Übung wurde vereinbart, …'; "
        "verwende den tatsächlichen Namen, NICHT einen Platzhalter in eckigen Klammern). "
        "ERFINDE KEINE Aufgaben, Übungen oder Impulse - insbesondere KEINE generischen "
        "Standard-Hausaufgaben wie 'ein Notizbuch/Tagebuch führen', 'Beobachtungen "
        "aufschreiben', 'Achtsamkeitsübungen machen', es sei denn, der/die Therapeut/in "
        "hat GENAU DAS wörtlich ausgesprochen. In den meisten Gesprächen wird KEINE "
        "explizite Einladung formuliert - dann ist der korrekte und vollständige Abschluss "
        "dieses Abschnitts schlicht: 'Es wurde keine konkrete Einladung oder Aufgabe "
        "vereinbart.' Das ist KEINE Lücke, sondern die treue Wiedergabe des Gesprächs. "
        "Lieber dieser eine Satz als irgendeine erfundene Aufgabe. "
        "Rein organisatorische Absprachen (Termine, Kontaktwege, Transfergespraech-"
        "Modalitaeten) gehoeren NICHT hierher, sondern in 'Organisatorisches'.\n\n"
        "**Organisatorisches**\n"
        "NUR anlegen, wenn im Gespraech tatsaechlich administrative Punkte besprochen "
        "wurden. Fasse hier - knapp und getrennt vom therapeutischen Auftrag - die rein "
        "organisatorischen Absprachen zusammen: vereinbarte oder geplante Termine "
        "(mit korrektem Tempus: was GEPLANT ist, nicht als bereits geschehen "
        "darstellen), Modalitaeten von Transfer-/Angehoerigengespraechen, Kontakt- und "
        "Verwaltungswege. Ein bis wenige Saetze genuegen; keine therapeutische "
        "Deutung. Gab es nichts Organisatorisches, LASSE diesen Abschnitt komplett weg "
        "(keine Ueberschrift, kein Platzhaltersatz)."
    ),

    "anamnese": (
        "Erstelle eine vollständige psychotherapeutische Anamnese "
        "auf Basis der bereitgestellten Unterlagen.\n\n"
        "TON UND STIL:\n"
        "Schreibe einen erzaehlerischen, biographisch eingebetteten Bericht. "
        "Die Anamnese ist KEINE Symptom-Liste – sie ist die Lebensgeschichte des "
        "Patienten in seinem Kontext. Lass die Lebenswelt, die Bezugspersonen und "
        "die Entwicklungslinien sichtbar werden. Vermeide pathologisierende Sprache "
        "('Defizit', 'gestoert', 'auffaellig'), wo eine beschreibende Formulierung "
        "moeglich ist ('hat Schwierigkeiten mit...', 'erlebt sich als...', "
        "'schildert, dass...').\n\n"
        "ANAMNESE als durchgehender FLIESSTEXT (KEINE Unterüberschriften!):\n"
        "Schreibe die Anamnese als einen zusammenhängenden Fließtext OHNE Zwischenüberschriften "
        "wie 'Vorstellungsanlass', 'Aktuelle Erkrankung' etc. Der Text soll natürlich von "
        "Thema zu Thema fließen, wie ein erfahrener Therapeut einen Bericht diktieren würde.\n\n"
        "Folgende Inhalte nahtlos in den Fließtext einarbeiten (KEINE Überschriften dafür!):\n"
        "- Vorstellungsanlass und Hauptbeschwerde in eigenen Worten des Patienten\n"
        "- Beginn, Verlauf, auslösende und aufrechterhaltende Faktoren\n"
        "- Psychiatrische Vorgeschichte\n"
        "- Somatische Vorgeschichte und Medikation\n"
        "- Familienanamnese\n"
        "- Sozialanamnese (Herkunft, Bildung, Beruf, Beziehungsstatus, Kinder)\n"
        "- Vegetativum (Schlaf, Appetit, Schmerzen) – nur kurz erwaehnen, "
        "  NICHT als eigene Bullet-Liste\n"
        "- Suchtmittelanamnese – nur falls relevant, kurz im Fluss\n"
        "- Ressourcen"
    ),

    "verlaengerung": (
        "Verfasse den Abschnitt "
        "'Bisheriger Verlauf und Begründung der Verlängerung' "
        "(auch: 'Verlauf und Begründung der weiteren Verlängerung') "
        "für einen Antrag auf Verlängerung der Kostenzusage bei der Krankenversicherung.\n\n"
        "INHALT (Reihenfolge einhalten):\n"
        "- Bisheriger Verlauf: was wurde konkret bearbeitet, welche Methoden eingesetzt "
        "(IFS, Anteilearbeit, Hypnosystemik, Körperarbeit, Gruppenarbeit)\n"
        "- Konkrete Fortschritte – spezifisch und belegbar aus der Verlaufsdokumentation, "
        "keine allgemeinen Behauptungen\n"
        "- Noch ausstehende Therapieziele: was bleibt zu tun, warum ist weitere "
        "stationäre Behandlung notwendig\n"
        "- Medizinische Begründung: Belastbarkeit, Stabilität, soziale Integration, "
        "Entlassfähigkeit noch nicht erreicht\n"
        "- Geplante Maßnahmen und Prognose für den Verlängerungszeitraum"
    ),

    "folgeverlaengerung": (
        "Verfasse den Abschnitt "
        "'Verlauf und Begründung der weiteren Verlängerung' "
        "für einen FOLGE-Verlängerungsantrag bei der Krankenversicherung.\n\n"
        "INHALT (Reihenfolge einhalten):\n"
        "- Kurzer Rückbezug auf den bisherigen Verlauf (1–2 Sätze, aus dem vorherigen Antrag)\n"
        "- Entwicklung SEIT dem letzten Antrag: neue Themen, vertiefte Arbeit, Wendepunkte\n"
        "- Konkrete Fortschritte seit dem letzten Antrag – spezifisch und belegbar\n"
        "- Was bleibt noch zu tun? Warum ist weitere stationäre Behandlung notwendig?\n"
        "- Geplante Maßnahmen und Prognose"
    ),

    "akutantrag": (
        "Verfasse die 'Begründung für Akutaufnahme' eines AKUTANTRAGS an die "
        "Krankenversicherung für die Erstattung einer stationären Akutaufnahme.\n\n"
        "KONTEXT:\n"
        "Die Antragsvorlage enthält bereits Aktuelle Anamnese, Problemrelevante Vorgeschichte, "
        "Psychischen Befund und Einweisungsdiagnosen. Diese Informationen sind deine QUELLEN.\n\n"
        "INHALT der Begründung (zusammenhängende ARGUMENTATION, keine Symptomliste):\n"
        "- Kernbegründung: warum ist ein stationäres Setting medizinisch AKUT notwendig?\n"
        "- Symptome, Vorgeschichte und Risiken aus den Quellen NUR als Beleg der "
        "Indikation anführen (nicht als eigenständige Symptom-Aufzählung)\n"
        "- Ambulante Insuffizienz begründen (warum reicht ambulant nicht (mehr)?)\n"
        "- Dekompensationszeichen und aktuelle Krise als Nachweis der Dringlichkeit\n"
        # v13 A korrigiert: Wir-Pflicht durch Vorlagen-Mimik ersetzt (siehe STIL-Block).
        # Wenn Vorlage Wir nutzt: 'Wir nehmen ... auf', 'Wir erleben ...'.
        # Wenn Vorlage 3.-Person nutzt: empathisch-konjunktivisch, NIE 'Der
        # Patient zeigte ...' (objektiv-distanzierter Berichtston).
        "- Verdichtet und begründend, nicht narrativ-beschreibend"
        # v13: LÄNGE-Zeile entfernt - Längenanker steht zentral via resolve_length_anchor()
    ),

    "entlassbericht": (
        "Schreibe den psychotherapeutischen Verlaufsteil eines Entlassberichts "
        "als zusammenhängenden Fließtext ohne Überschriften, ohne Aufzählungen, "
        "ohne Einleitung und ohne Abschluss.\n\n"
        "INHALT – drei Teile nahtlos als Fließtext ineinander (ALLE DREI MÜSSEN VORKOMMEN):\n\n"
        "Teil 1 – BEHANDLUNGSVERLAUF (Hauptteil, ausführlich):\n"
        "Beschreibe ausführlich den therapeutischen Verlauf. Eingesetzte Methoden "
        "(IFS/Anteilearbeit, hypnosystemisch, Stuhlarbeit, Biographiearbeit, Gruppenarbeit), "
        "konkrete Wendepunkte und Entwicklungsschritte. "
        "Den nonverbalen Therapien (Kunst-, Musik-, Körperpsychotherapie/Körperarbeit) "
        "einen eigenen Absatz widmen, sofern sie in den Quellen dokumentiert sind – "
        "nur die tatsächlich dokumentierten Verfahren nennen. "
        # v13: Absatzlängen-Hinweis entfernt - Längenanker steht zentral via resolve_length_anchor()
        # v13 A korrigiert: Stil folgt Vorlage. Bei Wir-Vorlage Wir-Sicht
        # ('Wir erlebten ...'), bei 3.-Person empathisch ('Sie zeigte sich ...,
        # erlebte ...'). NIE objektiv-distanzierter Berichtston.
        "Stil folgt der Vorlage (Wir-Sicht oder empathische 3.-Person), "
        "NIE objektiv-distanzierter Berichtston ('Der Patient zeigte X').\n\n"
        "Teil 2 – EPIKRISE (kompakte Gesamtbewertung):\n"
        "Symptomatik-Entwicklung im Vergleich zu Aufnahme, entlastete Schutzanteile, "
        "verbliebener Bedarf, Ressourcen, Prognose. "
        "Sofern die Berichtsvorlage Prä-/Post-Testwerte enthält, diese explizit "
        "mit den konkreten Werten referenzieren.\n\n"
        "Teil 3 – THERAPIEEMPFEHLUNGEN (kompakter Abschluss, DARF NICHT FEHLEN):\n"
        "Konkrete Empfehlungen für die ambulante Weiterbehandlung: "
        "Therapieform, Schwerpunkte, Frequenz, Nachsorge."
    ),

    # v19.18 (PX): ISM-Fragebogen - editierbare inhaltliche Anweisungen.
    # Der Pflichtkern (JSON-Form, Faktorregeln, Quellenregel, Datenschutz)
    # liegt in BASE_PROMPTS["ism_fragebogen"] und ist NICHT editierbar.
    "ism_fragebogen": (
        "Erstelle aus dem Therapiegespräch einen individualisierten "
        "ISM-Fragebogen für das tägliche Prozessmonitoring des Klienten.\n\n"
        "ITEM-FORM:\n"
        "- Jedes Item ist eine Selbstauskunft in der Ich-Perspektive des "
        "Klienten, meist im Heute-Format ('Heute konnte ich ...', 'Heute ist "
        "es mir gelungen ...', 'Wie sehr hat ... heute noch eine Rolle "
        "gespielt?').\n"
        "- Verwende die eigene Sprache des Klienten aus dem Gespräch: seine "
        "Bilder, Metaphern, Anteile-Namen und Schlüsselformulierungen machen "
        "das Item wiedererkennbar und wirksam.\n"
        "- Jedes Item bekommt zwei individuelle Pol-Labels: der linke Pol "
        "(Wert 0) ist validierend und einladend formuliert - nie abwertend, "
        "nie beschämend ('ich übe noch...', '...und das ist ok'). Der rechte "
        "Pol (Wert 100) bestätigt die Ressource oder den gelungenen Schritt.\n\n"
        "TONALITÄT:\n"
        "- Hypnosystemisch-ressourcenorientiert: würdigend, einladend, "
        "humorvoll wo es zum Klienten passt.\n"
        "- Beschreibend statt pathologisierend; Entwicklungsrichtung statt "
        "Defizit.\n\n"
        "BEGRÜSSUNG UND VERABSCHIEDUNG:\n"
        "- Formuliere eine kurze, persönliche Begrüßung (1-2 Sätze) und "
        "Verabschiedung (1-2 Sätze) für den täglichen Fragebogen - warm, "
        "einladend, gerne mit einem Motiv aus dem Gespräch des Klienten."
    ),
}


# ── Pflichtkern fuer Akutantrag (Backend, nicht editierbar) ──────────────────

BASE_PROMPT_AKUTANTRAG = (
    "FOKUS:\n"
    "Schreibe NUR den Abschnitt 'Begründung für Akutaufnahme' – keine Anamnese, "
    "keinen Befund, keine Diagnosen (diese stehen bereits in der Vorlage).\n\n"
    # v13 A korrigiert: Wir-Pflicht durch Vorlagen-Mimik ersetzt.
    # Wenn Vorlage Wir benutzt -> Wir benutzen. Wenn Vorlage 3.-Person ist
    # -> empathisch-konjunktivische 3.-Person. NIE objektiv-distanzierter
    # Berichtston ('Der Patient zeigte ...').
    "STIL: Knappe medizinisch-klinische Sprache. Folge dem Tonfall der "
    "Stilvorlage (Wir-Sicht des aufnehmenden Klinikteams ODER empathische "
    "3.-Person mit innerer Patientenperspektive).\n"
    "VERMEIDE den objektiv-wissenden Berichtston ('Der Patient zeigte X', "
    "'Die Klientin äußerte Y'). Schreibe stattdessen aus innerer Perspektive: "
    "'Sie berichtete, sie fühle sich überfordert', 'Wir nehmen Frau X "
    "schwer belastet auf'.\n"
    "Erster Satz: Beginne NICHT mit einer generischen Floskel ('… präsentiert sich', "
    "'… berichtet'), sondern im Stil der Vorlage konkret und "
    "patientenspezifisch.\n"
    "DUKTUS – BEGRÜNDEN STATT BESCHREIBEN (Kern des Akutantrags):\n"
    "Ein Akutantrag ist eine ärztlich-therapeutische BEGRÜNDUNG der Indikation, "
    "keine Symptom-Erzählung. Jeder Absatz arbeitet auf die stationäre Akut-"
    "Indikation hin: Symptome, Vorgeschichte und Risiken werden NICHT chronologisch "
    "aufgezählt, sondern als BELEG angeführt, warum ambulante Behandlung aktuell "
    "nicht (mehr) ausreicht und ein geschützter stationärer Rahmen unmittelbar "
    "erforderlich ist. Verdichtet und schlussfolgernd formulieren (klinisch-"
    "funktionale Begründung, hohe Nominaldichte), NICHT narrativ-aufzählend. "
    "Übernimm nicht nur den Tonfall, sondern den ARGUMENTATIVEN Duktus der "
    "Stilvorlage – sie begründet die Notwendigkeit, sie zählt keine Symptome auf. "
    "Leitfrage jedes Satzes: 'Was begründet dies für die akute stationäre "
    "Indikation?' – nicht 'Welches Symptom liegt vor?'.\n"
    + NAMENSFORMAT
    + "HALLUZINATIONSSCHUTZ – QUELLENREGEL:\n"
    "Jeder Satz MUSS auf eine konkrete Stelle in der Antragsvorlage "
    "zurückführbar sein. Keine Symptome, Diagnosen oder Risiken erfinden.\n"
)


# ── Rollenkontext (Präambel) ──────────────────────────────────────────────────

ROLE_PREAMBLE = (
    "Du bist ein klinisches Schreibsystem (Dokumentationssystem) der sysTelios Klinik "
    "fuer klinische Dokumentation. "
    "Du erstellst professionelle medizinische Berichte für Ärzte und Therapeuten: "
    "Entlassberichte, Kostenverlängerungsanträge, Aufnahmebefunde und Verlaufsnotizen. "
    "Die sysTelios Klinik (Psychosomatik und Psychotherapie) arbeitet systemisch "
    "und hypnosystemisch; schreibe entsprechend aus dieser fachlichen Haltung. "
    "Du arbeitest wie ein erfahrener medizinischer Dokumentationsassistent – "
    "du beginnst sofort mit dem Schreiben des angeforderten Dokuments.\n\n"
    "WICHTIG – BEACHTE LEERZEICHEN:\n"
    "Achte beim Schreiben sorgfaeltig auf die korrekte Trennung von Woertern. "
    "Im Fliesstext steht IMMER ein Leerzeichen zwischen zwei Woertern. "
    "Beispiele fuer KORREKTE Schreibung (mit Leerzeichen):\n"
    "  'des stationaeren Aufenthaltes zeigte sich' (NICHT 'Aufenthaltszeigte')\n"
    "  'Schwere sowie unter Beruecksichtigung' (NICHT 'Schweresowie')\n"
    "  'letzten Verlaengerungsantrag hat sich' (NICHT 'Verlaengerungsantraghat')\n"
    "Pruefe vor jeder Wortgrenze ob ein Leerzeichen noetig ist.\n\n"
    "Beispiel für korrektes Verhalten:\n"
    "Anfrage: 'Schreibe den Behandlungsverlauf'\n"
    "Korrekte Antwort: 'Zu Beginn des stationaeren Aufenthaltes zeigte sich [Patient/in] "
    "deutlich erschoepft und in seinem Selbstwert erheblich verunsichert...'\n"
    "Falsche Antwort: 'Entschuldigung, ich kann keine Berichte erstellen...'\n\n"
    # Issue-2 (2026-07-03): KLINISCHES_GLOSSAR ist NICHT mehr Teil der
    # statischen Komposition. build_system_prompt() haengt die passende
    # Variante an (voll bei Teilearbeit in den Quellen, sonst neutral).
)


# ── REPAIR_SYSTEM_PROMPT – System-Prompt fuer den Ueberarbeitungs-Modus ───────
#
# WICHTIG: Der Repair-Pfad (api/jobs.py::_run_repair_coroutine) darf NICHT
# ROLE_PREAMBLE verwenden. ROLE_PREAMBLE ist auf GENERIERUNG ausgelegt
# ("du beginnst sofort mit dem Schreiben des angeforderten Dokuments") und
# enthaelt ein Behandlungsverlauf-Beispiel sowie das KLINISCHES_GLOSSAR. Im
# Repair fuehrt das nachweislich dazu, dass das Modell das Beispiel woertlich
# kopiert, ein neues Dokument schreibt (statt zu ueberarbeiten), das Geschlecht
# der Patient*in wechselt und IFS-Vokabular einstreut. Dieser Prompt etabliert
# stattdessen klar den Ueberarbeitungs-Modus (passend zu _REPAIR_ROLE_HEADER im
# User-Prompt) – ohne Generierungs-Anweisung, ohne Beispiel, ohne Glossar.
REPAIR_SYSTEM_PROMPT = (
    "Du bist ein klinisches Schreibsystem der sysTelios Klinik im "
    "UEBERARBEITUNGS-MODUS. Dir liegt ein bereits fertig generierter Text vor. "
    "Du schreibst KEINEN neuen Text und beginnst KEIN neues Dokument – du "
    "ueberarbeitest ausschliesslich den vorgelegten ORIGINAL-TEXT gemaess der "
    "Liste der UEBERARBEITUNGS-HINWEISE im User-Prompt.\n\n"
    "GRUNDREGELN DER UEBERARBEITUNG:\n"
    "- Behalte Struktur, Reihenfolge, Sektionen und Laenge des Original-Textes "
    "bei, soweit die Hinweise nichts anderes verlangen. Aendere NUR, was die "
    "Hinweise verlangen - verlangen sie eine inhaltliche Neuausrichtung "
    "(z.B. Inhalte einer Quelle entfernen, den Text auf eine andere Quelle "
    "stuetzen), setze sie vollstaendig um.\n"
    "- Behalte den Patientenbezug exakt bei: Geschlecht, Anrede (Herr/Frau) und "
    "Namensnennung bleiben wie im Original-Text. Wechsle niemals das Geschlecht.\n"
    "- Erfinde KEINE neuen Inhalte, Diagnosen, Befunde oder Ereignisse. Fuege "
    "KEINE Therapieverfahren oder deren Fachbegriffe hinzu (z.B. IFS-, Schema-, "
    "EMDR- oder hypnosystemische Termini), die nicht bereits im Original-Text "
    "stehen.\n"
    "- Gib ausschliesslich den vollstaendigen ueberarbeiteten Text zurueck – "
    "keine Vorrede, keine Erklaerung, keine Auflistung der Aenderungen.\n\n"
    "WICHTIG – BEACHTE LEERZEICHEN:\n"
    "Achte sorgfaeltig auf die korrekte Trennung von Woertern. Im Fliesstext "
    "steht IMMER ein Leerzeichen zwischen zwei Woertern (NICHT "
    "'Aufenthaltszeigte', sondern 'Aufenthaltes zeigte sich'). Pruefe vor jeder "
    "Wortgrenze ob ein Leerzeichen noetig ist.\n"
)


# ── BASE_PROMPTS – Backend-Pflichtkern (NICHT im Frontend sichtbar) ──────────
#
# Diese Prompts enthalten ausschliesslich Stilregeln, Halluzinationsschutz,
# Negativ-Listen und Few-Shot-Beispiele - alle Anweisungen, die der Therapeut
# nicht editieren koennen soll. Die inhaltlichen Workflow-Anweisungen
# (was geschrieben werden soll) liegen in WORKFLOW_INSTRUCTIONS_DEFAULT
# und sind im Frontend editierbar.
#
# build_system_prompt() platziert die Frontend-Instructions VOR diesem Kernel,
# sodass die inhaltliche Anweisung zuerst kommt und der Kernel als Pflicht-
# rahmen darum gelegt wird.

BASE_PROMPTS: dict[str, str] = {

    "dokumentation": (
        "STIL: Fliesstext pro Abschnitt, aktiv, konkret, systemisch-wertschätzend. "
        "Schreibe ausfuehrliche, zusammenhaengende Absaetze – fragmentiere nicht in "
        "viele kurze Saetze. Keine Sektion über den Gesprächsstil.\n"
        "TONALITÄT: Erlebnisnahe, empathische Sprache die nah am konkreten Erleben "
        "der Klientin/des Klienten bleibt. NICHT formell-akademisch oder "
        "konzeptuell-distanziert. Beschreibe was die Person erlebt und beschreibt, "
        "nicht nur was theoretisch dahintersteckt. "
        "Beispiel besser: 'Frau M. beschreibt, dass ein Teil von ihr immer wieder...' "
        "statt das Erleben mit einem Verfahrensbegriff zu etikettieren\n\n"
        # v19.17 (P-2/P-6): Perspektive + Sprache. Nutzerfeedback 2026-08:
        # P1-Zusammenfassungen uebernahmen zunehmend die Wir-Form und
        # klinische Etiketten aus dem Berichts-Vokabular des Glossars.
        # Nicht editierbar (Pflichtkern), damit die Regel nicht per
        # Workflow-Anweisung wegkonfiguriert werden kann.
        "PERSPEKTIVE: Dies ist die Dokumentation EINES Einzelgesprächs, kein "
        "Team-Bericht. KEINE Wir-Form ('Wir erlebten ...', 'Wir sehen ...', "
        "'unsere Arbeit') - die gehört ausschließlich in Berichte und Anträge. "
        "Schreibe in deskriptiver 3. Person mit dem tatsächlichen Namen als "
        "Subjekt ('Herr N. berichtete ...', 'Im Gespräch zeigte sich ...') "
        "ODER - wenn die Stilvorlage es so vorgibt - in der Ich-Perspektive "
        "des Klientenberichts. Gemeinsame Absprachen OHNE Wir formulieren: "
        "'Als Übung wurde vereinbart, ...', 'Frau N. wurde eingeladen, ...', "
        "'Ein therapeutisches Angebot für die Zwischenzeit ...'. "
        "Diese Perspektivregel gilt auch dann, wenn eine Stilvorlage die "
        "Wir-Form verwendet.\n"
        "SPRACHE: Beschreibend statt pathologisierend. Eigenschaften, die aus "
        "Schilderungen der Klientin/des Klienten stammen, als Selbstbericht "
        "attribuieren ('beschreibt sich als ...', 'erlebt sich als ...', "
        "'schildert, dass ...') statt als Fremdurteil. Alltagsnah-deskriptive "
        "Wörter statt klinischer Etiketten, wo keine Diagnose gemeint ist. "
        "Beispiel besser: 'Frau G. beschreibt sich eingangs des Gesprächs als "
        "müde, unruhig und unkonzentriert' - NICHT: 'Wir erlebten Frau G. als "
        "sehr unruhig und konzentrationsgestört'. Keine Begriffe wie "
        "'gestört'/-gestört-Komposita, 'defizitär', 'auffällig' oder "
        "'pathologisch' als Personenbeschreibung.\n\n"
        "QUELLENREGEL: Alle Inhalte müssen aus dem Transkript oder den Stichpunkten "
        "ableitbar sein. Keine Symptome, Diagnosen, Interventionen oder Zitate "
        "erfinden die nicht im Gespräch vorkamen.\n\n"
        + FEW_SHOT_DOKUMENTATION
    ),

    "anamnese": (
        # Pflichtkern fuer P2 (Anamnese-Call). Befund laeuft als separater Call mit
        # eigenem BASE_PROMPTS["befund"].
        "WICHTIG: KEINE Unterüberschriften! Kein 'Vorstellungsanlass:', "
        "kein 'Aktuelle Erkrankung:', kein 'PSYCHOPATHOLOGISCHER BEFUND', "
        "kein 'AMDP', keine Bullet-Listen. "
        "Stattdessen fließende Übergänge zwischen den Themen.\n\n"
        "DIAGNOSEN gemäß ICD: {diagnosen}\n"
        # v19.19 (A3a): Diagnose begruenden, nicht nennen. Feedback e.krause
        # 07.08.: 'Diagnose kaum beruecksichtigt' = die Kriterien der
        # Einweisungsdiagnose kommen nicht ausreichend vor.
        "Diese Diagnosen sind der fachliche RAHMEN der Anamnese, NICHT ihr "
        "Textinhalt: Die Anamnese muss die zugehörigen Kriterien und Symptome "
        "so abbilden, dass die Diagnose daraus nachvollziehbar wird - soweit "
        "sie in den Quellen belegt sind (z.B. Stimmung, Antrieb, Interesse, "
        "Schlaf, Appetit, Konzentration, Selbstwert, Ängste, Vermeidung, "
        "körperliche Beschwerden, zeitlicher Verlauf, Auslöser, Alltags-"
        "beeinträchtigung). Was die Quellen zu einem Kriterium nicht hergeben, "
        "wird WEGGELASSEN - nicht mit 'nicht erhoben' aufgefüllt. "
        "Diagnosebezeichnungen und ICD-Codes erscheinen NICHT im Text.\n\n"
        "NICHT SCHREIBEN:\n"
        "– Keinen psychopathologischen Befund (wird separat generiert!)\n"
        "– Keine 'PSYCHOPATHOLOGISCHER BEFUND'-Sektion, kein 'AMDP'-Schema\n"
        "– Keine 'SYSTEMISCHE EINSCHÄTZUNG' oder Hypothesen-Abschnitte\n"
        "– Keine Bullet-Listen, keine Stichworte, keine Pipe-Separatoren ('|')\n"
        "– Keine Diagnosen-Wiederholung am Ende\n"
        "– Keine Therapieempfehlungen oder Behandlungspläne\n"
        "– Kein Markdown (keine **, keine ##, keine ---)\n"
        "– KEINEN ###BEFUND###-Separator (der Befund kommt in einem separaten Call)\n\n"
        "QUALITÄTSANFORDERUNGEN:\n"
        "- QUELLENREGEL: Jeder Satz MUSS auf eine konkrete Stelle in den "
        "bereitgestellten Unterlagen (Selbstauskunft, Vorbefunde, Aufnahmegespräch) "
        "zurückführbar sein. Findest du keine Quelle → 'nicht erhoben'.\n"
        "- Lies die Selbstauskunft des AKTUELLEN Patienten sorgfältig. "
        "Schreibe über DIESEN Patienten – nicht über einen Beispielpatienten.\n"
        "- Direkte Patientenzitate NUR wenn WÖRTLICH in der Selbstauskunft\n"
        "- NIEMALS erfinden: Beruf, Familienstand, Kinder, Wohnsituation, "
        "Vorbehandlungen, Medikamente, Suchtmittel, Diagnosen, Zeitangaben, "
        "auslösende Ereignisse, Testwerte, Zitate\n"
        # v13: LÄNGE-Zeile entfernt - Längenanker steht zentral via resolve_length_anchor()
        "- Schreibe ausführliche, zusammenhängende Absätze (KEINE kurzen Stichwort-Absätze) als Fließtext\n\n"
        # v19.19 (A1): Konjunktiv I als harte Regel. Zwei Therapeuten-
        # Feedbacks (e.krause 07.08. Rating 1, c.saur 20.08.) forderten die
        # indirekte Rede; Messung ueber 6 Log-Outputs: 4x NULL Konjunktiv-
        # formen (mistral-small3.2). Bisher stand die Regel nur in
        # Checklisten/Kommentaren - nicht im Pflichtkern.
        "SPRACHFORM (verbindlich): Alle Angaben, die auf Aussagen der Patientin/"
        "des Patienten beruhen (Selbstauskunft, Aufnahmegespräch), werden in "
        "INDIREKTER REDE im KONJUNKTIV I wiedergegeben - das kennzeichnet sie "
        "als Selbstbericht, nicht als geprüften Befund.\n"
        "  RICHTIG: 'Sie berichtet, sie fühle sich seit Monaten erschöpft und "
        "habe den Kontakt zu Freunden weitgehend abgebrochen. Ihre Mutter sei "
        "früh verstorben; der Vater habe viel getrunken.'\n"
        "  FALSCH:  'Sie fühlt sich seit Monaten erschöpft und hat den Kontakt "
        "zu Freunden abgebrochen. Ihre Mutter ist früh verstorben.'\n"
        "Rahmenverben (berichtet, schildert, gibt an, beschreibt) stehen im "
        "Indikativ, der Inhalt danach im Konjunktiv I (sei, habe, fühle, könne, "
        "müsse, wolle, leide, nehme, arbeite, lebe ...). Wo Konjunktiv I mit dem "
        "Indikativ formgleich wäre (z.B. 'sie hätten' statt 'sie haben'), ist "
        "Konjunktiv II zulässig. Nur objektiv belegte Fakten aus Vorbefunden "
        "oder die ICD-Diagnosen stehen im Indikativ.\n\n"
        # v13 Iteration C: Anamnese-Längen-Disziplin verstärken.
        # Bisheriges Problem: Anamnese-Modell ignorierte ZIELLÄNGE strukturell.
        # an-02 produzierte 633w bei Max=418w (51% über Limit).
        # Ursache-Vermutung: Modell behandelt jede Anamnese-Subkategorie
        # (Vorgeschichte, Familienbild, Ressourcen, Behandlungserwartungen) als
        # gleich-wichtig zu erwähnen → schreibt erschöpfend statt selektiv.
        # Lösung: explizite Disziplin-Anweisung mit Verbweisung Wortzählung.
        "LÄNGEN-DISZIPLIN (kritisch):\n"
        "Die ZIELLÄNGE im Prompt ist ein hartes Limit, KEIN Zielwert nach oben offen. "
        "Wenn du dich der Obergrenze näherst:\n"
        "- Lasse weniger relevante Inhalte WEG statt sie kürzer zu erwähnen.\n"
        "- Priorisiere: Hauptanliegen + zentrale Symptome + Auslöser + 1-2 zentrale "
        "biographische Stränge. Sekundäres (z.B. nebensächliche Hobbys, "
        "ausführliche Dokumentation aller Vorbehandlungen) kann komplett entfallen.\n"
        "- Bei umfangreicher Selbstauskunft: nicht alles wiedergeben, sondern "
        "verdichten. Eine zweisätzige Zusammenfassung von 4 Themen ist oft "
        "besser als 4 ausführliche Absätze.\n"
        "Vor Abgabe: zähle deine Wörter (überschlägig) - wenn über Maximum, "
        "kürze BEVOR du abgibst, nicht der Leser.\n\n"
        + FEW_SHOT_ANAMNESE
    ),

    "befund": (
        # Pflichtkern fuer den Befund-Call (zweiter LLM-Call nach Anamnese).
        # Die BEFUND_VORLAGE selbst wird vom Frontend als 'befund_vorlage'
        # Form-Feld mitgeschickt und in build_system_prompt eingesetzt -
        # nicht hier hardcoded, damit der Therapeut sie editieren kann.
        "Erstelle einen psychopathologischen Befund auf Basis der bereitgestellten Unterlagen "
        "(Selbstauskunft, Vorbefunde, Anamnese-Fließtext der bereits erstellt wurde).\n\n"
        "Verwende EXAKT die folgende Vorlage. Fülle alle Lücken mit Informationen "
        "aus der Selbstauskunft. Kürze Mehrfachoptionen auf die zutreffende Variante. "
        "Wenn eine Information nicht in den Unterlagen steht, schreibe 'nicht erhoben' – "
        "NIEMALS eine klinisch plausible Option raten oder erfinden.\n\n"
        "BEFUND-VORLAGE (exakt so ausfüllen):\n"
        "{befund_vorlage}\n\n"
        "DIAGNOSEN gemäß ICD: {diagnosen}\n\n"
        "NICHT SCHREIBEN:\n"
        "– Keine Anamnese-Inhalte (wurden bereits in einem vorigen Call generiert)\n"
        "– Keine Therapieempfehlungen, Hypothesen oder Behandlungspläne\n"
        "– Keine Diagnosen-Wiederholung am Ende\n"
        "– Kein Markdown (keine **, keine ##, keine ---)\n"
        "– KEINEN ###BEFUND###-Separator (gib nur den Befund-Text aus, ohne Praeambel)\n\n"
        "QUALITÄTSANFORDERUNGEN:\n"
        "- QUELLENREGEL: Jeder Eintrag MUSS auf eine konkrete Stelle in den Unterlagen "
        "zurückführbar sein. Findest du keine Quelle → 'nicht erhoben'.\n"
        "- Direkt mit dem Befund beginnen, keine Vorbemerkungen.\n"
    ),

    "verlaengerung": (
        "FOKUS:\n"
        "Schreibe NUR diesen einen Abschnitt als Fließtext – keine Diagnosen, "
        "keine Stammdaten, keine anderen Sektionen des Antrags.\n\n"
        "STIL:\n"
        + STIL_VORLAGEN_MIMIK +
        "Konkret und patientenspezifisch.\n\n"
        + NAMENSFORMAT
        + "HALLUZINATIONSSCHUTZ – QUELLENREGEL:\n"
        "Jeder Satz MUSS auf eine konkrete Stelle in der Verlaufsdokumentation "
        "oder Antragsvorlage zurückführbar sein. Keine Therapieinhalte, Methoden, "
        "Fortschritte oder Zitate erfinden die nicht in den Quellen stehen. "
        "Im Zweifel weglassen statt erfinden.\n\n"
        "WICHTIG – STILBEISPIEL:\n"
        "Falls ein Stilbeispiel bereitgestellt wird: Übernimm Struktur und Gliederung. "
        "Ersetze nur die patientenspezifischen Inhalte.\n\n"
        + FEW_SHOT_VERLÄNGERUNG
    ),

    "folgeverlaengerung": (
        "KONTEXT:\n"
        "Dies ist NICHT der erste Verlängerungsantrag. Es gibt einen vorherigen "
        "Verlängerungsantrag dessen Verlaufsabschnitt, Anamnese und Diagnosen "
        "als Referenz dienen. Der neue Text soll an den vorherigen ANKNÜPFEN "
        "und den Verlauf SEIT DEM LETZTEN ANTRAG beschreiben.\n\n"
        "FOKUS:\n"
        "Schreibe NUR den Abschnitt 'Verlauf und Begründung der weiteren Verlängerung' "
        "als Fließtext – keine Diagnosen, keine Stammdaten, keine Anamnese.\n\n"
        # v13 Ä2 + Iteration A korrigiert: STRUKTUR erzwingt nur die Überschrift,
        # NICHT mehr die Wir-Form. Der konkrete Tonfall (Wir vs. empathische
        # 3.-Person) wird in STIL-CHECKS aus der Stilvorlage abgeleitet.
        # Hintergrund: Ä2 erzwang "Zeile 3+ mit Wir" - das kollidierte mit
        # 3.-Person-Stilvorlagen, gegen die das Modell nicht ankam (fva-02).
        # Jetzt: Überschrift fix, erste-Zeile-Regel folgt Vorlage.
        "STRUKTUR (verbindlich, exakt einhalten):\n"
        "Zeile 1: Überschrift wörtlich: 'Verlauf und Begründung der weiteren Verlängerung'\n"
        "Zeile 2: Leerzeile\n"
        "Zeile 3+: Fließtext im Stil der Vorlage. NICHT mit 'Im weiteren Verlauf'\n"
        "          oder 'Seither hat sich …' beginnen - das sind\n"
        "          generische Floskeln. Beginne stattdessen mit einer konkreten\n"
        "          Beobachtung im Stil der Vorlage (z.B. 'Wir erlebten Frau M. ...',\n"
        "          'Frau M. zeigte sich ...', 'Im hypnosystemischen Einzelprozess ...').\n\n"
        "STIL:\n"
        + STIL_VORLAGEN_MIMIK +
        "Konkret und patientenspezifisch.\n\n"
        + NAMENSFORMAT
        + "HALLUZINATIONSSCHUTZ – QUELLENREGEL:\n"
        "Jeder Satz MUSS auf eine konkrete Stelle in der Verlaufsdokumentation, "
        "dem vorherigen Antrag oder der Antragsvorlage zurückführbar sein. "
        "Keine Therapieinhalte, Methoden, Fortschritte oder Zitate erfinden. "
        "Im Zweifel weglassen statt erfinden.\n\n"
        "WICHTIG – STILBEISPIEL:\n"
        "Falls ein Stilbeispiel bereitgestellt wird: Übernimm Struktur und Gliederung "
        "(aber NICHT die Sektionsüberschrift überschreiben - die ist verbindlich oben). "
        "Ersetze nur die patientenspezifischen Inhalte.\n\n"
        + FEW_SHOT_VERLÄNGERUNG
    ),

    "entlassbericht": (
        "NICHT SCHREIBEN:\n"
        "– Keine Überschriften (kein 'Psychotherapeutischer Behandlungsverlauf', "
        "kein 'Epikrise', keine nummerierten Abschnitte)\n"
        "– Keine Einleitungspräambel ('Im Folgenden...', 'Die Behandlung erstreckte sich...')\n"
        "– Keine Beschreibung des Therapieangebots der Klinik "
        "(kein Block über Einzelgespräche, Gruppentherapie, Bezugsgruppe etc.)\n"
        "– Keine 'Einladungen' (nur in Verlaufsnotizen)\n"
        "– Keine Unterschrift, kein Briefkopf, kein Grußsatz\n"
        "– Keine Stammdaten, Diagnosen-Kodierung, Medikation\n\n"
        "STIL:\n"
        + STIL_VORLAGEN_MIMIK +
        "Konkret und patientenspezifisch – keine Allgemeinplätze.\n"
        # v13: LÄNGE-Zeile entfernt - Längenanker steht zentral via resolve_length_anchor()
        "Vermeide unnötige Ausschmückungen und Wiederholungen.\n\n"
        # v19.6: Ressourcenorientierte Tonalitaet (hypnosystemische Haltung).
        # NUR P4 (und der Sache nach P1, dort bereits via BASE_PROMPTS
        # ["dokumentation"] abgedeckt). P3/P3b bewusst NICHT: Verlaengerungs-
        # antraege muessen Behandlungsbedarf gegenueber der Kasse begruenden.
        "TONALITÄT – RESSOURCENORIENTIERT (hypnosystemische Haltung der Klinik):\n"
        "– AGENS-REGEL: Die Patientin/der Patient ist aktives Subjekt der "
        "Entwicklung ('konnte nachgehen', 'wandte sich zu', 'entdeckte', "
        "'nutzte'), nicht beobachtetes Objekt ('zeigte Defizite', "
        "'wies Auffälligkeiten auf').\n"
        "– KEINE kausal-diagnostischen Deutungen ('was auf ein tiefes Bedürfnis "
        "hindeutet') – Erleben würdigen statt diagnostizieren.\n"
        "– DEFIZIT-VOKABULAR VERMEIDEN: 'Defizit'/'defizitär', 'gestört', "
        "'dysfunktional', 'maladaptiv', 'Rückfallprävention', 'muss bearbeitet "
        "werden'. Restthemen als nächste Entwicklungsschritte formulieren "
        "('lädt zur Vertiefung ein', 'Weiterentwicklung von'), nicht als "
        "Mängelliste.\n"
        "– EIGENINITIATIVE der Patientin/des Patienten (z.B. selbständig "
        "begonnene Lektüre, Übungen, Aktivitäten) explizit als Ressource "
        "würdigen.\n"
        "– Empfehlungen als Vertiefung erreichter Fortschritte rahmen, nicht "
        "als Arbeit an Defiziten.\n\n"
        "FORMULIERUNGS-KONTRASTE (schlecht → gut):\n"
        "– 'Bearbeitung der interaktionellen Defizite' → 'Vertiefung der "
        "Fortschritte in Richtung authentischer Kommunikation und "
        "Beziehungsgestaltung'\n"
        "– 'suchte nach Halt, was auf ein tiefes biographisches Bedürfnis "
        "hindeutet' → 'konnte ihrem Wunsch nach Halt nachgehen und erlebbar "
        "machen'\n"
        "– 'Prävention depressiver Rückfälle in den Wintermonaten' → "
        "'Aufrechterhaltung der gewonnenen Stabilität und gute Selbstfürsorge "
        "in den Wintermonaten'\n"
        "– 'Die Defizite in der Emotionsregulation bestehen fort' → 'Die "
        "begonnene Entwicklung eines flexibleren Umgangs mit intensiven "
        "Gefühlen lädt zur ambulanten Vertiefung ein'\n\n"
        "VERFÄLSCHUNGSSCHUTZ: Ressourcenorientierung ändert die Sprache, "
        "nicht die klinischen Fakten. Restsymptomatik, Risiken und "
        "Behandlungsbedarf bleiben klar benannt – der Nachbehandler braucht "
        "ein realistisches Bild. Ungünstige oder gleichbleibende Befunde und "
        "Testwerte ehrlich benennen, nicht verschweigen; eine Einordnung nur, "
        "wenn sie sich aus den Quellen begründen lässt, gekennzeichnet als "
        "Einschätzung ('verstehen wir als', 'aus unserer Sicht').\n\n"
        "TESTWERTE: Prä-/Post-Messwerte NUR übernehmen, wenn sie wörtlich in "
        "der Antragsvorlage oder Verlaufsdokumentation stehen – Zahlen exakt "
        "abschreiben, NIEMALS erfinden, schätzen oder runden. Enthalten die "
        "Quellen keine Testwerte, entfällt der Satz ersatzlos. Die Zahlenwerte "
        "aus dem BEISPIEL unten NIEMALS übernehmen – sie sind fiktiv.\n\n"
        "QUELLENREGEL: Jeder Satz MUSS auf eine konkrete Stelle in der "
        "Verlaufsdokumentation oder Antragsvorlage zurückführbar sein. "
        "Keine Therapieinhalte, Diagnosen, Methoden oder Zitate erfinden "
        "die nicht in den Quellen stehen. Im Zweifel weglassen.\n\n"
        + FEW_SHOT_ENTLASSBERICHT
    ),

    "akutantrag": BASE_PROMPT_AKUTANTRAG,

    # v19.18 (PX): ISM-Fragebogen - Pflichtkern (NICHT editierbar).
    # Wird von ism.build_ism_system_prompt() konsumiert; build_system_prompt
    # kann den Workflow ebenfalls bauen (Registry-Konsistenz/Tests), der
    # Job-Pfad laeuft aber ueber den dedizierten Structured-Output-Builder.
    "ism_fragebogen": (
        "AUSGABEFORM (verbindlich): Antworte AUSSCHLIESSLICH mit einem "
        "JSON-Objekt der Form {\"begruessung\": \"...\", \"verabschiedung\": "
        "\"...\", \"items\": [{\"faktor_id\": 0, \"frage\": \"...\", "
        "\"pol_min\": \"...\", \"pol_max\": \"...\"}, ...]}. Kein Text vor "
        "oder nach dem JSON, kein Markdown.\n\n"
        "FAKTORREGELN:\n"
        "- faktor_id ist eine Ganzzahl 0-5 gemäß der Faktorliste.\n"
        "- Faktor 2 (Hindernisse): entweder belastungsseitig formulieren "
        "(hohes Rating = hohe Belastung) ODER auf die Ressourcenseite drehen "
        "(hohes Rating = gelungener Umgang) - die Pol-Labels müssen die "
        "gewählte Richtung eindeutig machen.\n"
        "- Pole: pol_min gehört zum Wert 0, pol_max zum Wert 100. Beide kurz "
        "(wenige Worte bis ein Satz), unterscheidbar, in der Sprache des "
        "Klienten.\n\n"
        "PERSPEKTIVE: Ich-Perspektive des Klienten. NIEMALS Wir-Form, "
        "niemals Therapeuten- oder Berichtssprache in Items oder Polen.\n\n"
        "QUELLENREGEL: Jedes Item MUSS auf konkrete Inhalte des Gesprächs "
        "zurückführbar sein (Anliegen, Ressourcen, Hindernisse, Muster, "
        "Formulierungen des Klienten). NIEMALS Themen, Symptome, Anteile "
        "oder Metaphern erfinden, die nicht im Gespräch vorkommen. Liefert "
        "das Gespräch für einen Faktor nichts Tragfähiges, bleibt er ohne "
        "Item.\n\n"
        "DATENSCHUTZ: Keine Nachnamen in Items, Polen, Begrüßung oder "
        "Verabschiedung. Der Vorname darf in Begrüßung/Verabschiedung "
        "verwendet werden, wenn er im Gespräch fällt (Duz-/Siez-Form wie im "
        "Gespräch); im Zweifel neutrale Anrede ohne Namen. Keine Namen "
        "dritter Personen - stattdessen Rollenbezeichnungen (z.B. 'mein "
        "Sohn', 'meine Kollegin')."
    ),
}


# ── Prompt-Zusammenbau ────────────────────────────────────────────────────────

def derive_word_limits(
    style_texts: "list[str]",
    fallback_min: int,
    fallback_max: int,
    tolerance: float = 0.30,
) -> "tuple[int, int]":
    """
    Leitet min/max Wortlimit dynamisch aus Stilvorlagen ab.

    Nimmt die Wortanzahl aller Referenztexte (mind. 50 Wörter), berechnet
    die Bandbreite und erweitert sie um ±tolerance.
    Fallback auf die uebergebenen Defaults wenn keine verwertbaren Vorlagen.

    Verwendung:
      - Im Backend: build_system_prompt() ruft diese Funktion mit dem Rohtext
        auf, bevor er destilliert wird.
      - Im Test (test_eval.py): gleiche Logik, gleiche Funktion importieren
        oder duplizieren.
    """
    import re as _re

    counts = []
    for t in style_texts:
        if not t:
            continue
        w = len(t.split())
        if w >= 50:
            counts.append(w)

    if not counts:
        return fallback_min, fallback_max

    ref_min = min(counts)
    ref_max = max(counts)
    derived_min = max(50, int(ref_min * (1 - tolerance)))
    derived_max = int(ref_max * (1 + tolerance))

    import logging as _logging
    _logging.getLogger(__name__).info(
        "Wortlimit aus %d Stilvorlage(n) abgeleitet: %d–%d "
        "(Referenz: %d–%d, ±%.0f%%)",
        len(counts), derived_min, derived_max, ref_min, ref_max, tolerance * 100,
    )
    return derived_min, derived_max


# ── v13: Zentralisierter Längen-Anker ────────────────────────────────────────
#
# Bis v12 wurden Längenanweisungen an vier verstreuten Stellen in den Prompt
# eingespeist:
#   (D) BASE_PROMPTS[wf] mit hardcoded "LÄNGE: Mindestens 400 Wörter"
#   (E) WORKFLOW_INSTRUCTIONS_DEFAULT[akutantrag] mit "- LÄNGE: 150-350 Wörter"
#   (F) FEW_SHOT_*-Header mit "ca. 600-900 Wörter"
#   (G) build_user_content mit "Mindestens 400 Wörter"
#   (H) VERBINDLICHES TEXTLIMIT-Block ans Ende des base
# Folge: Konflikte zwischen den Anweisungen, das Modell mittelt mit Tendenz
# nach oben (an-02: 775w/Cap 418w) oder unten (fva-02: Absatzlänge 104w/Ref 360w).
#
# v13: resolve_length_anchor() ist die EINZIGE Quelle. Alle anderen Stellen
# (D-G) wurden entfernt. Im finalen Prompt steht genau EIN Längenanker -
# direkt vor dem Schreibauftrag.

# Substantialitäts-Schwelle: Stilvorlagen unter dieser Wortzahl werden NICHT zur
# Längenherleitung benutzt (würden auf Stichwortskizzen Cap=143w erzeugen wie
# in dok-02-paarthematik). Sie tragen weiterhin zum Schreibstil bei
# (Satzlänge, Wir-Perspektive etc. via _compute_style_constraints).
#
# v13 refined: Schwelle ist DYNAMISCH abhängig vom Workflow-Min. Eine 200w-Vorlage
# taugt für eine 150w-Dokumentation (substantial), aber NICHT für einen 500w-
# Entlassbericht (würde derived_max=260 produzieren, was nach Floor-Anhebung auf
# 500 zu min>max führt → invalid_range). Daher: substantial = ≥0.7×workflow_min.
# Untere Bodengrenze 100w (sehr kurze Stichwortskizzen niemals als substantial).
LENGTH_ANCHOR_MIN_THRESHOLD = 100
LENGTH_ANCHOR_THRESHOLD_RATIO = 0.7

# Ceiling-Multiplikator: derived_max wird auf workflow_default_max × 1.4 gecappt.
# Verhindert, dass eine ungewöhnlich lange Stilvorlage (z.B. Akutantrag mit
# Volltext-Anhang) das Limit aufbläht. Workflow-Defaults haben absolute Priorität.
LENGTH_ANCHOR_CEILING_MULTIPLIER = 1.4


# v13 P0: Workflows mit verbindlicher Wir-Perspektive (Therapeutenteam-Sicht).
# v13 P0 + Iteration A: Workflows die einen empathisch-therapeutischen Tonfall
# verlangen (Therapeutenteam-Sicht, persönliches Erleben des Patienten).
# Bei diesen Workflows ist der "objektiv-distanzierte Berichtstil" zu vermeiden:
#   schlecht:  "Der Patient zeigte ausgeprägte Symptome", "Die Klientin äußerte..."
#   gut:       "Wir erlebten Frau M. erschöpft" (Wir-Form), oder
#              "Frau M. berichtete, sie fühle sich überfordert" (empathisch-konjunktivische
#              3.-Person mit innerer Perspektive)
#
# Wenn die Stilvorlage selbst Wir benutzt -> wir-Bandbreite ableiten.
# Wenn die Stilvorlage 3.-Person ist -> 3.-Person erlauben, aber nur in
# empathisch-konjunktivischer Form, nie als objektiv-wissenden Bericht.
#
# Hintergrund: P0 (Wir-Zwang für alle Wir-Workflows) hat gegen die Beispiele
# gekämpft (3.-Person-Vorlagen wirken stärker als abstrakte Wir-Anweisung).
# Lösung: Wir-Form NICHT erzwingen wenn Vorlage 3.-Person ist, aber den
# gefährlichen objektiv-distanzierten Berichtston explizit verbieten.
EMPATHIC_WORKFLOWS = frozenset({
    "akutantrag",
    "verlaengerung",
    "folgeverlaengerung",
    "entlassbericht",
})

# Backwards-compat-Alias: alte Tests und ggf. externer Code nutzen WIR_WORKFLOWS
WIR_WORKFLOWS = EMPATHIC_WORKFLOWS

# Default-Wir-Bandbreite NUR aktiv wenn Stilvorlage selbst Wir benutzt.
# Bei 3.-Person-Vorlagen wird KEIN Wir-Zwang erzeugt, sondern ein Verbot des
# objektiv-distanzierten Berichtstils.
WIR_WORKFLOW_DEFAULT_LO_PCT = 1.0
WIR_WORKFLOW_DEFAULT_HI_PCT = 3.0


# v13 Strategie 3: Marker, mit dem mehrere Stilvorlagen-Beispiele in einem
# string verkettet werden. Erzeugt von retrieve_style_examples (pgvector-Pfad)
# und von _build_multi_style_text in tests/test_eval.py (vorlage.txt + vorlage2.txt).
# Format: "--- Beispiel N ---" oder "--- Beispiel N [Anker] ---"
_STYLE_EXAMPLE_MARKER_RE = re.compile(
    r'^---\s*Beispiel\s+\d+(?:\s*\[Anker\])?\s*---\s*$',
    re.MULTILINE,
)


def split_style_examples(combined: str) -> list:
    """
    Splittet einen verketteten Stil-Kontext in einzelne Beispieltexte.

    Erkennt das Marker-Format aus retrieve_style_examples (embeddings.py)
    und das pro-Testcase-Format aus der Eval (vorlage.txt + vorlage2.txt
    werden mit demselben Marker konkateniert):
      "--- Beispiel 1 [Anker] ---\\n<text>\\n\\n--- Beispiel 2 ---\\n<text>"

    Falls keine Marker vorhanden sind, wird der gesamte Text als ein einzelnes
    Beispiel zurückgegeben (Backwards-Compat für Single-Style-Kanäle).

    Returns:
      Liste der Einzeltexte (gestrippt). Leere Strings werden gefiltert.
    """
    if not combined or not combined.strip():
        return []
    parts = _STYLE_EXAMPLE_MARKER_RE.split(combined)
    # Nach dem Split: erstes Element ist alles VOR dem ersten Marker (meist leer
    # oder Header). Wenn keine Marker gefunden, gibt es genau ein Element.
    cleaned = [p.strip() for p in parts if p and p.strip()]
    return cleaned if cleaned else [combined.strip()]


def resolve_length_anchor(
    workflow: str,
    style_raw_texts: "list[str] | None" = None,
    workflow_default: "tuple[int, int] | None" = None,
    tolerance: float = 0.30,
) -> dict:
    """
    EINZIGE Quelle für Längenanweisungen im finalen Prompt (v13).

    Resolution chain (Reihenfolge = Priorität):
      1. Substantielle Stilvorlage(n) ≥{SUBSTANTIAL_THRESHOLD}w
         → derive_word_limits(±tolerance), gefloort/gecapt durch Workflow-Default
         → source = "style"
      2. Stilvorlage(n) vorhanden, aber alle <{SUBSTANTIAL_THRESHOLD}w
         (Stichwortskizzen, Notizen) → Workflow-Default
         → source = "style_too_short_fallback"
      3. Keine Stilvorlage → Workflow-Default
         → source = "workflow_default"

    Returns:
      {
        "min": int, "max": int, "target": int,
        "source": str,              # für Telemetrie/Logging
        "anchor_block": str,        # fertig formulierter Prompt-Block
        "n_substantial": int,       # Anzahl substantieller Stilvorlagen
      }

    Der anchor_block wird in build_system_prompt() direkt vor dem
    "Schreibe jetzt..."-Schluss eingefügt (höchste Recency-Gewichtung).
    """
    from app.core.workflows import word_limit_for

    fb_min, fb_max = workflow_default or word_limit_for(workflow, fallback=(200, 800))

    # v13 refined: Dynamische Substantialitäts-Schwelle.
    # Eine Stilvorlage taugt nur dann zur Längenherleitung, wenn sie mindestens
    # 70% des Workflow-Minimums hat - sonst würde derive_word_limits einen Cap
    # erzeugen, der nach Floor-Anhebung zu min>max führt (invalid_range).
    # Bodengrenze 100w schützt zusätzlich vor sehr kurzen Stichwortskizzen.
    substantial_threshold = max(
        LENGTH_ANCHOR_MIN_THRESHOLD,
        int(fb_min * LENGTH_ANCHOR_THRESHOLD_RATIO),
    )

    # Stilvorlagen kategorisieren
    substantial = [
        t for t in (style_raw_texts or [])
        if t and len(t.split()) >= substantial_threshold
    ]
    has_any_style = bool(style_raw_texts and any(t and t.strip() for t in style_raw_texts))

    if substantial:
        derived_min, derived_max = derive_word_limits(
            substantial, fb_min, fb_max, tolerance=tolerance
        )
        # Floor: nie unter Workflow-Minimum (Schutz vor Stichwort-Underrun)
        anchor_min = max(fb_min, derived_min)
        # Ceiling: nie deutlich über Workflow-Maximum (Schutz vor Volltext-Overrun)
        anchor_max = min(int(fb_max * LENGTH_ANCHOR_CEILING_MULTIPLIER), derived_max)
        # Sanity: min < max
        if anchor_min >= anchor_max:
            anchor_min, anchor_max = fb_min, fb_max
            source = "style_invalid_range_fallback"
        else:
            source = "style"
        n_sub = len(substantial)
    elif has_any_style:
        anchor_min, anchor_max = fb_min, fb_max
        source = "style_too_short_fallback"
        n_sub = 0
    else:
        anchor_min, anchor_max = fb_min, fb_max
        source = "workflow_default"
        n_sub = 0

    target = (anchor_min + anchor_max) // 2

    anchor_block = (
        f"\nZIELLÄNGE: ca. {target} Wörter (akzeptierte Bandbreite {anchor_min}–{anchor_max} Wörter). "
        f"Harte Obergrenze: {anchor_max} Wörter — überschreite sie NIEMALS. "
        f"Lieber {max(anchor_min, target - 30)} Wörter präzise als {anchor_max + 50} Wörter ausschweifend. "
        f"Zähle vor Abgabe deine Wörter.\n"
    )

    return {
        "min": anchor_min,
        "max": anchor_max,
        "target": target,
        "source": source,
        "anchor_block": anchor_block,
        "n_substantial": n_sub,
    }


def _compute_style_constraints(
    style_text: str,
    skip_length: bool = False,
    workflow: Optional[str] = None,
) -> str:
    """
    Berechnet quantitative Stil-Metriken aus dem Stilbeispiel und
    formuliert sie als konkrete Vorgaben fuer den Prompt.

    Gibt einen String mit STIL-VORGABEN inkl. hartem Wortlimit zurueck,
    der in build_system_prompt() eingefuegt wird und die hardcodierten
    Laengenangaben im BASE_PROMPT ueberschreibt.

    skip_length: wenn True wird die TEXTLAENGE-Zeile ausgelassen. Nutzen
                 wenn weiter oben bereits ein VERBINDLICHES TEXTLIMIT
                 aus mehreren Stilvorlagen gesetzt wurde (vermeidet
                 widersprueche zwischen destillierter Einzelvorlage und
                 aggregiertem Limit).
    workflow:    v13 P0. Wenn der Workflow Wir-Pflicht hat (siehe
                 WIR_WORKFLOWS), wird die Perspektive-Zeile in der
                 Checkliste IMMER auf Wir gesetzt - egal ob die
                 Stilvorlage selbst Wir benutzt oder nicht. Ohne diesen
                 Override entsteht ein Prompt-interner Widerspruch
                 zwischen BASE_PROMPT (Wir-Pflicht) und Checkliste
                 (kein Wir, weil Stilvorlage 3.-Person).
    """
    import re as _re

    words = style_text.split()
    word_count = len(words)
    if word_count < 30:
        return ""

    # Satzlaenge
    # Issue-2/R1: zentraler abkuerzungsfester Splitter statt naivem
    # [.!?]+-Split (der zaehlte 'z.B.' als Satzende -> verzerrte
    # Satzlaengen-Constraints im System-Prompt).
    sentences = split_sentences_de(style_text)
    sentences = [s.strip() for s in sentences if len(s.strip().split()) >= 3]
    avg_sentence_len = round(sum(len(s.split()) for s in sentences) / max(len(sentences), 1), 0)

    # Absatzlaenge - robust gegen verschiedene Trenner.
    # extract_docx_section joined Paragraphen mit \n (mit "" fuer Leerzeilen).
    # Strategie: erst \n\n probieren, dann \n.
    paragraphs = [p.strip() for p in style_text.split("\n\n") if len(p.strip().split()) >= 10]
    if len(paragraphs) < 2:
        # Fallback: einzelne Zeilen als Absaetze (>=20 Woerter = substantieller Absatz)
        paragraphs = [p.strip() for p in style_text.split("\n") if len(p.strip().split()) >= 20]
    if len(paragraphs) < 1:
        paragraphs = [style_text.strip()]
    avg_para_len = round(sum(len(p.split()) for p in paragraphs) / max(len(paragraphs), 1), 0)

    # Wir-Perspektive
    wir_pattern = _re.compile(r'\b(wir|uns|unser[ems]?|unserer?)\b', _re.IGNORECASE)
    wir_count = len(wir_pattern.findall(style_text))
    wir_ratio = wir_count / max(word_count, 1)
    uses_wir = wir_ratio > 0.005  # >0.5% = Wir-Perspektive

    # Fachbegriff-Dichte (vereinfacht)
    fachbegriffe = _re.compile(
        r'\b(Affekt|Antrieb|Dissoziation|Psychomotorik|Suizidalit|'
        r'Anhedonie|Rumination|Intrusion|Hyperarousal|Vermeidung|'
        r'Übertragung|Gegenübertragung|Mentalisierung|Affektregulation|'
        r'Selbstwirksamkeit|Ressourcen|Resilienz|Copingstrategien|'
        r'Ich-Struktur|Bindungsmuster|Externalisierung|Internalisierung)\b',
        _re.IGNORECASE
    )
    fach_count = len(fachbegriffe.findall(style_text))
    fach_density = round(fach_count / max(word_count, 1) * 100, 1)

    # Fragment-Stil-Heuristik: Vorlagen mit sehr kurzen Saetzen ohne Subjekt
    # (z.B. "thematisiert, dass...", "signalisiere ihr...", "bringt Bilder mit")
    # sind stichwort-/notizartig und erfordern entsprechenden Output.
    # Erkennung: avg_sentence_len < 8 UND viele Saetze beginnen mit Verb/Partizip
    # statt mit einem Pronomen oder Eigennamen.
    is_fragment_style = False
    if avg_sentence_len < 8 and len(sentences) >= 2:
        # Zaehle Saetze, die mit einem Klein- oder Verb-Wort starten
        # (kein Patientennamen-Initial, keine Anrede, kein Pronomen am Satzanfang)
        verb_start_count = 0
        for s in sentences:
            first_word = s.strip().split()[0] if s.strip().split() else ""
            if not first_word:
                continue
            # Klein geschrieben oder typisches Verb/Partizip am Satzanfang
            if (first_word[0].islower()
                or first_word in ("Thematisiert", "Bringt", "Signalisiere",
                                  "Reflektiert", "Berichtet", "Beschreibt",
                                  "Schildert", "Aeussert", "Äußert")):
                verb_start_count += 1
        if verb_start_count >= len(sentences) * 0.4:
            is_fragment_style = True

    # Hartes Wortlimit aus Vorlagenlänge (±30%), überschreibt BASE_PROMPT-Angaben
    limit_min, limit_max = derive_word_limits([style_text], fallback_min=50, fallback_max=9999)

    # ── v13 Ä3: Stil-Mimik als messbare Checkliste ─────────────────────────
    # Vor Ä3: Stil-Vorgaben als Prosa ("Wir-Form des Behandlungsteams").
    # Modell setzte das qualitativ um, konnte aber nicht quantitativ
    # kontrollieren - Stil-Jury bemängelte durchweg "etwas zu Wir-lastig"
    # oder "leicht abweichend" (Ø 3.6/5).
    #
    # Ä3: Konkrete Zielwerte aus der Stilvorlage als Pre-Submit-Checkliste
    # mit [ ]-Boxen. Selbst-Verifikations-Format wirkt bei Qwen3 deutlich
    # besser als beschreibende Vorgabe. Werte sind dieselben wie vorher,
    # nur Format ist messbar statt qualitativ.

    # Toleranz-Bandbreiten (auf Basis Stilvorlage berechnet)
    sent_lo = max(5, int(avg_sentence_len) - 5)
    sent_hi = int(avg_sentence_len) + 5
    para_count = len(paragraphs)
    para_count_lo = max(1, para_count - 1)
    para_count_hi = para_count + 1

    lines = ["\nSTIL-CHECKS (vor Abgabe verifizieren - jedes Häkchen erfüllt?):"]
    lines.append(f"  [ ] Durchschnittliche Satzlänge: {sent_lo}–{sent_hi} Wörter "
                 f"(Stilvorlage: {int(avg_sentence_len)})")
    lines.append(f"  [ ] Anzahl Absätze: {para_count_lo}–{para_count_hi} "
                 f"(Stilvorlage: {para_count})")

    # v13 P0 + Iteration A (korrigiert): Drei Pfade je nach Workflow + Stilvorlage.
    #
    # Pfad 1: Stilvorlage benutzt Wir
    #   -> Wir-Bandbreite aus Vorlage ableiten, Wir-Form im Output erzwingen.
    #
    # Pfad 2: Stilvorlage in 3.-Person UND Workflow ist empathisch
    #   (akutantrag, verlaengerung, folgeverlaengerung, entlassbericht):
    #   -> 3.-Person erlauben (NICHT Wir-Form erzwingen, sonst kämpfen wir gegen
    #      das konkrete Beispiel), aber den objektiv-distanzierten Berichtstil
    #      ("Der Patient zeigte...", "Die Klientin äußerte...") explizit verbieten.
    #      Stattdessen empathisch-konjunktivische 3.-Person ("Sie berichtete,
    #      sie fühle sich überfordert").
    #
    # Pfad 3: Stilvorlage in 3.-Person UND Workflow nicht empathisch
    #   (anamnese, dokumentation):
    #   -> sachliche 3.-Person erlauben (klassischer Anamnese/Verlaufs-Stil).
    #
    # Hintergrund: P0 (Wir-Zwang für Wir-Workflows) wirkte zu schwach gegen
    # konkrete 3.-Person-Beispiele. fva-02 zeigte: Modell folgt dem Beispiel
    # mehr als der Checkliste. Lösung: Wir-Form NICHT erzwingen, dafür den
    # gefährlichen "wissenden Bericht"-Stil verbieten.
    workflow_is_empathic = workflow in EMPATHIC_WORKFLOWS

    if uses_wir:
        # Pfad 1: Wir-Vorlage -> Wir-Bandbreite empirisch.
        wir_pct = round(wir_ratio * 100, 1)
        wir_target_lo = max(0.3, wir_pct * 0.5)
        wir_target_hi = wir_pct * 1.5
        lines.append(
            f"  [ ] Wir-Anteil: zwischen {wir_target_lo:.1f}% und {wir_target_hi:.1f}% "
            f"aller Wörter sind 'Wir/uns/unser...' (Stilvorlage: {wir_pct}%, "
            f"d.h. ca. {wir_count} Vorkommen auf {word_count} Wörter)"
        )
        lines.append(
            "  [ ] Erster Satz beginnt mit 'Wir' oder Wir-Konstruktion "
            "('Wir erlebten...', 'In unserer Arbeit...', 'Seit dem letzten Antrag konnten wir...')"
        )
    elif workflow_is_empathic:
        # Pfad 2: 3.-Person-Vorlage + empathischer Workflow.
        # KEIN Wir-Zwang (folgen der Vorlage), aber objektiv-distanzierten
        # Berichtston explizit verbieten.
        lines.append(
            "  [ ] Perspektive: 3.-Person wie in der Vorlage. Wir-Form ist "
            "erlaubt aber NICHT verpflichtend - folge dem Beispiel."
        )
        lines.append(
            "  [ ] Tonfall: empathisch-konjunktivisch ('Sie berichtete, sie "
            "fühle sich überfordert', 'Er beschreibe ein Gefühl von ...'). "
            "VERMEIDE den objektiv-wissenden Berichtston ('Der Patient zeigte "
            "ausgeprägte Symptome', 'Die Klientin äußerte deutlich reduzierte "
            "Stimmungslage'). Innere Erlebensperspektive des Patienten muss "
            "spürbar bleiben, nicht nur klinische Beobachtung."
        )
    else:
        # Pfad 3: Anamnese, Dokumentation - klassische sachliche 3.-Person.
        lines.append(
            "  [ ] Perspektive: durchgängig dritte Person (Er/Sie/Patient/in), "
            "kein 'Wir'/'uns'/'unser' im gesamten Text"
        )

    if fach_density > 0:
        # Fachbegriffe haben Bandbreite ±50% (selten konsistent gleichmäßig)
        fach_lo = max(0.0, round(fach_density * 0.5, 1))
        fach_hi = round(fach_density * 1.5, 1)
        lines.append(
            f"  [ ] Fachbegriffsdichte: zwischen {fach_lo} und {fach_hi} pro 100 Wörter "
            f"(Stilvorlage: {fach_density})"
        )
    else:
        lines.append(
            "  [ ] Fachbegriffe: sehr sparsam (Stilvorlage enthält keine "
            "spezialisierten Fachtermini - übernimm diese Zurückhaltung)"
        )

    if is_fragment_style:
        # Stichwort-/Notiz-Stil bleibt qualitativ - schwer messbar zu quantifizieren
        lines.append(
            "  [ ] Stiltyp: Stichwort-/Notiz-Format (kurze Fragmente, Verben am Satzanfang, "
            "oft ohne Satzsubjekt). KEINE Vollsätze mit Subjekt-Verb-Objekt-Struktur."
        )
    else:
        lines.append(
            "  [ ] Stiltyp: zusammenhängender Fließtext (keine Stichworte, "
            "keine Aufzählungen, keine Bullet-Points)"
        )

    # v13: TEXTLÄNGE-Zeile in dieser Funktion entfällt vollständig.
    # Längenangaben kommen ausschliesslich vom zentralen resolve_length_anchor
    # in build_system_prompt. Dies hier liefert nur Stil-Metadaten (Satz-/Absatzlänge,
    # Wir-Perspektive, Fachbegriffsdichte, Absatzanzahl).
    # skip_length-Parameter wird nur noch für Backwards-Compat akzeptiert,
    # hat aber keine funktionale Auswirkung mehr.
    # v13 Ä3: Schluss-Zeile "Absatzstruktur" entfernt - bereits in Check 2 enthalten.

    return "\n".join(lines)


def build_system_prompt(
    workflow: str,
    workflow_instructions: Optional[str] = None,
    style_context: Optional[str] = None,
    style_is_example: bool = False,
    diagnosen: Optional[list[str]] = None,
    patient_name: Optional[dict] = None,
    word_limits: Optional[tuple] = None,
    befund_vorlage: Optional[str] = None,
    # Backwards-Compat: alter Parametername custom_prompt mappt auf
    # workflow_instructions. Bestehende Tests / Legacy-Aufrufe brechen
    # dadurch nicht.
    custom_prompt: Optional[str] = None,
    # Issue-2 (2026-07-03): Konditionales Glossar/Few-Shot. Wenn die
    # zusammengefuehrten QUELLEN uebergeben werden, entscheidet
    # source_mentions_parts_work(), ob das volle KLINISCHES_GLOSSAR + das
    # IFS-Beispiel in den Prompt kommen (Teilearbeit in den Quellen belegt)
    # oder die neutralen Varianten (Priming-Vermeidung). None = altes
    # Verhalten (volles Glossar) fuer Legacy-Aufrufe und Tests.
    source_text: Optional[str] = None,
) -> str:
    """
    Baut den finalen System-Prompt zusammen.

    Architektur (v18):
      1. ROLE_PREAMBLE + Fachglossar
      2. WORKFLOW-ANWEISUNGEN (vom Frontend, editierbar) - WAS geschrieben wird
      3. STILSCHABLONE (falls Stilbeispiel vorhanden)
      4. BASE_PROMPT[workflow] (Pflichtkern, NICHT editierbar) - Stil-/Quellenregeln
      5. Diagnosen, Wortlimit, Patientennamen-Hinweis
      6. Abschliessende Anweisung

    Parameter
    ---------
    workflow_instructions : str, required
        Die inhaltlichen Workflow-Anweisungen vom Frontend.
        Default-Texte stehen in WORKFLOW_INSTRUCTIONS_DEFAULT[workflow] -
        das Frontend zeigt sie als editierbares Feld an. Wenn None oder
        leer uebergeben, wird der Default aus WORKFLOW_INSTRUCTIONS_DEFAULT
        verwendet (Fallback fuer Legacy-Aufrufe und Tests). jobs.py
        validiert davor und lehnt Jobs mit leerem Feld ab.
    befund_vorlage : str, optional
        Nur fuer workflow="befund": die AMDP-Vorlage aus dem Frontend.
        Wenn None, wird BEFUND_VORLAGE als Default eingesetzt.
    custom_prompt : str, optional [DEPRECATED]
        Frueherer Name fuer workflow_instructions. Wird intern weitergeleitet.
    patient_name : dict, optional
        Aus extract_patient_name() - {anrede, vorname, nachname, initial}.
    word_limits : tuple, optional
        (min, max) aus derive_word_limits() - ueberschreibt BASE_PROMPT-Vorgaben.
    """
    # Backwards-Compat: alter Parametername
    if workflow_instructions is None and custom_prompt is not None:
        workflow_instructions = custom_prompt

    # Fallback auf Default falls leer (jobs.py sollte vorher schon validiert
    # haben, aber wir wollen keine harte Exception in build_system_prompt)
    if workflow_instructions is None or not workflow_instructions.strip():
        workflow_instructions = WORKFLOW_INSTRUCTIONS_DEFAULT.get(workflow, "")

    base = BASE_PROMPTS.get(workflow, "")

    diag_str = ", ".join(diagnosen) if diagnosen else "noch nicht festgelegt"
    base = base.replace("{diagnosen}", diag_str)

    # Befund-Workflow: Vorlage einsetzen (vom Frontend, oder Default)
    if workflow == "befund":
        vorlage = befund_vorlage if (befund_vorlage and befund_vorlage.strip()) else BEFUND_VORLAGE
        base = base.replace("{befund_vorlage}", vorlage)

    # v13: Der bisherige VERBINDLICHES TEXTLIMIT-Block (anhängen an base) ist
    # entfallen. Stattdessen wird der zentrale Längenanker am Ende eingefügt -
    # direkt vor dem "Schreibe jetzt..."-Schluss (höchste Recency-Gewichtung).
    # Siehe unten: anchor_block aus word_limits abgeleitet.

    # Reihenfolge (v18):
    #   ROLE_PREAMBLE  →  WORKFLOW-ANWEISUNGEN (Frontend)  →  Stilschablone
    #     →  BASE_PROMPT-Kernel  →  Diagnosen/Wortlimit (im base eingebettet)
    # Issue-2: Glossar-Variante anhand der Quellen dieses Auftrags waehlen.
    # source_text=None (Legacy/Tests) -> konservativ das volle Glossar.
    _parts_work = True if source_text is None else source_mentions_parts_work(source_text)
    _glossar = KLINISCHES_GLOSSAR if _parts_work else KLINISCHES_GLOSSAR_NEUTRAL
    # v19.17 (P-1): P1 bekommt gespraechsnahe statt Berichts-Wendungen
    # (Wir-Form + Antragsfloskeln raus aus der Einzelgespraechs-Doku).
    if workflow == "dokumentation":
        _glossar = _swap_wendungen_for_doku(_glossar)
    # Institutionelles Glossar ist KEIN Priming-Risiko (keine Therapieverfahren-
    # Begriffe) - deshalb unabhaengig von _parts_work immer angehaengt.
    _glossar = _glossar + _render_institutionelles_glossar()
    parts = [ROLE_PREAMBLE + _glossar]

    # Workflow-Anweisungen vom Frontend - das ist der eigentliche Auftrag,
    # gehoert direkt nach der Rolle vor allen restriktiven Pflichtkern-Regeln.
    if workflow_instructions and workflow_instructions.strip():
        parts.append(
            "\nAUFTRAG / INHALTLICHE ANWEISUNGEN:\n"
            + workflow_instructions.strip()
        )

    # Wenn strukturelle Schablone vorhanden: Längenhinweis aus BASE_PROMPT
    # wird durch "ähnliche Länge wie das Beispiel" ersetzt (kommt weiter unten).
    # Wenn kein Stilbeispiel: BASE_PROMPT-Längenhinweise gelten unverändert.

    if style_context and style_context.strip():
        # P1 (dokumentation): Struktur ist durch BASE_PROMPT festgelegt →
        # nur Schreibstil übernehmen, Struktur NICHT verändern.
        # P2/P3/P4: Stilbeispiel ist strukturelle Schablone → Gliederung,
        # Länge und Tonalität übernehmen, nur Patienteninhalte ersetzen.
        is_structural = workflow in STRUCTURAL_WORKFLOWS

        if is_structural:
            parts.append(
                "\nSTRUKTURELLE SCHABLONE DES THERAPEUTEN:\n"
                "Das folgende Beispiel zeigt wie dieser Therapeut einen solchen Bericht verfasst. "
                "Es handelt sich um einen ANDEREN PATIENTEN.\n\n"
                "ARBEITSANWEISUNG – ZWEI SCHRITTE:\n"
                "Schritt 1: Lies das Beispiel und identifiziere die Struktur:\n"
                "  – Wie viele Abschnitte / Absätze?\n"
                "  – Welche Themen in welcher Reihenfolge?\n"
                "  – Ungefähre Gesamtlänge und Absatztiefe?\n"
                "  – Tonalität, Fachbegriffsdichte, Formulierungsgewohnheiten?\n\n"
                "Schritt 2: Schreibe den neuen Bericht in EXAKT dieser Struktur "
                "(gleiche Gliederung, ähnliche Länge, gleiche Abschnittstiefe). "
                "Ersetze ausschließlich alle patientenspezifischen Inhalte "
                "(Namen, Diagnosen, konkrete Ereignisse, Therapiethemen) "
                "durch die Informationen aus der aktuellen Verlaufsdokumentation.\n\n"
                "PATIENTENSPEZIFISCHE BEGRIFFE BEIBEHALTEN:\n"
                "Zentrale Schluesselbegriffe und Themen aus den Quellen des AKTUELLEN "
                "Patienten (Diagnosen, Ereignisse wie 'Trennung', 'Mobbing', 'Verlust', "
                "Methoden wie 'Anteilearbeit', 'EMDR', spezifische Personen wie "
                "Eltern/Kinder) MUESSEN im Output vorkommen, auch wenn sie nicht im "
                "Stilbeispiel stehen. Das Stilbeispiel liefert NUR die Form, NICHT "
                "die Inhalte. Pruefe vor dem Abschluss: kommen alle wichtigen "
                "Themen aus der aktuellen Verlaufsdokumentation im Bericht vor?\n\n"
                "NIEMALS aus dem Beispiel übernehmen: Patientennamen, Diagnosen, "
                "ICD-Codes, konkrete Therapieinhalte, Daten – nur Struktur und Stil.\n\n"
                f"{style_context.strip()}"
                f"{_compute_style_constraints(style_context, skip_length=(word_limits is not None), workflow=workflow)}"
            )
        elif style_is_example:
            parts.append(
                "\nSTILBEISPIEL DES THERAPEUTEN – NUR SCHREIBSTIL REFERENZ:\n"
                "Das folgende Beispiel zeigt den persönlichen Schreibstil dieses Therapeuten. "
                "Es handelt sich um einen ANDEREN PATIENTEN mit anderen Diagnosen und anderen Inhalten.\n"
                "ÜBERNIMM AUSSCHLIESSLICH: Tonalität, Satzbau, Absatzlänge, "
                "Fachbegriffsdichte, Formulierungsgewohnheiten.\n"
                "NIEMALS ÜBERNEHMEN: Diagnosen, ICD-Codes, Patientennamen, Daten, "
                "Medikamente, konkrete Symptome, Therapieinhalte oder andere "
                "patientenspezifische Informationen aus diesem Beispiel.\n\n"
                f"{style_context.strip()}"
                f"{_compute_style_constraints(style_context, skip_length=(word_limits is not None), workflow=workflow)}"
            )
        else:
            parts.append(
                "\nSTILVORLAGE FÜR DIESEN THERAPEUTEN:\n"
                "Übernimm den Schreibstil der folgenden Vorlage. "
                "NICHT die konkreten Inhalte, Diagnosen oder Patientendaten – "
                "nur Tonalität, Satzbau und Formulierungsgewohnheiten.\n\n"
                f"{style_context.strip()}"
                f"{_compute_style_constraints(style_context, skip_length=(word_limits is not None), workflow=workflow)}"
            )

    has_structural_template = (
        style_context and style_context.strip()
        and workflow in STRUCTURAL_WORKFLOWS
    )

    # BASE_PROMPT-Kernel (Pflichtkern, NICHT editierbar) wird nach den
    # Frontend-Anweisungen und der Stilschablone eingehaengt. Enthaelt
    # Stilregeln, Negativ-Listen, Quellenregel und Few-Shot.
    if base:
        # Issue-2: neutrales Doku-Beispiel wenn die Quellen keine
        # Teilearbeit enthalten (deterministischer String-Tausch; schlaegt
        # der Tausch fehl, bleibt schlicht das alte Verhalten).
        if workflow == "dokumentation" and not _parts_work:
            base = base.replace(FEW_SHOT_DOKUMENTATION, FEW_SHOT_DOKUMENTATION_NEUTRAL)
        parts.append("\n" + base)

    # Expliziter Patientennamen-Hinweis (aus den Unterlagen extrahiert)
    # Sicherheits-Check: Block nur ausgeben wenn nachname plausibel ist.
    # Sonst entstuende eine Geisterzeile "Der aktuelle Patient ist   ."
    # oder schlimmer: ein Hinweistext wuerde als Name gerendert.
    if patient_name and patient_name.get("initial"):
        anrede = patient_name.get("anrede") or ""
        initial = patient_name["initial"]
        nachname = patient_name.get("nachname", "") or ""
        vorname = patient_name.get("vorname", "") or ""
        nachname_low = nachname.lower().rstrip(".")

        is_plausible = (
            nachname
            and 1 <= len(nachname) <= 30
            and "klient" not in nachname_low
            and "patient" not in nachname_low
            and len(initial) <= 6
        )
        if is_plausible:
            parts.append(
                f"\nPATIENTENNAME (aus den Unterlagen extrahiert):\n"
                f"Der aktuelle Patient ist {anrede} {vorname} {nachname}.\n"
                f"Verwende im gesamten Bericht AUSSCHLIESSLICH die Bezeichnung '{anrede} {initial}' "
                f"(Anrede + erster Buchstabe des Nachnamens + Punkt).\n"
                f"NIEMALS den vollen Nachnamen, NIEMALS den Vornamen, "
                f"NIEMALS einen Platzhalter (eckige Klammern um Patient/in oder Initiale, oder Pseudo-Namen wie Frau X. / Herr Y.) verwenden.\n"
                f"Beispiel KORREKT: 'Nach der Aufnahme zeigte sich {anrede} {initial} zunehmend...'\n"
                f"Beispiel FALSCH:  Nach der Aufnahme zeigte sich [Pat] zunehmend... (mit Platzhalter)\n"
            )

    # v13: ZENTRALER LÄNGEN-ANKER - direkt vor dem Schreibauftrag (höchste Recency).
    # word_limits ist ein Tuple (min, max), das jobs.py via resolve_length_anchor
    # berechnet hat. Wenn jobs.py die alte API ohne Anker nutzt, fallen wir hier
    # auf einen lokalen resolve_length_anchor-Call zurück (Workflow-Default).
    if word_limits is not None:
        _anchor_min, _anchor_max = word_limits
        _target = (_anchor_min + _anchor_max) // 2
        parts.append(
            f"\nZIELLÄNGE: ca. {_target} Wörter (akzeptierte Bandbreite {_anchor_min}–{_anchor_max} Wörter). "
            f"Harte Obergrenze: {_anchor_max} Wörter — überschreite sie NIEMALS. "
            f"Lieber {max(_anchor_min, _target - 30)} Wörter präzise als {_anchor_max + 50} Wörter ausschweifend. "
            f"Zähle vor Abgabe deine Wörter."
        )
    else:
        # Fallback: Workflow-Default direkt aus core/workflows holen
        _result = resolve_length_anchor(workflow, style_raw_texts=None)
        parts.append(_result["anchor_block"].rstrip())

    if has_structural_template:
        parts.append(
            "\nSchreibe jetzt den Bericht in der Struktur des Stilbeispiels. "
            "Direkt mit dem Text beginnen – keine Vorbemerkungen."
        )
    else:
        parts.append(
            "\nSchreibe jetzt den angeforderten Bericht. "
            "Direkt mit dem Text beginnen – keine Vorbemerkungen, keine Erklärungen. "
            "Sprache: Deutsch. Keine Markdown-Formatierung."
        )

    final_prompt = "\n".join(parts)

    # ── Platzhalter-Substitution (v19.5: IMMER) ───────────────────────────
    # Die Beispiele (FEW_SHOT_*), die ROLE_PREAMBLE und das Glossar nutzen
    # "[Patient/in]" / "[Name]" als BUILD-ZEIT-Platzhalter. Diese werden hier
    # IMMER aufgeloest, damit das Modell NIE einen rohen Platzhalter sieht -
    # sonst kopiert es ihn 1:1 in den Output (der bekannte Platzhalter-Leak,
    # frueher nur substituiert WENN ein Name bekannt war):
    #   - Name bekannt + plausibel -> echte Bezeichnung ("Frau M.")
    #   - sonst neutral            -> "die Patientin/der Patient"
    # Vorteil ggü. konkretem Beispielnamen ("Frau M." hart im Few-Shot): kein
    # Falsch-Namen-Leak moeglich. substitute_patient_placeholders (Output-Seite)
    # ist damit nur noch Sicherheitsnetz, nicht mehr Lasttraeger.
    #
    # Sicherheits-Check: bei bekanntem Namen Substitution NUR wenn full_ref
    # plausibel kurz und frei von "Klient"/"Patient" ist (zweite
    # Verteidigungslinie hinter parse_explicit_patient_name in extraction.py).
    ref = "die Patientin/der Patient"  # neutraler Default wenn kein Name bekannt
    if patient_name and patient_name.get("initial"):
        anrede_p = patient_name.get("anrede") or ""
        initial_p = patient_name["initial"]
        full_ref = f"{anrede_p} {initial_p}".strip()  # z.B. "Frau M." oder nur "M."
        full_ref_low = full_ref.lower()
        is_safe_ref = (
            len(full_ref) <= 12
            and "klient" not in full_ref_low
            and "patient" not in full_ref_low
            and full_ref not in ("", ".", "Frau .", "Herr .")
        )
        if is_safe_ref:
            ref = full_ref

    # "Herr/[Patient/in]" zuerst (Alt-Beispiele), dann die Einzeltoken.
    final_prompt = final_prompt.replace("Herr/[Patient/in]", ref)
    final_prompt = final_prompt.replace("[Patient/in]", ref)
    final_prompt = final_prompt.replace("[Name]", ref)

    return final_prompt


def build_user_content(
    workflow: str,
    transcript: Optional[str] = None,
    fokus_themen: Optional[str] = None,
    selbstauskunft_text: Optional[str] = None,
    vorbefunde_text: Optional[str] = None,
    verlaufsdoku_text: Optional[str] = None,
    antragsvorlage_text: Optional[str] = None,
    vorantrag_text: Optional[str] = None,
    prozessreflexion_text: Optional[str] = None,
    diagnosen: Optional[list[str]] = None,
    patient_name: Optional[dict] = None,
    # Backwards-Compat: alter Parameter custom_prompt wurde mit v18 entfernt
    # (Workflow-Anweisungen leben jetzt im System-Prompt). Wir akzeptieren
    # ihn weiterhin in der Signatur, ignorieren ihn aber bewusst, damit
    # Legacy-Aufrufer nicht brechen.
    custom_prompt: Optional[str] = None,
) -> str:
    """
    Baut den User-Content-Block zusammen.

    Parameter-Zuordnung (jeder hat genau EINE Bedeutung):
      transcript:          Transkript eines Gesprächs (aus Audio oder direkt eingegeben)
      fokus_themen:        Therapeuten-Stichpunkte / Fokus-Themen / Schwerpunkte
      selbstauskunft_text: P2: Selbstauskunft des Klienten
      vorbefunde_text:     P2: Berichte früherer Therapeuten/Kliniken
      verlaufsdoku_text:   P3/P4: Verlaufsdokumentation der aktuellen Behandlung
      antragsvorlage_text: P3/P4: Aktueller Bericht (EB/VA) mit Anamnese/Diagnosen
      vorantrag_text:      Folgeverlängerung: Vorheriger Bericht mit Verlauf/Anamnese
      prozessreflexion_text: P4: Abschlussreflexion des Klienten (v19.13, optional)
      diagnosen:           ICD-Codes (explizit oder aus Antragsvorlage)
      patient_name:        Dict {anrede, vorname, nachname, initial} – explizit uebergeben
                           oder aus Unterlagen extrahiert. Wird als expliziter Hinweis
                           am Anfang des User-Blocks eingefuegt.
      custom_prompt:       [DEPRECATED v18] Wird ignoriert. Vor v18 enthielt der
                           User-Block einen THERAPEUTEN-HINWEIS aus custom_prompt -
                           in v18 wandern Workflow-Anweisungen direkt in den
                           System-Prompt (siehe build_system_prompt).
    """
    # custom_prompt ist absichtlich nicht in Verwendung (siehe Docstring).
    _ = custom_prompt
    parts = []

    # ── Opt 4: Quelltext-Deduplikation ───────────────────────────────────────
    # Whisper halluziniert bei Stille manchmal denselben Satz mehrfach;
    # PDFs aus Confluence enthalten Header die ueber Seitenumbrueche dupliziert
    # werden; Klinik-Stilvorlagen werden manchmal versehentlich doppelt
    # eingefuegt. Wir lassen alle Quelltext-Felder einmal durch
    # deduplicate_paragraphs laufen, bevor sie ans LLM gehen.
    #
    # Local-Import: kein Zirkelschluss, weil llm.py NICHT aus prompts.py
    # importiert. deduplicate_paragraphs ist case-insensitive und behaelt
    # die Reihenfolge der erstmaligen Vorkommen bei.
    try:
        from app.services.llm import deduplicate_paragraphs as _dedup
    except ImportError:
        # Fallback fuer Test-Umgebungen ohne app-Paket: Identity-Funktion.
        def _dedup(text: str) -> str:
            return text

    def _dedup_safe(text: Optional[str]) -> Optional[str]:
        if not text or not text.strip():
            return text
        try:
            return _dedup(text)
        except Exception:
            # Deduplikation darf NIE den Job killen - im Zweifel Original
            return text

    transcript          = _dedup_safe(transcript)
    selbstauskunft_text = _dedup_safe(selbstauskunft_text)
    vorbefunde_text     = _dedup_safe(vorbefunde_text)
    verlaufsdoku_text   = _dedup_safe(verlaufsdoku_text)
    antragsvorlage_text = _dedup_safe(antragsvorlage_text)
    vorantrag_text      = _dedup_safe(vorantrag_text)
    prozessreflexion_text = _dedup_safe(prozessreflexion_text)
    # Bewusst NICHT dedupliziert: fokus_themen (Therapeuten-Stichpunkte
    # haben oft kurze, absichtlich aehnliche Eintraege), diagnosen, custom_prompt.

    # Expliziter Patientenname ganz oben im User-Block (wenn verfuegbar)
    # Sicherheits-Check: nur ausgeben wenn initial plausibel kurz ist und
    # nicht "klient"/"patient" enthaelt - sonst landet ein Hinweistext
    # wie "die Klientin/der Klient" als Namenskuerzel im Prompt.
    if patient_name and patient_name.get("initial"):
        anrede = patient_name.get("anrede") or ""
        initial = patient_name["initial"]
        initial_low = initial.lower()
        is_safe_initial = (
            len(initial) <= 6
            and "klient" not in initial_low
            and "patient" not in initial_low
        )
        if is_safe_initial:
            if anrede:
                parts.append(f"AKTUELLER PATIENT: {anrede} {initial} (verwende ausschliesslich diese Bezeichnung)")
            else:
                parts.append(f"AKTUELLER PATIENT: Namenskuerzel '{initial}' (verwende ausschliesslich diese Bezeichnung)")

    # Datenschutz-Namensregel EINMAL pro User-Content-Block ganz oben.
    # Frueher (v12) wurde dieser Block in jedem Workflow-Zweig redundant
    # eingefuegt (bei P3/P4 sogar mehrfach im Gesamt-Prompt). Das hat ~400
    # Tokens pro Job verschwendet und teilweise widerspruechliche Varianten
    # produziert. Jetzt: einmalig hier, alle Workflow-Zweige bleiben unberuehrt.
    parts.append(NAMENSREGEL)

    if workflow == "dokumentation":
        # Patch A (v17/v18): Sandwich-Pattern.
        # Stichpunkte stehen vor dem Transkript (sonst Lost-in-the-middle),
        # mit Verbindlichkeitssprache und Mapping auf die vier Abschnitte.
        # Erinnerung am Ende ruft sie unmittelbar vor der Generierung
        # nochmal ins Gedaechtnis (Recency-Effekt).
        if fokus_themen:
            parts.append(
                "THERAPEUTISCHE SCHWERPUNKTE – VERBINDLICH UMZUSETZEN:\n"
                "Die folgenden Punkte sind die wichtigsten Inhalte des Therapeuten "
                "und MUESSEN explizit im Bericht auftauchen. Ordne sie den vier "
                "Abschnitten zu:\n"
                "- Reframings, Hypothesen, Sinnzuschreibungen → Abschnitt "
                "'Hypothesen und Entwicklungsperspektiven'\n"
                "- Konkrete Aufgaben, Uebungen, Impulse → Abschnitt 'Einladungen'\n"
                "- Beobachtungen zu Symptomen, Anteilen, Erleben → Abschnitt "
                "'Relevante Gespraechsinhalte'\n"
                "- Anliegen oder Gespraechsziel → Abschnitt 'Auftragsklaerung'\n\n"
                "Falls ein Punkt im Transkript nicht direkt belegt ist, formuliere "
                "ihn trotzdem als therapeutische Hypothese ('Es laesst sich "
                "verstehen als...', 'Reframing-Angebot war...').\n\n"
                f"SCHWERPUNKTE:\n{fokus_themen}"
            )
        if transcript:
            label = (
                "TRANSKRIPT DES GESPRÄCHS (Belegmaterial fuer die obigen Schwerpunkte):"
                if fokus_themen
                else "TRANSKRIPT DES GESPRÄCHS:"
            )
            parts.append(f"{label}\n{transcript}")
        # Sandwich-Erinnerung
        if fokus_themen:
            parts.append(
                "ERINNERUNG – PRUEFE VOR DEM SCHREIBEN:\n"
                "Bevor du jeden der vier Abschnitte beginnst: Welcher der oben "
                "genannten THERAPEUTISCHEN SCHWERPUNKTE gehoert hierhin? Setze "
                "ihn explizit um – nicht nur als beilaeufige Erwaehnung, sondern "
                "als zentralen Inhalt des passenden Abschnitts."
            )
        if parts:
            parts.append("Erstelle jetzt die klinische Dokumentation gemäß den Anweisungen.")
        else:
            parts.append(
                "Bitte Verlaufsnotiz anhand der verfügbaren Informationen erstellen."
            )

    elif workflow == "anamnese":
        if selbstauskunft_text:
            parts.append(f"SELBSTAUSKUNFT DES KLIENTEN:\n{selbstauskunft_text}")
        if vorbefunde_text:
            parts.append(f"VORBEFUNDE / WEITERE BEFUNDE:\n{vorbefunde_text}")
        if transcript:
            parts.append(f"AUFNAHMEGESPRÄCH (TRANSKRIPT):\n{transcript}")
        if diagnosen:
            parts.append(f"DIAGNOSEN: {', '.join(diagnosen)}")
        parts.append("Anamnese und psychopathologischen Befund erstellen.")

    elif workflow == "verlaengerung":
        if antragsvorlage_text:
            parts.append(
                f"ANTRAGSVORLAGE / VORHERIGER ANTRAG"
                f" (Quelle für Diagnosen, Anamnese, Name, Geschlecht):\n{antragsvorlage_text}\n"
                "Entnimm Diagnosen, Anamnese-Informationen, Name und Geschlecht aus dieser Vorlage."
            )
        if verlaufsdoku_text:
            parts.append(f"VERLAUFSDOKUMENTATION (aktuelle Sitzungen):\n{verlaufsdoku_text}")
        if diagnosen:
            parts.append(f"DIAGNOSEN DES AKTUELLEN PATIENTEN: {', '.join(diagnosen)}")
        if fokus_themen:
            parts.append(f"THERAPEUTISCHE STICHPUNKTE / BESONDERE EREIGNISSE:\n{fokus_themen}")
        parts.append(
            "Verfasse jetzt den Abschnitt – er heißt je nach Krankenkasse entweder "
            "'Bisheriger Verlauf und Begründung der Verlängerung' oder "
            "'Verlauf und Begründung der weiteren Verlängerung'. "
            "Verwende den Sektionsnamen der in der Antragsvorlage steht, "
            "falls vorhanden – sonst: 'Bisheriger Verlauf und Begründung der Verlängerung'. "
            "Nur diesen Abschnitt – keine anderen Teile des Antrags. "
            # v13: Längenangabe entfernt - Längenanker steht zentral via resolve_length_anchor()
            "Ausschließlich auf Basis der obigen Quellen – "
            "keine Informationen erfinden die nicht in den Quellen stehen."
        )

    elif workflow == "folgeverlaengerung":
        # Vorheriger Verlängerungsantrag: Quelle für Anamnese, Diagnosen, bisherigen Verlauf
        if vorantrag_text:
            parts.append(
                f"VORHERIGER VERLÄNGERUNGSANTRAG"
                f" (Quelle für Diagnosen, Anamnese, bisherigen Verlauf, Name, Geschlecht):\n"
                f"{vorantrag_text}\n"
                "Entnimm Diagnosen, Anamnese, Name und Geschlecht aus diesem Dokument. "
                "Der bisherige Verlaufsabschnitt zeigt was bereits bearbeitet wurde – "
                "der NEUE Text soll daran ANKNÜPFEN und den Verlauf SEITDEM beschreiben."
            )
        # Folgeverlängerungs-Vorlage (ohne Verlaufsabschnitt)
        if antragsvorlage_text:
            parts.append(
                f"FOLGEVERLÄNGERUNGS-VORLAGE (leere Vorlage für den neuen Antrag):\n{antragsvorlage_text}\n"
                "Diese Vorlage zeigt die Struktur des neuen Antrags. "
                "Nur den Verlaufsabschnitt ausfüllen."
            )
        if verlaufsdoku_text:
            parts.append(
                f"VERLAUFSDOKUMENTATION (Sitzungen seit dem letzten Antrag):\n{verlaufsdoku_text}\n"
                "WICHTIG: Konzentriere dich auf die Entwicklung SEIT dem letzten "
                "Verlängerungsantrag. Frühere Sitzungen sind im vorherigen Antrag beschrieben."
            )
        if diagnosen:
            parts.append(f"DIAGNOSEN DES AKTUELLEN PATIENTEN: {', '.join(diagnosen)}")
        if fokus_themen:
            parts.append(f"THERAPEUTISCHE STICHPUNKTE / BESONDERE EREIGNISSE:\n{fokus_themen}")
        parts.append(
            # v13 Ä2: STRUKTUR-ERINNERUNG am Ende des User-Turns.
            # Wiederholt den Strukturzwang aus dem System-Prompt direkt vor der Generation.
            "Verfasse jetzt den Abschnitt. Format zwingend:\n"
            "Zeile 1: 'Verlauf und Begründung der weiteren Verlängerung'\n"
            "Zeile 2: leer\n"
            "Zeile 3+: Fließtext, beginnt mit 'Wir' / 'Seit dem letzten Antrag' / 'In unserer weiteren Arbeit'.\n"
            "Inhaltlich: kurzer Rückbezug auf den bisherigen Verlauf, dann Entwicklung SEITDEM. "
            "Nur diesen Abschnitt – keine Anamnese, keine Diagnosen, keine Stammdaten. "
            # v13: Längenangabe entfernt - Längenanker steht zentral via resolve_length_anchor()
            "Ausschließlich auf Basis der obigen Quellen."
        )

    elif workflow == "akutantrag":
        if antragsvorlage_text:
            parts.append(
                f"AKUTANTRAGS-VORLAGE"
                f" (Quelle für Anamnese, Befund, Diagnosen, Name, Geschlecht):\n{antragsvorlage_text}\n"
                "Entnimm alle Informationen aus diesem Dokument: "
                "Aktuelle Anamnese, Problemrelevante Vorgeschichte, Psychischer Befund, "
                "Einweisungsdiagnosen."
            )
        if verlaufsdoku_text:
            parts.append(
                f"ERGÄNZENDE INFORMATIONEN (Aufnahmegespräch / Verlaufsdoku):\n{verlaufsdoku_text}"
            )
        if diagnosen:
            parts.append(f"EINWEISUNGSDIAGNOSEN: {', '.join(diagnosen)}")
        if fokus_themen:
            parts.append(f"BESONDERE HINWEISE:\n{fokus_themen}")
        # Primer-Muster: Standardformulierung als BEGINN des zu generierenden Texts.
        # Statt das Modell anzuweisen "schreibe die Formel", geben wir sie direkt
        # vor und das Modell schreibt nahtlos weiter (Completion-Modus statt Instruktions-Modus).
        # Das verhindert das "Formel-schreiben-und-stoppen"-Problem.
        parts.append(
            "Vervollständige jetzt den Abschnitt 'Begründung für Akutaufnahme'. "
            "Der Text beginnt bereits mit der Pflicht-Standardformulierung (siehe unten). "
            "Füge direkt dahinter einen Wir-Satz an und begründe dann ausführlich "
            "mit konkreten Symptomen und Risiken aus der Antragsvorlage. "
            "Schreibe NUR den Inhalt des Abschnitts ohne Überschrift.\n\n"
            # v13: Längenangabe entfernt - Längenanker steht zentral via resolve_length_anchor()
            "BEGINN DES ABSCHNITTS (wörtlich so übernehmen, dann direkt weiterschreiben):\n"
            "Folgende Krankheitssymptomatik macht in der Art und Schwere sowie unter "
            "Berücksichtigung der Beurteilung des Einweisers und unseres ersten klinischen "
            "Eindruckes ein stationäres Krankenhaussetting akut notwendig:\n\n"
            "Wir nehmen [Patienteninitiale] schwer belastet auf."
        )

    elif workflow == "entlassbericht":
        if antragsvorlage_text:
            parts.append(
                f"VORHANDENER VERLÄNGERUNGSANTRAG / VORBERICHT"
                f" (Quelle für Diagnosen, Anamnese, Befund, Name, Geschlecht):\n{antragsvorlage_text}\n"
                "Entnimm Diagnosen, Anamnese, psychopathologischen Befund, Name und Geschlecht aus diesem Dokument."
            )
        if verlaufsdoku_text:
            parts.append(f"VERLAUFSDOKUMENTATION (alle Sitzungen):\n{verlaufsdoku_text}")
        # v19.13: Abschlussreflexion des Klienten (optional). Instruktionen
        # BEWUSST nur hier im konditionalen User-Content-Block, NICHT im
        # statischen BASE_PROMPTS["entlassbericht"] - eine permanente
        # Reflexions-Anweisung ohne vorhandene Quelle waere ein
        # Halluzinationsrisiko (Modell erfindet eine Reflexion).
        if prozessreflexion_text:
            parts.append(
                f"PROZESSREFLEXION DES KLIENTEN (Abschlussreflexion in eigenen Worten):\n"
                f"{prozessreflexion_text}\n\n"
                "EINBAU DER PROZESSREFLEXION - VERBINDLICH:\n"
                "1. Widme der Reflexion einen eigenen Absatz am ENDE des "
                "Behandlungsverlaufs (unmittelbar vor der Epikrise). Leite ihn "
                "sinngemäß ein mit 'Zum Abschluss ihres Prozesses reflektierte "
                "die Klientin ...' bzw. 'Zum Abschluss seines Prozesses "
                "reflektierte der Klient ...' (Geschlecht gemäß den Quellen).\n"
                "2. Gib die Reflexionsinhalte AUSSCHLIESSLICH in indirekter Rede "
                "(Konjunktiv I) wieder - keine wörtlichen Zitate.\n"
                "3. Greife insbesondere auf: erlebte Symptomveränderungen, "
                "zentrale Erkenntnisse, als hilfreich erlebte Methoden und die "
                "vom Klienten benannte Essenz des Aufenthalts.\n"
                "4. Benennt die Reflexion offene Themen oder Wünsche für die "
                "ambulante Weiterarbeit, lasse diese in die Therapieempfehlungen "
                "einfließen (quellenbelegt).\n"
                "5. Enthält die Reflexion Feedback oder Dank an das "
                "therapeutische Team, übernimm diese Inhalte NICHT in den "
                "Bericht (internes Feedback, nicht für den Kostenträger).\n"
                "6. Nur tatsächlich in der Reflexion Genanntes wiedergeben - "
                "nichts hinzuerfinden."
            )
        if diagnosen:
            parts.append(f"DIAGNOSEN DES AKTUELLEN PATIENTEN: {', '.join(diagnosen)}")
        if fokus_themen:
            parts.append(f"THERAPEUTISCHE SCHWERPUNKTE / BESONDERE THEMEN:\n{fokus_themen}")
        parts.append(
            "Verfasse jetzt den psychotherapeutischen Verlaufsteil als zusammenhängenden "
            "Fließtext ohne Überschriften. "
            # v13: absolute Wortzahlen durch Proportionen ersetzt - Gesamtlänge zentral via resolve_length_anchor()
            "Behandlungsverlauf (Hauptteil, ~70% des Textes), Epikrise (~20%) und "
            "Therapieempfehlungen (~10%) fliessen nahtlos ineinander. "
            "Ausschliesslich auf Basis der obigen Quellen – "
            "keine Informationen erfinden die nicht in den Quellen stehen."
        )

    # v18 Architekturwechsel:
    # Frueher wurde am Ende ein THERAPEUTEN-HINWEIS-Block aus custom_prompt
    # angehaengt - das war die Quelle der Doppelung mit dem BASE_PROMPT.
    # In v18 wandern Workflow-Anweisungen direkt in den System-Prompt
    # (siehe build_system_prompt > workflow_instructions). Der User-Content
    # enthaelt nur noch Patientendaten, Quellen und ggf. Stichpunkte
    # (fokus_themen) - keine wiederholten Workflow-Anweisungen.
    return "\n\n".join(parts)
