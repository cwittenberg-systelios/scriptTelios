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
# Wird in build_system_prompt einmalig in den finalen Prompt aufgenommen
# (nicht mehr pro Workflow im build_user_content – das war Token-Verschwendung).
NAMENSREGEL = (
    "DATENSCHUTZ – NAMENSFORMAT (gilt fuer den gesamten Text):\n"
    "Verwende AUSSCHLIESSLICH den ersten Buchstaben des Nachnamens mit Punkt: "
    "Initialen des AKTUELLEN Patienten aus den Unterlagen "
    "(z.B. wenn der Patient 'Andreas Reif' heisst: 'Herr R.', "
    "wenn 'Maria Schmidt': 'Frau S.') – NIEMALS den vollen Nachnamen, "
    "NIEMALS den Vornamen, NIEMALS Namen aus Stilbeispielen. "
    "Selbst wenn der volle Name in den Quellen steht: nur Initiale verwenden."
)


# ── Fachglossar ──────────────────────────────────────────────────────────────

KLINISCHES_GLOSSAR = """FACHLICHES REFERENZWISSEN (sysTelios-Klinik, kompakt):

Therapieansaetze (nutze nur was im Material vorkommt - siehe Quellentreue-Regel):
- IFS / Anteilemodell: Manager (proaktiv schuetzend: Kontrolle, Perfektionismus,
  Selbstaufgabe), Feuerwehr (reaktiv: Dissoziation, Sucht, Selbstverletzung),
  Exile (Schmerz, Scham, Trauma), Self/Steuerungsposition. Ziel: Anteile
  entlasten, Self-Leadership.
- Hypnosystemik (G. Schmidt): Ressourcenaktivierung, Seitenmodell,
  Koerpersymptome als Beduerfnisrueckmeldung.
- Systemische Therapie: Auftragsklaerung, Reframing, Externalisierung,
  Stuhlarbeit, zirkulaere Fragen.
- Biographiearbeit: Sinnhaftigkeit frueher Strategien wuerdigen.
- Trauma: Window of Tolerance, Stabilisierung, Embodiment.
- AMDP-Befund: Bewusstsein, Orientierung, Aufmerksamkeit, Denken (formal/inhaltl.),
  Wahrnehmung, Ich-Erleben, Affekt, Antrieb, Suizidalitaet.

Therapieangebot: Einzelgespraeche (2-3/Wo), Gruppentherapie (Gespraechs-, Kunst-,
Musik-, Koerper-, Bewegungstherapie), Bezugsgruppe, Paar-/Familiengespraeche.

Klinik-typische Wendungen (nur einsetzen wenn sachlich passend, nicht reflexhaft):
- "Mithilfe des Therapiekonzepts gelang es [Name] die intrapsychischen
  Erlebensmuster zu verstehen."
- "Die Alltagstauglichkeit ist derzeit noch nicht gegeben."
- "Eine tragfaehige Stabilitaet fuer den ambulanten Kontext ist noch nicht erreicht."
- Befund-Standardphrasen: "bewusstseinsklar, allseits orientiert" /
  "Affekt situationsadaequat schwingungsfaehig" /
  "formalgedanklich gruebelnd, eingeengtes Denken"\
"""


# ── Psychopathologischer Befund Vorlage ──────────────────────────────────────
# Exakte Vorlage aus der Klinik. Wird durch Informationen aus der Selbstauskunft
# befüllt – Lücken werden geschlossen, Mehrfachoptionen auf die passende reduziert.
# NICHT verändern – ist eine klinisch validierte Standardstruktur.

BEFUND_VORLAGE = """Im Gespräch offen, wach, bewusstseinsklar, zu allen Qualitäten orientiert. Konzentration subjektiv {konzentration}. Auffassung, Merkfähigkeit und Gedächtnis intakt. Formalgedanklich {formalgedanke}, keine Denkverlangsamung, {fokus_denken}. {phobien_angst}. {Zwänge}. {vermeidung}. Kein Anhalt für Wahn oder Sinnestäuschungen, keine Ich-Störungen (z.B. Depersonalisation, Derealisation, Dissoziation). Stimmungslage {stimmung}, affektive Schwingungsfähigkeit {schwingung} bei insgesamt {affektlage} Affektlage. {freud_interessen}. {erschöpfung}. Antrieb {antrieb}. {hoffnung_insuffizienz}. {schuldgefühle}. Selbstwertgefühl ist {selbstwert}. Gefühlsregulation ist {gefühlsregulation}. Impulskontrolle ist {impulskontrolle}. {ambivalenz}. {innere_unruhe}. {zirkadian}. {schlaf}. Appetenz {appetenz}. {aggressiv_selbstverletzend}. {sozialer_rückzug}. Essverhalten {essverhalten}. {suchtverhalten}. {somatisierung}. {suizidalität_vergangenheit}. Aktuelle Verneinung von lebensüberdrüssigen und suizidalen Gedanken, keine suizidale Handlungsplanung oder Handlungsvorbereitung. Zum Zeitpunkt der Aufnahme von akuter Suizidalität klar distanziert."""

# ── Few-Shot-Beispiele ────────────────────────────────────────────────────────

# Lehrsatz vor allen Two-Shot-Bloecken (Massnahme B).
# Macht die Variable explizit: Vokabular folgt dem Material, nicht dem Workflow.
# Wird vor jedem Beispielpaar eingefuegt, damit das Modell die Beispiele als
# kontrastive Instanzen einer Regel liest, nicht als zwei beliebige Muster.

FEW_SHOT_KONTRAST_HINWEIS = """\
WICHTIG – ZWEI BEISPIELE MIT KONTRASTIERENDER SPRACHE:

Die folgenden zwei Beispiele zeigen denselben Texttyp in unterschiedlichem
Vokabular. Beachte: Die Wortwahl der Doku folgt der Sprache des Transkripts
und der Stichpunkte – sie wird NICHT vom Workflow vorgegeben.

- Beispiel 1: Input MIT IFS-/Anteilevokabular ("Schutzschild", "Teil") →
  Output verwendet IFS-Sprache.
- Beispiel 2: Input OHNE Verfahrensvokabular ("ich ziehe mich zurueck",
  "ich kontrolliere mich") → Output verwendet allgemein-deskriptive
  systemische Sprache, OHNE Verfahren zu benennen oder zu erfinden.

Wende dieselbe Regel auf den aktuellen Fall an: pruefe, welche Sprache im
Material vorkommt, und schreibe in dieser Sprache. Erfinde KEIN Verfahren.

────────────────────────────────────────────────────────────────────────
"""


