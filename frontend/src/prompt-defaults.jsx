// ────────────────────────────────────────────────────────────────────────────
// src/prompt-defaults.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";


// ── Prompts ─────────────────────────────────────────────────────
// ── Frontend-Defaults fuer Workflow-Anweisungen (v18) ─────────────────────
//
// Diese Texte sind die Default-Inhalte fuer den Prompt-Editor und werden als
// 'workflow_instructions' Form-Feld zum Backend geschickt. Der Backend-
// Pflichtkern (Stilregeln, Quellenregel, Few-Shot, Negativ-Listen) ist
// unsichtbar fuer den Therapeuten und wird im Backend ergaenzt.
//
// Architektur: was hier editiert wird ist WAS geschrieben werden soll,
// nicht WIE. Der Therapeut kann den Auftrag anpassen ohne die
// Pflichtkern-Regeln aushebeln zu koennen.

const P_DOKU = `Erstelle eine systemische Gesprächsdokumentation. Schreibe aktiv aus der Perspektive der Klientin/des Klienten – nicht über das Gespräch, sondern über die Person und ihre Themen. Gliedere den Text in folgende vier Abschnitte mit den jeweiligen Überschriften:

**Auftragsklärung**
Beschreibe worum es der Klientin/dem Klienten ging und was das gemeinsame Ziel des Gesprächs war. Beispiel: "Im Mittelpunkt stand..." oder "Frau M. kam mit dem Anliegen..." (verwende den tatsächlichen Namen des Patienten – NICHT den Platzhalter "[Patient/in]").

**Relevante Gesprächsinhalte**
Schildere die wesentlichen Inhalte aus Sicht der Klientin/des Klienten: Symptome, Erlebensmuster, innere Anteile, Beziehungsdynamiken, Ressourcen. Konkrete Formulierungen statt allgemeiner Beschreibungen. Systemische und IFS-Begriffe wo passend (Manager-Anteile, Exile, Self-Energy, Feuerwehr-Anteile etc.).

**Hypothesen und Entwicklungsperspektiven**
Formuliere systemische Hypothesen über Sinnzusammenhänge. Zeige Entwicklungsperspektiven auf – was wird möglich, wenn... Ressourcenorientiert und konkret.

**Einladungen**
Beschreibe die konkreten Aufgaben, Übungen oder Impulse die mitgegeben wurden – aktiv formuliert: "Frau M. wurde eingeladen, ..." oder "Als Übung wurde vereinbart, ..." (verwende den tatsächlichen Namen, NICHT "[Patient/in]").

PERSPEKTIVE UND SPRACHE (gilt für alle Abschnitte):
Keine Wir-Form – "Wir erlebten Frau G. ..." gehört in Team-Berichte, nicht in die Dokumentation eines Einzelgesprächs. Deskriptive 3. Person mit dem Namen als Subjekt ("Frau G. beschreibt sich eingangs des Gesprächs als müde, unruhig und unkonzentriert") oder Ich-Perspektive des Klientenberichts. Beschreibend statt pathologisierend: "unkonzentriert" statt "konzentrationsgestört", Selbstbeschreibungen als solche kennzeichnen ("beschreibt sich als ...").`;

const P_ANAMNESE = `Erstelle eine vollständige psychotherapeutische Anamnese auf Basis der bereitgestellten Unterlagen.

TON UND STIL:
Schreibe einen erzählerischen, biographisch eingebetteten Bericht. Die Anamnese ist KEINE Symptom-Liste – sie ist die Lebensgeschichte des Patienten in seinem Kontext. Lass die Lebenswelt, die Bezugspersonen und die Entwicklungslinien sichtbar werden. Vermeide pathologisierende Sprache ("Defizit", "gestört", "auffällig"), wo eine beschreibende Formulierung möglich ist ("hat Schwierigkeiten mit...", "erlebt sich als...", "schildert, dass..."). Patientenangaben in indirekter Rede im Konjunktiv I: "Sie berichtet, sie fühle sich erschöpft und habe wenig Kontakt" – nicht "Sie fühlt sich erschöpft und hat wenig Kontakt".

ANAMNESE als durchgehender FLIESSTEXT (KEINE Unterüberschriften!):
Schreibe die Anamnese als einen zusammenhängenden Fließtext OHNE Zwischenüberschriften wie "Vorstellungsanlass", "Aktuelle Erkrankung" etc. Der Text soll natürlich von Thema zu Thema fließen, wie ein erfahrener Therapeut einen Bericht diktieren würde.

Folgende Inhalte nahtlos einarbeiten (KEINE Überschriften dafür!):
- Vorstellungsanlass und Hauptbeschwerde in eigenen Worten des Patienten
- Beginn, Verlauf, auslösende und aufrechterhaltende Faktoren
- Psychiatrische Vorgeschichte
- Somatische Vorgeschichte und Medikation
- Familienanamnese
- Sozialanamnese (Herkunft, Bildung, Beruf, Beziehungsstatus, Kinder)
- Vegetativum (Schlaf, Appetit, Schmerzen) – nur kurz erwähnen, NICHT als Bullet-Liste
- Suchtmittelanamnese – nur falls relevant, kurz im Fluss
- Ressourcen`;