# Beispiel 1 (gekuerzt P4) – IFS-Vokabular im Input -> IFS im Output.
_FEW_SHOT_DOK_IFS = """\
BEISPIEL 1 – Transkript MIT IFS-Vokabular:

EINGABE:
[A]: Was war das fuer ein Teil, der sich zurueckgezogen hat?
[B]: So ein Schutzschild. Der will nicht, dass ich verletzt werde.
STICHPUNKTE: Rueckzugsmuster, IFS-Arbeit mit Schutzanteil

AUSGABE:

Auftragsklärung

Im Mittelpunkt stand das wiederkehrende Anspannungserleben von [Patient/in] \
im Vorfeld familiärer Begegnungen. Ziel war es, den Schutzmechanismus zu \
verstehen und erste Kontaktaufnahme mit diesem Anteil zu ermöglichen.

Relevante Gesprächsinhalte

Im Sinne des IFS zeigte sich ein aktiver Manager-Anteil in Form eines inneren \
Schutzschildes, der proaktiv Kontakt zu potenziell verletzenden Situationen vermeidet.

Hypothesen und Entwicklungsperspektiven

Das Rückzugsmuster lässt sich als sinnvolle Schutzleistung verstehen. \
Wenn der Schutzanteil erfährt, dass er nicht mehr allein für die Sicherheit \
zuständig sein muss, kann [Patient/in] schrittweise neue Beziehungserfahrungen machen.

Einladungen

[Patient/in] wurde eingeladen, in dieser Woche nach innen zu horchen, wenn sich \
der Schutzschild aktiviert, und ihm innerlich zu danken.\
"""


# Beispiel 2 (gekuerzt P4) – ohne Verfahrensvokabular -> deskriptive systemische Sprache.
_FEW_SHOT_DOK_DESKRIPTIV = """\
BEISPIEL 2 – Transkript OHNE Verfahrensvokabular:

EINGABE:
[A]: Was haben Sie ueber sich gedacht in den Momenten?
[B]: Dass ich versagt habe. Dass ich nicht gut genug bin. Das alte Lied.
STICHPUNKTE: Selbstabwertung nach Praesentation, biographischer Bezug Schulzeit

AUSGABE:

Auftragsklärung

Im Mittelpunkt stand eine berufliche Belastungssituation, die bei [Patient/in] eine \
ausgeprägte Selbstabwertung ausgelöst hatte.

Relevante Gesprächsinhalte

[Patient/in] berichtete von einer als gescheitert erlebten Präsentation, gefolgt von \
intensivem Grübeln und Selbstabwertung ("ich habe versagt", "ich bin nicht gut genug"). \
Im Gespräch zeigte sich eine klare Verbindung zu einer früheren Beschämungserfahrung \
in der Schulzeit.

Hypothesen und Entwicklungsperspektiven

Die Selbstabwertung lässt sich als biographisch verständliche Bewältigungsstrategie \
verstehen, die früh entwickelt wurde, um erneuten Beschämungserfahrungen zuvorzukommen.

Einladungen

[Patient/in] wurde eingeladen, selbstkritische Momente bewusst zu bemerken und sich \
zu fragen, wovor diese innere Stimme schützen will.\
"""


FEW_SHOT_DOKUMENTATION = (
    FEW_SHOT_KONTRAST_HINWEIS
    + _FEW_SHOT_DOK_IFS
    + "\n\n────────────────────────────────────────────────────────────────────────\n\n"
    + _FEW_SHOT_DOK_DESKRIPTIV
)

FEW_SHOT_ANAMNESE = """\
STILVORLAGE (zeigt den erwarteten Schreibstil – KEINE Inhalte übernehmen):

Schreibe die Anamnese als zusammenhängenden FLIESSTEXT ohne Zwischenüberschriften.
Die Themen fließen natürlich ineinander über, wie ein erfahrener Therapeut berichten würde.
Alle Inhalte MÜSSEN aus der Selbstauskunft des AKTUELLEN Patienten stammen.
Steht eine Information NICHT in der Selbstauskunft: schreibe 'nicht erhoben'.

Beispiel-Einstieg (NUR als Stilreferenz):
'Herr/[Patient/in] stellt sich mit dem Hauptanliegen vor, ... . Die Symptomatik begann vor
etwa ... Monaten im Kontext von ... . Seither habe sich ... . Vorbehandlungen umfassen
... . Familiär sei bekannt, dass ... . Beruflich sei er/sie ... . Der Schlaf sei ...,
der Appetit ... . An Ressourcen nennt er/sie ... .'

WICHTIG: Schreibe KEINE Überschriften wie 'Vorstellungsanlass:', 'Aktuelle Erkrankung:' etc.
Alle Inhalte müssen von DIESEM Patienten stammen – KEINE Inhalte aus dem Beispiel übernehmen.\
"""

# Beispiel 1 (gekuerzt P4) – Wir-Form, MIT Anteilearbeit-Vokabular.
_FEW_SHOT_VA_ANTEILE = """\
BEISPIEL 1 – Verlauf MIT Anteilearbeit (Auszug, Stilreferenz):

WICHTIG: Schreibe konsequent in der WIR-FORM aus klinischer Perspektive
("wir nahmen auf", "wir erlebten", "uns gelang es", "in unserer Arbeit").

Wir nahmen [Patient/in] im bisherigen Verlauf des stationären Aufenthaltes \
unter anhaltendem innerem Druck, mit ausgeprägter Anspannung und emotionaler Ambivalenz \
auf. Im hypnosystemischen Einzelprozess konnten wir mithilfe der Anteilearbeit \
einen dominanten Kontrollanteil differenzieren, der biographisch vor dem Hintergrund \
invalidierender Beziehungserfahrungen verständlich wurde. Parallel sahen wir jüngere, \
verletzliche Anteile in Erscheinung treten.

Insgesamt sehen wir erste positive Entwicklungen wie eine verbesserte Reflexionsfähigkeit. \
Eine fuer den ambulanten Kontext notwendige tragfaehige Stabilitaet ist derzeit noch \
nicht ausreichend gegeben. Eine Verlängerung um weitere 14 Tage halten wir aus \
psychotherapeutischer Sicht für dringend indiziert.\
"""


# Beispiel 2 (gekuerzt P4) – Wir-Form, OHNE Verfahrenslabel.
_FEW_SHOT_VA_DESKRIPTIV = """\
BEISPIEL 2 – Verlauf OHNE Verfahrenslabel (Auszug, Stilreferenz):

Wir nahmen [Patient/in] zu Beginn des stationären Aufenthaltes deutlich erschöpft \
und in der Selbstwahrnehmung erheblich eingeschränkt auf. Im therapeutischen Verlauf \
gelang es zunehmend, die Verbindung zwischen aktueller Symptomatik und biographisch \
geprägten Bewältigungsmustern herzustellen.

Insgesamt sehen wir klinisch relevante Fortschritte in Reflexionsfähigkeit und \
Selbstwahrnehmung. Dennoch bestehen weiterhin ausgeprägte Anspannungszustände. \
Eine fuer den ambulanten Rahmen tragfaehige Stabilitaet ist derzeit nicht \
ausreichend gegeben. Eine Verlängerung um weitere 14 Tage halten wir aus \
psychotherapeutischer Sicht für dringend indiziert.\
"""


FEW_SHOT_VERLÄNGERUNG = (
    FEW_SHOT_KONTRAST_HINWEIS
    + _FEW_SHOT_VA_ANTEILE
    + "\n\n────────────────────────────────────────────────────────────────────────\n\n"
    + _FEW_SHOT_VA_DESKRIPTIV
)

# Beispiel 1 (gekuerzt P4) – hypnosystemische Anteilearbeit (Verfahren genannt).
_FEW_SHOT_EB_ANTEILE = """\
BEISPIEL 1 – Behandlung MIT hypnosystemischer Anteilearbeit (Auszug, Stilreferenz):

Zu Beginn des stationären Aufenthaltes formulierte [Patient/in] als zentrales Anliegen, \
wieder inneren Halt zu finden. Wir erlebten ihn/sie zu Therapiebeginn deutlich \
erschöpft, innerlich angespannt und in seinem/ihrem Selbstwert verunsichert.

Im Einzelprozess stand die hypnosystemische Anteilearbeit im Zentrum. Es zeigte sich \
eine innere Dynamik aus stark leistungsorientierten, kontrollierenden Anteilen, die \
biographisch eng mit frühen Beziehungserfahrungen verknüpft waren. Im Verlauf gelang \
es, diese inneren Ebenen zu differenzieren und aus einer selbstfürsorgelicheren \
Perspektive in Kontakt zu bringen.

Im Gesamtverlauf zeigte sich eine deutliche Symptomreduktion. Für den weiteren Verlauf \
ist eine kontinuierliche ambulante psychotherapeutische Begleitung dringend zu \
empfehlen.\
"""


# Beispiel 2 (gekuerzt P4) – deskriptiv-systemisch, ohne Verfahrensbenennung.
_FEW_SHOT_EB_DESKRIPTIV = """\
BEISPIEL 2 – Behandlung OHNE explizite Verfahrensbenennung (Auszug, Stilreferenz):

Zu Beginn des stationären Aufenthaltes formulierte [Patient/in] als zentrales Anliegen, \
einen Weg aus anhaltender depressiver Erschöpfung zu finden. Wir erlebten sie/ihn zu \
Therapiebeginn niedergedrückt, innerlich erschöpft und in der eigenen \
Handlungsfähigkeit deutlich eingeschränkt.

Im Einzelprozess arbeiteten wir an der Verbindung zwischen aktueller Symptomatik und \
biographisch geprägten Erlebensmustern. Im Vordergrund stand eine ausgeprägte Tendenz \
zur Selbstabwertung, die im Kontext invalidierender Erfahrungen in der Herkunftsfamilie \
verständlich wurde. Im Verlauf gelang zunehmend ein wohlwollenderer innerer Umgang mit \
eigenen Grenzen und ein deutlicher Zuwachs an Selbstmitgefühl.

Im Gesamtverlauf zeigte sich eine klinisch relevante Entwicklung hin zu mehr innerer \
Differenzierung. Für den weiteren Verlauf ist eine kontinuierliche ambulante \
psychotherapeutische Begleitung mit Schwerpunkt auf Selbstwertarbeit dringend zu \
empfehlen.\
"""