// Befund-Vorlage als separates editierbares Feld in P2.
// Der umgebende Pflichtkern (Quellenregel, NICHT-Schreiben-Liste) liegt im Backend.
const P_BEFUND_VORLAGE = `Im Gespräch offen, wach, bewusstseinsklar, zu allen Qualitäten orientiert. Konzentration subjektiv {konzentration}. Auffassung, Merkfähigkeit und Gedächtnis intakt. Formalgedanklich {formalgedanke}, keine Denkverlangsamung, {fokus_denken}. {phobien_angst}. {Zwänge}. {vermeidung}. Kein Anhalt für Wahn oder Sinnestäuschungen, keine Ich-Störungen (z.B. Depersonalisation, Derealisation, Dissoziation). Stimmungslage {stimmung}, affektive Schwingungsfähigkeit {schwingung} bei insgesamt {affektlage} Affektlage. {freud_interessen}. {erschöpfung}. Antrieb {antrieb}. {hoffnung_insuffizienz}. {schuldgefühle}. Selbstwertgefühl ist {selbstwert}. Gefühlsregulation ist {gefühlsregulation}. Impulskontrolle ist {impulskontrolle}. {ambivalenz}. {innere_unruhe}. {zirkadian}. {schlaf}. Appetenz {appetenz}. {aggressiv_selbstverletzend}. {sozialer_rückzug}. Essverhalten {essverhalten}. {suchtverhalten}. {somatisierung}. {suizidalität_vergangenheit}. Aktuelle Verneinung von lebensüberdrüssigen und suizidalen Gedanken, keine suizidale Handlungsplanung oder Handlungsvorbereitung. Zum Zeitpunkt der Aufnahme von akuter Suizidalität klar distanziert.`;

const P_VERL = `Du bist systemischer Psychotherapeut einer hypnosystemischen Klinik für Psychosomatik und Psychotherapie. Verfasse den Abschnitt "Bisheriger Verlauf und Begründung der Verlängerung" (auch: "Verlauf und Begründung der weiteren Verlängerung") für einen Antrag auf Verlängerung der Kostenzusage bei der Krankenversicherung.

INHALT (Reihenfolge einhalten):
- Bisheriger Verlauf: was wurde konkret bearbeitet, welche Methoden eingesetzt (IFS, Anteilearbeit, Hypnosystemik, Körperarbeit, Gruppenarbeit)
- Konkrete Fortschritte – spezifisch und belegbar aus der Verlaufsdokumentation, keine allgemeinen Behauptungen
- Noch ausstehende Therapieziele: was bleibt zu tun, warum ist weitere stationäre Behandlung notwendig
- Medizinische Begründung: Belastbarkeit, Stabilität, soziale Integration, Entlassfähigkeit noch nicht erreicht
- Geplante Maßnahmen und Prognose für den Verlängerungszeitraum`;

const P_VERL_FOLGE = `Du bist systemischer Psychotherapeut einer hypnosystemischen Klinik für Psychosomatik und Psychotherapie. Verfasse den Abschnitt "Verlauf und Begründung der weiteren Verlängerung" für einen FOLGE-Verlängerungsantrag bei der Krankenversicherung.

INHALT (Reihenfolge einhalten):
- Kurzer Rückbezug auf den bisherigen Verlauf (1–2 Sätze, aus dem vorherigen Antrag)
- Entwicklung SEIT dem letzten Antrag: neue Themen, vertiefte Arbeit, Wendepunkte
- Konkrete Fortschritte seit dem letzten Antrag – spezifisch und belegbar
- Was bleibt noch zu tun? Warum ist weitere stationäre Behandlung notwendig?
- Geplante Maßnahmen und Prognose`;