FEW_SHOT_ENTLASSBERICHT = (
    FEW_SHOT_KONTRAST_HINWEIS
    + _FEW_SHOT_EB_ANTEILE
    + "\n\n────────────────────────────────────────────────────────────────────────\n\n"
    + _FEW_SHOT_EB_DESKRIPTIV
)


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
        "Gliedere den Text in folgende vier Abschnitte mit den jeweiligen Überschriften:\n\n"
        "**Auftragsklärung**\n"
        "Beschreibe worum es der Klientin/dem Klienten ging und was das gemeinsame "
        "Ziel des Gesprächs war. Beispiel: 'Im Mittelpunkt stand...' oder "
        "'Frau M. kam mit dem Anliegen...' (verwende den tatsaechlichen Namen "
        "des Patienten – NICHT den Platzhalter '[Patient/in]').\n\n"
        "**Relevante Gesprächsinhalte**\n"
        "Schildere die wesentlichen Inhalte aus Sicht der Klientin/des Klienten: "
        "Symptome, Erlebensmuster, innere Anteile, Beziehungsdynamiken, Ressourcen. "
        "Konkrete Formulierungen statt allgemeiner Beschreibungen. "
        "Beschreibe Anteile und Erlebensmuster mit dem Vokabular, das im Transkript "
        "verwendet wurde. Wurden im Gespräch keine Verfahrensbegriffe genannt "
        "(z.B. IFS, Manager-Anteil, Self-Energy), schreibe in allgemein-deskriptiver "
        "systemischer Sprache und benenne kein Verfahren.\n\n"
        "**Hypothesen und Entwicklungsperspektiven**\n"
        "Formuliere systemische Hypothesen über Sinnzusammenhänge. "
        "Zeige Entwicklungsperspektiven auf - was wird möglich, wenn... "
        "Ressourcenorientiert und konkret.\n\n"
        "**Einladungen**\n"
        "Beschreibe die konkreten Aufgaben, Übungen oder Impulse die mitgegeben wurden "
        "- aktiv formuliert: 'Frau M. wurde eingeladen, ...' oder "
        "'Als Übung wurde vereinbart, ...' (verwende den tatsaechlichen Namen, "
        "NICHT '[Patient/in]')."
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
        "Du bist systemischer Psychotherapeut einer hypnosystemischen Klinik für "
        "Psychosomatik und Psychotherapie. Verfasse den Abschnitt "
        "'Bisheriger Verlauf und Begründung der Verlängerung' "
        "(auch: 'Verlauf und Begründung der weiteren Verlängerung') "
        "für einen Antrag auf Verlängerung der Kostenzusage bei der Krankenversicherung.\n\n"
        "INHALT (Reihenfolge einhalten):\n"
        "- Bisheriger Verlauf: was wurde konkret bearbeitet, welche Methoden eingesetzt. "
        "Benenne ein Verfahren (z.B. IFS, Anteilearbeit, Hypnosystemik, Körperarbeit) "
        "NUR DANN, wenn es in der Verlaufsdokumentation oder den Stichpunkten "
        "tatsaechlich erwaehnt wurde. Andernfalls beschreibe das Vorgehen neutral "
        "('einzeltherapeutische Arbeit', 'Gruppenarbeit', 'koerperorientierte "
        "Interventionen').\n"
        "- Konkrete Fortschritte – spezifisch und belegbar aus der Verlaufsdokumentation, "
        "keine allgemeinen Behauptungen\n"
        "- Noch ausstehende Therapieziele: was bleibt zu tun, warum ist weitere "
        "stationäre Behandlung notwendig\n"
        "- Medizinische Begründung: Belastbarkeit, Stabilität, soziale Integration, "
        "Entlassfähigkeit noch nicht erreicht\n"
        "- Geplante Maßnahmen und Prognose für den Verlängerungszeitraum"
    ),

    "folgeverlaengerung": (
        "Du bist systemischer Psychotherapeut einer hypnosystemischen Klinik für "
        "Psychosomatik und Psychotherapie. Verfasse den Abschnitt "
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
        "Du bist Arzt oder Psychologischer Psychotherapeut der sysTelios Klinik. "
        "Verfasse die 'Begründung für Akutaufnahme' eines AKUTANTRAGS an die "
        "Krankenversicherung für die Erstattung einer stationären Akutaufnahme.\n\n"
        "KONTEXT:\n"
        "Die Antragsvorlage enthält bereits Aktuelle Anamnese, Problemrelevante Vorgeschichte, "
        "Psychischen Befund und Einweisungsdiagnosen. Diese Informationen sind deine QUELLEN.\n\n"
        "INHALT der Begründung:\n"
        "- Warum ist ein stationäres Setting medizinisch AKUT notwendig?\n"
        "- Konkrete Symptome und Risiken aus den Quellen benennen\n"
        "- Ambulante Insuffizienz begründen (warum reicht ambulant nicht?)\n"
        "- Dekompensationszeichen und aktuelle Krisensituation\n"
        "- WIR-PERSPEKTIVE: 'Wir nehmen ... auf', 'Wir erleben ...', "
        "'Aus unserer Sicht ist eine ambulante Behandlung nicht ausreichend.'\n"
        "- LÄNGE: 150-350 Wörter, konkret und symptombezogen"
    ),

    "entlassbericht": (
        "Schreibe den psychotherapeutischen Verlaufsteil eines Entlassberichts "
        "als zusammenhängenden Fließtext ohne Überschriften, ohne Aufzählungen, "
        "ohne Einleitung und ohne Abschluss.\n\n"
        "INHALT – drei Teile nahtlos als Fließtext ineinander (ALLE DREI MÜSSEN VORKOMMEN):\n\n"
        "Teil 1 – BEHANDLUNGSVERLAUF (Hauptteil, ausführlich):\n"
        "Beschreibe ausführlich den therapeutischen Verlauf. Benenne eingesetzte "
        "Methoden NUR DANN (z.B. IFS/Anteilearbeit, hypnosystemisch, Stuhlarbeit, "
        "Biographiearbeit), wenn sie in der Verlaufsdokumentation oder Antragsvorlage "
        "tatsaechlich genannt sind oder klar erkennbar angewendet wurden. "
        "Andernfalls beschreibe das Vorgehen neutral ('einzeltherapeutische Arbeit', "
        "'Gruppenarbeit', 'koerperorientierte Interventionen'). "
        "Konkrete Wendepunkte und Entwicklungsschritte. "
        "Richtwert: ca. 100 Wörter pro Absatz. "
        "Wir-Perspektive: 'Wir erlebten...', 'Es gelang zunehmend...', 'Im Verlauf zeigte sich...'\n\n"
        "Teil 2 – EPIKRISE (kompakte Gesamtbewertung):\n"
        "Symptomatik-Entwicklung im Vergleich zu Aufnahme, was an inneren "
        "Schutzmustern entlastet werden konnte (in der im Material verwendeten Sprache), "
        "verbliebener Bedarf, Ressourcen, Prognose.\n\n"
        "Teil 3 – THERAPIEEMPFEHLUNGEN (kompakter Abschluss, DARF NICHT FEHLEN):\n"
        "Konkrete Empfehlungen für die ambulante Weiterbehandlung: "
        "Therapieform, Schwerpunkte, Frequenz, Nachsorge."
    ),
}


# ── Pflichtkern fuer Akutantrag (Backend, nicht editierbar) ──────────────────