const P_AKUT = `Du bist Arzt oder Psychologischer Psychotherapeut der sysTelios Klinik. Verfasse die "Begründung für Akutaufnahme" eines AKUTANTRAGS an die Krankenversicherung für die Erstattung einer stationären Akutaufnahme.

KONTEXT:
Die Antragsvorlage enthält bereits Aktuelle Anamnese, Problemrelevante Vorgeschichte, Psychischen Befund und Einweisungsdiagnosen. Diese Informationen sind deine QUELLEN.

INHALT:
- Warum ist ein stationäres Setting medizinisch AKUT notwendig?
- Konkrete Symptome und Risiken aus den Quellen benennen
- Ambulante Insuffizienz begründen (warum reicht ambulant nicht?)
- Dekompensationszeichen und aktuelle Krisensituation
- Beginne mit folgender Standardformulierung – WÖRTLICH, unverändert (Kopie, keine Paraphrase, keine Wortauslassungen):

  >>>
  Folgende Krankheitssymptomatik macht in der Art und Schwere sowie unter Berücksichtigung der Beurteilung des Einweisers und unseres ersten klinischen Eindruckes ein stationäres Krankenhaussetting akut notwendig:
  <<<

  Danach Zeilenumbruch, nächster Absatz MUSS beginnen mit:
  "Wir nehmen [Patient/in] schwer belastet auf..."`;

const P_ENTL = `Schreibe den psychotherapeutischen Verlaufsteil eines Entlassberichts als zusammenhängenden Fließtext ohne Überschriften, ohne Aufzählungen, ohne Einleitung und ohne Abschluss.

INHALT – drei Teile nahtlos als Fließtext ineinander (ALLE DREI MÜSSEN VORKOMMEN):

Teil 1 – BEHANDLUNGSVERLAUF (Hauptteil, ausführlich):
Beschreibe ausführlich den therapeutischen Verlauf. Eingesetzte Methoden (IFS/Anteilearbeit, hypnosystemisch, Stuhlarbeit, Biographiearbeit, Gruppenarbeit), konkrete Wendepunkte und Entwicklungsschritte. Richtwert: ca. 100 Wörter pro Absatz. Wir-Perspektive: "Wir erlebten...", "Es gelang zunehmend...", "Im Verlauf zeigte sich..."

Teil 2 – EPIKRISE (kompakte Gesamtbewertung):
Symptomatik-Entwicklung im Vergleich zu Aufnahme, entlastete Schutzanteile, verbliebener Bedarf, Ressourcen, Prognose.

Teil 3 – THERAPIEEMPFEHLUNGEN (kompakter Abschluss, DARF NICHT FEHLEN):
Konkrete Empfehlungen für die ambulante Weiterbehandlung: Therapieform, Schwerpunkte, Frequenz, Nachsorge.`;

const P_ISM = `Erstelle aus dem Therapiegespräch einen individualisierten ISM-Fragebogen für das tägliche Prozessmonitoring des Klienten.

ITEM-FORM:
- Jedes Item ist eine Selbstauskunft in der Ich-Perspektive des Klienten, meist im Heute-Format ("Heute konnte ich ...", "Heute ist es mir gelungen ...", "Wie sehr hat ... heute noch eine Rolle gespielt?").
- Verwende die eigene Sprache des Klienten aus dem Gespräch: seine Bilder, Metaphern, Anteile-Namen und Schlüsselformulierungen machen das Item wiedererkennbar und wirksam.
- Jedes Item bekommt zwei individuelle Pol-Labels: der linke Pol (Wert 0) ist validierend und einladend formuliert – nie abwertend, nie beschämend ("ich übe noch...", "...und das ist ok"). Der rechte Pol (Wert 100) bestätigt die Ressource oder den gelungenen Schritt.

TONALITÄT:
- Hypnosystemisch-ressourcenorientiert: würdigend, einladend, humorvoll wo es zum Klienten passt.
- Beschreibend statt pathologisierend; Entwicklungsrichtung statt Defizit.

BEGRÜSSUNG UND VERABSCHIEDUNG:
- Formuliere eine kurze, persönliche Begrüßung (1-2 Sätze) und Verabschiedung (1-2 Sätze) für den täglichen Fragebogen – warm, einladend, gerne mit einem Motiv aus dem Gespräch des Klienten.`;

export { P_DOKU, P_ANAMNESE, P_BEFUND_VORLAGE, P_VERL, P_VERL_FOLGE, P_AKUT, P_ENTL, P_ISM };