BASE_PROMPT_AKUTANTRAG = (
    "FOKUS:\n"
    "Schreibe NUR den Abschnitt 'Begründung für Akutaufnahme' – keine Anamnese, "
    "keinen Befund, keine Diagnosen (diese stehen bereits in der Vorlage).\n\n"
    "STIL: Knappe medizinisch-klinische Sprache aus WIR-PERSPEKTIVE des aufnehmenden "
    "Klinikteams.\n"
    "WICHTIG ZUR WIR-PERSPEKTIVE: Nach der Standardformulierung MUSS der erste "
    "inhaltliche Satz mit 'Wir' beginnen ('Wir nehmen ... auf', 'Wir erleben ...'). "
    "NICHT mit '[Patient/in] präsentiert sich' oder '[Patient/in] berichtet'.\n"
    "LÄNGE: 200-350 Wörter.\n\n"
    "NAMENSFORMAT: Nur erster Buchstabe des Nachnamens des AKTUELLEN Patienten "
    "(z.B. 'Frau K.' / 'Herr S.'). NIEMALS einen Platzhalter (z.B. eckige Klammern "
    "um das Wort Patient/in) und niemals Namen aus dem Stilbeispiel verwenden.\n\n"
    "HALLUZINATIONSSCHUTZ – QUELLENREGEL:\n"
    "Jeder Satz MUSS auf eine konkrete Stelle in der Antragsvorlage "
    "zurückführbar sein. Keine Symptome, Diagnosen oder Risiken erfinden.\n"
)


# ── Rollenkontext (Präambel) ──────────────────────────────────────────────────

ROLE_PREAMBLE = (
    "Du bist ein klinisches Schreibsystem der sysTelios Klinik. "
    "Du erstellst professionelle medizinische Berichte (Entlassberichte, "
    "Kostenverlaengerungsantraege, Aufnahmebefunde, Verlaufsnotizen) und "
    "beginnst sofort mit dem Schreiben des angeforderten Dokuments - "
    "ohne Vorrede, ohne Verweigerung, ohne Meta-Kommentar.\n\n"
    "QUELLENTREUE BEI THERAPIEVERFAHREN (gilt fuer ALLE Workflows):\n"
    "Verwende ein konkretes Therapieverfahren (IFS, Hypnosystemik, Schematherapie, "
    "EMDR, Stuhlarbeit etc.) oder dessen Fachvokabular (Manager-Anteil, Exile, "
    "Self-Energy, Schutzschild, Modus, Schema) NUR DANN namentlich, wenn das "
    "Verfahren oder seine Begriffe in den Quellen (Transkript, Stichpunkte, "
    "Verlaufsdokumentation, Antragsvorlage) explizit vorkommen oder erkennbar "
    "angewendet wurden. Andernfalls schreibe in allgemein-systemischer Sprache "
    "('innerer Schutzmechanismus', 'Vermeidungsmuster', 'Selbstabwertung', "
    "'innere Kritikerstimme') und benenne KEIN Verfahren. Im Zweifel: lieber "
    "neutral-deskriptiv als ein Verfahren zu erfinden.\n\n"
    + KLINISCHES_GLOSSAR
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
        "statt 'Es zeigt sich ein Manager-Anteil der...'\n\n"
        "VERMEIDE THEORIE-ABDRIFT:\n"
        "- Der Abschnitt 'Hypothesen und Entwicklungsperspektiven' ist KEIN Lehrbuch-"
        "Eintrag. Er bleibt am konkreten Material des Gespraechs und benennt was "
        "Veraenderung im naechsten Schritt moeglich macht - nicht was theoretisch "
        "ueber den Patiententypus zu sagen waere.\n"
        "- KEINE Saetze die mit 'Die Suche nach X laesst sich als tiefe, "
        "biographisch verankerte Bewaeltigungsstrategie verstehen' beginnen, wenn "
        "im Gespraech nichts derartiges thematisiert wurde. Im Zweifel: konkret "
        "bleiben am Erleben.\n"
        "- Der Abschnitt 'Einladungen' ist 2-4 Saetze lang, nicht ein eigener Essay. "
        "Konkrete Aufgaben/Uebungen, keine erneute Reflexion ueber den Sinn.\n\n"
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
        "DIAGNOSEN gemäß ICD: {diagnosen}\n\n"
        "NICHT SCHREIBEN:\n"
        "– Keinen psychopathologischen Befund (wird separat generiert!)\n"
        "– Keine 'PSYCHOPATHOLOGISCHER BEFUND'-Sektion, kein 'AMDP'-Schema\n"
        "– Keine 'SYSTEMISCHE EINSCHÄTZUNG' oder Hypothesen-Abschnitte\n"
        "– Keine Bullet-Listen, keine Stichworte, keine Pipe-Separatoren ('|')\n"
        "– Keine Diagnosen-Wiederholung am Ende\n"
        "– Keine Therapieempfehlungen oder Behandlungspläne\n"
        "– Kein Markdown (keine **, keine ##, keine ---)\n"
        "– KEINEN ###BEFUND###-Separator (der Befund kommt in einem separaten Call)\n"
        "– KEINE 'NICHT-ERHOBEN-LITANEI': Wenn Informationen fehlen, fasse sie EINMAL "
        "kompakt zusammen ('Zu Vorbehandlungen, Medikation und Suchtanamnese liegen "
        "keine Angaben vor.') - NIEMALS Zeile fuer Zeile auflisten was alles nicht "
        "erhoben wurde. FALSCH: 'Er nennt keine Vorbehandlungen. Er nennt keine "
        "Medikamente. Er nennt keine Allergien. Er nennt keine Operationen...'. "
        "RICHTIG: Knapp im Fluss erwaehnen oder ganz weglassen.\n"
        "– KEINE Wiederholungen ganzer Absaetze. Wenn ein Thema schon einmal "
        "behandelt wurde, nicht im Befund-Stil nochmal aufrollen.\n\n"
        "QUALITÄTSANFORDERUNGEN:\n"
        "- QUELLENREGEL: Jeder Satz MUSS auf eine konkrete Stelle in den "
        "bereitgestellten Unterlagen (Selbstauskunft, Vorbefunde, Aufnahmegespräch) "
        "zurückführbar sein. Findest du keine Quelle → kompakt 'nicht erhoben' "
        "(siehe Regel oben - keine Litanei).\n"
        "- Lies die Selbstauskunft des AKTUELLEN Patienten sorgfältig. "
        "Schreibe über DIESEN Patienten – nicht über einen Beispielpatienten.\n"
        "- WENN DIE QUELLEN PRAKTISCH LEER SIND (weniger als ca. 100 verwertbare "
        "Woerter zum aktuellen Patienten): schreibe einen kurzen Hinweis-Absatz "
        "('Auf Basis der vorliegenden Unterlagen liessen sich nur folgende "
        "Eckpunkte erheben: ...') und beende den Text. Erfinde NICHTS aus dem "
        "Few-Shot-Beispiel, dem Glossar oder anderen Quellen.\n"
        "- Direkte Patientenzitate NUR wenn WÖRTLICH in der Selbstauskunft\n"
        "- NIEMALS erfinden: Beruf, Familienstand, Kinder, Wohnsituation, "
        "Vorbehandlungen, Medikamente, Suchtmittel, Diagnosen, Zeitangaben, "
        "auslösende Ereignisse, Testwerte, Zitate\n"
        "- LÄNGE Anamnese: Richtwert ca. 350-600 Wörter Fließtext. "
        "Falls weiter unten ein VERBINDLICHES TEXTLIMIT angegeben ist, "
        "hat dieses absolute Vorrang vor diesem Richtwert.\n\n"
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
        "WIR-PERSPEKTIVE des Therapeutenteams (verbindlich, nicht 3. Person/Passiv): "
        "Schreibe konsequent aus 'Wir'-Sicht: 'Wir nahmen [Patient/in] auf...', "
        "'In unserer Arbeit gelang es uns...', 'Wir erlebten [Patient/in] zunehmend...', "
        "'Im Einzelprozess konnten wir gemeinsam mit [Patient/in]...'. "
        "VERMEIDE Passivkonstruktionen wie 'es zeigte sich', 'konnte differenziert werden', "
        "'wurde bearbeitet'. Setze stattdessen das Wir-Subjekt aktiv: "
        "'wir sahen', 'wir bearbeiteten', 'es gelang uns'. "
        "Systemische Fachsprache wo inhaltlich passend. Fließtext, keine Aufzählungen.\n"
        "LÄNGE: Mindestens 400 Wörter. Konkret und patientenspezifisch.\n\n"
        "NAMENSFORMAT: Nur erster Buchstabe des Nachnamens des AKTUELLEN Patienten "
        "(z.B. 'Frau K.' / 'Herr S.'). NIEMALS einen Platzhalter (z.B. eckige Klammern "
        "um das Wort Patient/in) und niemals Namen aus dem Stilbeispiel verwenden, "
        "sowie 'die Klientin' / 'der Klient'.\n\n"
        "HALLUZINATIONSSCHUTZ – QUELLENREGEL:\n"
        "Jeder Satz MUSS auf eine konkrete Stelle in der Verlaufsdokumentation "
        "oder Antragsvorlage zurückführbar sein. Keine Therapieinhalte, Methoden, "
        "Fortschritte oder Zitate erfinden die nicht in den Quellen stehen. "
        "Im Zweifel weglassen statt erfinden.\n\n"
        "WICHTIG – STILBEISPIEL:\n"
        "Falls ein Stilbeispiel bereitgestellt wird: Übernimm Struktur, Gliederung "
        "und Länge exakt. Ersetze nur die patientenspezifischen Inhalte.\n\n"
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
        "STIL:\n"
        "WIR-PERSPEKTIVE des Therapeutenteams (verbindlich, nicht 3. Person/Passiv): "
        "Schreibe konsequent aus 'Wir'-Sicht: 'Seit dem letzten Antrag erlebten wir...', "
        "'In unserer weiteren Arbeit gelang es uns...', 'Wir konnten gemeinsam mit [Patient/in]...'. "
        "VERMEIDE Passivkonstruktionen wie 'es zeigte sich' oder 'konnte differenziert werden'. "
        "WICHTIG: Der erste Satz des Textes MUSS mit 'Wir' oder einer Wir-Konstruktion "
        "beginnen ('Wir erlebten ...', 'Seit dem letzten Antrag konnten wir ...', "
        "'In unserer weiteren Arbeit ...'). NICHT mit 'Im weiteren Verlauf' oder "
        "'Seither hat sich [Patient/in] ...' beginnen.\n"
        "Systemische Fachsprache wo inhaltlich passend. Fließtext, keine Aufzählungen.\n"
        "LÄNGE: Mindestens 400 Wörter. Konkret und patientenspezifisch.\n\n"
        "NAMENSFORMAT: Nur erster Buchstabe des Nachnamens des AKTUELLEN Patienten "
        "(z.B. 'Frau K.' / 'Herr S.'). NIEMALS einen Platzhalter (z.B. eckige Klammern "
        "um das Wort Patient/in) und niemals Namen aus dem Stilbeispiel verwenden, "
        "sowie 'die Klientin' / 'der Klient'.\n\n"
        "HALLUZINATIONSSCHUTZ – QUELLENREGEL:\n"
        "Jeder Satz MUSS auf eine konkrete Stelle in der Verlaufsdokumentation, "
        "dem vorherigen Antrag oder der Antragsvorlage zurückführbar sein. "
        "Keine Therapieinhalte, Methoden, Fortschritte oder Zitate erfinden. "
        "Im Zweifel weglassen statt erfinden.\n\n"
        "WICHTIG – STILBEISPIEL:\n"
        "Falls ein Stilbeispiel bereitgestellt wird: Übernimm Struktur, Gliederung "
        "und Länge exakt. Ersetze nur die patientenspezifischen Inhalte.\n\n"
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
        "STIL: Fließtext, Wir-Perspektive, systemische Fachsprache, "
        "konkret und patientenspezifisch – keine Allgemeinplätze.\n"
        "LÄNGE: Richtwert 600-900 Wörter gesamt. Wenn weiter unten ein VERBINDLICHES TEXTLIMIT "
        "ueber den Stilvorlagen angegeben wird, hat dieses absolute Vorrang. "
        "Vermeide unnötige Ausschmückungen und Wiederholungen.\n\n"
        "QUELLENREGEL: Jeder Satz MUSS auf eine konkrete Stelle in der "
        "Verlaufsdokumentation oder Antragsvorlage zurückführbar sein. "
        "Keine Therapieinhalte, Diagnosen, Methoden oder Zitate erfinden "
        "die nicht in den Quellen stehen. Im Zweifel weglassen.\n\n"
        + FEW_SHOT_ENTLASSBERICHT
    ),

    "akutantrag": BASE_PROMPT_AKUTANTRAG,
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


def _compute_style_constraints(style_text: str, skip_length: bool = False) -> str:
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
    """
    import re as _re

    words = style_text.split()
    word_count = len(words)
    if word_count < 30:
        return ""

    # Satzlaenge
    sentences = _re.split(r'[.!?]+', style_text)
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

    lines = ["\nSTIL-VORGABEN (unbedingt einhalten!):"]
    lines.append(f"- Satzlänge: durchschnittlich {int(avg_sentence_len)} Wörter pro Satz")
    lines.append(f"- Absatzlänge: durchschnittlich {int(avg_para_len)} Wörter pro Absatz")
    if is_fragment_style:
        lines.append(
            "- STILTYP: Stichwort-/Notiz-Stil (kurze Fragmente, oft ohne Satzsubjekt, "
            "Verben am Satzanfang). UEBERNIMM diesen Stil. Schreibe KEINE Vollsaetze "
            "mit Subjekt-Verb-Objekt-Struktur. Beispielform der Vorlage uebernehmen."
        )
    if fach_density > 0:
        lines.append(f"- Fachbegriffe: {'sparsam' if fach_density < 0.8 else 'moderat'} "
                      f"(ca. {fach_density} pro 100 Wörter)")
    else:
        lines.append("- Fachbegriffe: sehr sparsam einsetzen")
    if uses_wir:
        lines.append("- Perspektive: Wir-Form des Behandlungsteams "
                      "(\"Wir beobachteten...\", \"In unserer Arbeit...\"). "
                      "Der ERSTE Satz des Outputs MUSS mit 'Wir' oder einer "
                      "Wir-Konstruktion beginnen.")
    else:
        lines.append("- Perspektive: Dritte Person (Er/Sie-Form)")

    # Hartes Limit nur wenn nicht schon oben ueber VERBINDLICHES TEXTLIMIT gesetzt.
    # Vermeidet widerspruechliche Angaben zwischen diesem Einzel-Vorlagen-Limit
    # und dem aggregierten Limit aus derive_word_limits(alle Vorlagen).
    if not skip_length:
        lines.append(
            f"- TEXTLÄNGE (verbindlich): mindestens {limit_min} Wörter, "
            f"maximal {limit_max} Wörter. "
            f"Die Vorlage hat {word_count} Wörter – orientiere dich genau daran."
        )
    else:
        lines.append(
            f"- Absatzstruktur: die Vorlage hat ca. {len(paragraphs)} Absätze "
            f"(siehe TEXTLIMIT oben fuer die Gesamtlaenge)."
        )

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

    # Verbindliches Wortlimit aus Stilvorlagen anhängen (überschreibt BASE_PROMPT-Angaben)
    if word_limits is not None:
        limit_min, limit_max = word_limits
        base = base + (
            "\nVERBINDLICHES TEXTLIMIT (hat Vorrang vor allen anderen Laengenangaben "
            "im obigen Prompt, einschliesslich 'Mindestens X Woerter' / 'LAENGE: X-Y'): "
            f"mindestens {limit_min} Woerter, maximal {limit_max} Woerter. "
            f"Falls dieses Limit niedriger ist als eine BASE_PROMPT-Mindestangabe "
            f"(z.B. 'Mindestens 450 Woerter'): das BASE_PROMPT-Minimum ist UNGUELTIG, "
            f"das hier genannte Maximum ({limit_max}) gilt als harte Obergrenze. "
            f"Ueberschreite das Maximum NIEMALS - lieber kuerzer als zu lang.\n"
        )

    # Reihenfolge (v18):
    #   ROLE_PREAMBLE  →  WORKFLOW-ANWEISUNGEN (Frontend)  →  Stilschablone
    #     →  BASE_PROMPT-Kernel  →  Diagnosen/Wortlimit (im base eingebettet)
    parts = [ROLE_PREAMBLE]

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
                f"{_compute_style_constraints(style_context, skip_length=(word_limits is not None))}"
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
                f"{_compute_style_constraints(style_context, skip_length=(word_limits is not None))}"
            )
        else:
            parts.append(
                "\nSTILVORLAGE FÜR DIESEN THERAPEUTEN:\n"
                "Übernimm den Schreibstil der folgenden Vorlage. "
                "NICHT die konkreten Inhalte, Diagnosen oder Patientendaten – "
                "nur Tonalität, Satzbau und Formulierungsgewohnheiten.\n\n"
                f"{style_context.strip()}"
                f"{_compute_style_constraints(style_context, skip_length=(word_limits is not None))}"
            )

    has_structural_template = (
        style_context and style_context.strip()
        and workflow in STRUCTURAL_WORKFLOWS
    )

    # BASE_PROMPT-Kernel (Pflichtkern, NICHT editierbar) wird nach den
    # Frontend-Anweisungen und der Stilschablone eingehaengt. Enthaelt
    # Stilregeln, Negativ-Listen, Quellenregel und Few-Shot.
    if base:
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
            # P8: Pronomen-Constraint aus Anrede ableiten. Fehlerquelle in v12-Eval:
            # eb-02-ads-trennung wechselte zwischen "Herr R." und "Frau M." aus dem
            # Few-Shot. Hartes Mapping verhindert das.
            if anrede.lower() == "frau":
                pronomen = "sie/ihr/ihre"
                pron_satz = (
                    "Verwende konsistent weibliche Pronomen: 'sie', 'ihr', 'ihre' "
                    "(z.B. 'sie berichtet', 'ihre Beschwerden', 'in ihrem Erleben')."
                )
            elif anrede.lower() == "herr":
                pronomen = "er/sein/seine"
                pron_satz = (
                    "Verwende konsistent maennliche Pronomen: 'er', 'sein', 'seine' "
                    "(z.B. 'er berichtet', 'seine Beschwerden', 'in seinem Erleben')."
                )
            else:
                pronomen = "neutral"
                pron_satz = (
                    "Verwende neutrale Formulierungen, da das Geschlecht nicht "
                    "eindeutig ableitbar ist."
                )
            parts.append(
                f"\nPATIENTENNAME (aus den Unterlagen extrahiert):\n"
                f"Der aktuelle Patient ist {anrede} {vorname} {nachname}.\n"
                f"Verwende im gesamten Bericht AUSSCHLIESSLICH die Bezeichnung '{anrede} {initial}' "
                f"(Anrede + erster Buchstabe des Nachnamens + Punkt).\n"
                f"NIEMALS den vollen Nachnamen, NIEMALS den Vornamen, "
                f"NIEMALS einen Platzhalter (eckige Klammern um Patient/in oder Initiale, oder Pseudo-Namen wie Frau X. / Herr Y.) verwenden.\n"
                f"PRONOMEN ({pronomen}): {pron_satz}\n"
                f"WICHTIG: NICHT zwischen den Geschlechtsbezeichnungen wechseln "
                f"(z.B. niemals 'Herr R.' und im naechsten Satz 'Frau M.' aus einem "
                f"Beispiel uebernehmen). Das Stilbeispiel kann ein anderes Geschlecht "
                f"haben - der OUTPUT folgt aber AUSSCHLIESSLICH dem Geschlecht des "
                f"AKTUELLEN Patienten.\n"
                f"Beispiel KORREKT: 'Nach der Aufnahme zeigte sich {anrede} {initial} zunehmend...'\n"
                f"Beispiel FALSCH:  Nach der Aufnahme zeigte sich [Pat] zunehmend... (mit Platzhalter)\n"
            )

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

    # ── Platzhalter-Substitution ──────────────────────────────────────────
    # Die Beispiele (FEW_SHOT_*) enthalten "[Patient/in]" und "Herr/[Patient/in]"
    # als generische Platzhalter. Wenn der echte Name bekannt ist, ersetzen
    # wir diese durchgaengig - sonst uebernimmt das Modell den Platzhalter 1:1
    # in den Output.
    #
    # Sicherheits-Check: Substitution NUR wenn full_ref plausibel kurz und
    # frei von "Klient"/"Patient" ist. Andernfalls droht das Replace mit
    # einem Müll-String wie "die Klientin/der Klient" auszufuehren -
    # Quelle waere ein vom Frontend versehentlich gesendeter Hinweistext.
    # parse_explicit_patient_name filtert solche Strings bereits in
    # extraction.py raus, dieser Check ist die zweite Verteidigungslinie.
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
            # "Herr/[Patient/in]" -> "Frau M." (komplett ersetzen, keine zweischichtige Anrede mehr)
            final_prompt = final_prompt.replace("Herr/[Patient/in]", full_ref)
            # "[Patient/in]" -> "Frau M." / "Herr S."
            final_prompt = final_prompt.replace("[Patient/in]", full_ref)
            # "[Name]" (aus FACHLICHES REFERENZWISSEN) -> Initiale
            final_prompt = final_prompt.replace("[Name]", full_ref)
    # Wenn kein Name bekannt: Platzhalter bleiben stehen - das Modell muss
    # aus den Quellen ableiten. Die NAMENSFORMAT-Anweisung sorgt dafuer
    # dass es die Initiale selbst bildet.

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
    diagnosen: Optional[list[str]] = None,
    custom_prompt: Optional[str] = None,
    patient_name: Optional[dict] = None,
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
      diagnosen:           ICD-Codes (explizit oder aus Antragsvorlage)
      custom_prompt:       Therapeuten-Fokus (wird am Ende eingebettet)
      patient_name:        Dict {anrede, vorname, nachname, initial} – explizit uebergeben
                           oder aus Unterlagen extrahiert. Wird als expliziter Hinweis
                           am Anfang des User-Blocks eingefuegt.
    """
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
        # P7: Workflow-Primer fuer Anamnese. Gibt einen typischen Anfang und
        # einen typischen Abschluss vor, damit das Modell weiss wie der Text
        # rahmen muss. Reduziert das "AMDP-Block-am-Ende"-Problem.
        parts.append(
            "Erstelle jetzt die Anamnese als zusammenhaengenden Fliesstext "
            "(KEIN Befund - der laeuft als separater Call).\n\n"
            "TYPISCHER ANFANG (uebernimm Form, fuelle mit Inhalten des aktuellen "
            "Patienten):\n"
            "'[Anrede + Initial] stellt sich mit dem Hauptanliegen vor, [Inhalt aus "
            "Selbstauskunft]. Die Symptomatik begann vor etwa [Zeitraum] im Kontext "
            "von [Auslöser]. Seither [Verlauf]...'\n\n"
            "TYPISCHER ABSCHLUSS (synthetisierender Schlussabsatz):\n"
            "'Insgesamt zeige sich ein komplexes Zusammenspiel aus [Hauptmustern], "
            "vor dem Hintergrund [biographischer Kontext], welches aktuell zu "
            "[Funktionseinschraenkung] gefuehrt hat.'"
        )

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
            "Mindestens 400 Wörter. "
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
            "Verfasse jetzt den Abschnitt 'Verlauf und Begründung der weiteren Verlängerung'. "
            "Beginne mit einem kurzen Rückbezug auf den bisherigen Verlauf (aus dem vorherigen Antrag), "
            "dann beschreibe die Entwicklung SEITDEM. "
            "Nur diesen Abschnitt – keine Anamnese, keine Diagnosen, keine Stammdaten. "
            "Mindestens 400 Wörter. "
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
            "Schreibe NUR den Inhalt des Abschnitts ohne Überschrift (150-350 Wörter).\n\n"
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
        if diagnosen:
            parts.append(f"DIAGNOSEN DES AKTUELLEN PATIENTEN: {', '.join(diagnosen)}")
        if fokus_themen:
            parts.append(f"THERAPEUTISCHE SCHWERPUNKTE / BESONDERE THEMEN:\n{fokus_themen}")
        parts.append(
            "Verfasse jetzt den psychotherapeutischen Verlaufsteil als zusammenhängenden "
            "Fließtext ohne Überschriften. "
            "Behandlungsverlauf (mind. 500 Wörter), Epikrise (mind. 150 Wörter) und "
            "Empfehlungen (mind. 100 Wörter) fliessen nahtlos ineinander. "
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
