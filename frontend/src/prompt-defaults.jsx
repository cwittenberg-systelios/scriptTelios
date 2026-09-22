// ────────────────────────────────────────────────────────────────────────────
// src/prompt-defaults.jsx — GENERIERT, NICHT VON HAND EDITIEREN.
//
// Quelle: backend/app/services/prompts.py
//         (WORKFLOW_INSTRUCTIONS_DEFAULT, BEFUND_VORLAGE)
// Generator: backend/scripts/export_prompt_defaults.py
//   - `npm run build` ruft ihn als "prebuild" auf,
//   - `lint_gate.sh --check` und tests/unit/test_prompt_defaults_sync.py
//     schlagen bei Drift fehl.
//
// Aenderungen an Default-Prompts bitte ausschliesslich in prompts.py.
// (v19.21 S1: vorher zwei handgepflegte, auseinandergelaufene Fassungen.)
// ────────────────────────────────────────────────────────────────────────────

// dokumentation
const P_DOKU = `Erstelle eine systemische Gesprächsdokumentation. Schreibe aktiv aus der Perspektive der Klientin/des Klienten - nicht über das Gespräch, sondern über die Person und ihre Themen. Gliedere den Text in folgende Abschnitte mit den jeweiligen Überschriften (der letzte Abschnitt 'Organisatorisches' nur, wenn es tatsaechlich administrative Absprachen gab - sonst weglassen):

Formuliere JEDEN Abschnitt als dichten, ausformulierten Fliesstext-Absatz - mehrere vollstaendige, aufeinander aufbauende Saetze, die das vorhandene Material entfalten (vergleichbar der Absatzdichte des Beispiels unten). KEINE Stichpunkte, keine fragmentierten Ein-Satz-Absaetze, keine blossen Aufzaehlungen. Schoepfe die Inhalte aus dem Gespraech aus, strecke aber NIE durch Erfindung oder Wiederholung. Ausnahme 'Einladungen': nur was tatsaechlich ausgesprochen wurde - hier ist Kuerze korrekt.

**Auftragsklärung**
Beschreibe worum es der Klientin/dem Klienten ging und was das gemeinsame Ziel des Gesprächs war. Beispiel: 'Im Mittelpunkt stand...' oder 'Frau M. kam mit dem Anliegen...' (verwende den tatsaechlichen Namen des Patienten – NICHT einen Platzhalter in eckigen Klammern).
WICHTIG - Auftrag vs. Organisatorisches trennen: Der therapeutische Auftrag ist das INHALTLICH-therapeutische Anliegen (Symptome, Erleben, Muster, Beziehungsthemen). Rein administrative Absprachen zu Beginn eines Gesprächs - Terminfindung, Raum-/Verwaltungsfragen, Modalitaeten eines Transfer-/Angehoerigengespraechs, Kontakt-/Zugangswege, Wartelisten - sind NICHT der Auftrag, auch wenn sie zeitlich zuerst besprochen wurden. Sie gehoeren ausschliesslich in die Schluss-Sektion 'Organisatorisches' (siehe unten) und duerfen die Auftragsklaerung nicht dominieren. Wenn der/die Klient/in solche Punkte selbst als Vorgeplaenkel rahmt ('vorweg noch kurz ein paar Fragen'), ist genau das das Signal, sie NICHT als Auftrag zu werten.

**Relevante Gesprächsinhalte**
Schildere die wesentlichen Inhalte aus Sicht der Klientin/des Klienten: Symptome, Erlebensmuster, innere Anteile, Beziehungsdynamiken, Ressourcen. Konkrete Formulierungen statt allgemeiner Beschreibungen. WICHTIG zur Fachsprache: Verfahrensspezifische Begriffe (etwa aus der Teilearbeit) NUR dann, wenn Klient/in oder Therapeut/in im Gespräch tatsächlich in Anteile-/Teile-Sprache gesprochen oder das Verfahren erkennbar angewendet haben (gemäß Quellentreue-Regel des Glossars). Wurde das Gespräch NICHT so geführt, beschreibe in Alltagssprache ('ein Teil von ihr, der schützt') und stülpe KEIN Verfahrens-Vokabular über. Im Zweifel deskriptiv, statt ein Verfahren zu benennen.

**Hypothesen und Entwicklungsperspektiven**
Formuliere systemische Hypothesen über Sinnzusammenhänge. Zeige Entwicklungsperspektiven auf - was wird möglich, wenn... Ressourcenorientiert und konkret.

**Einladungen**
Gib NUR Einladungen, Vorschläge oder Aufgaben wieder, die der/die Therapeut/in im Gespräch TATSÄCHLICH und EXPLIZIT ausgesprochen hat. Erkennbar an Wendungen wie 'Ich lade Sie ein …', 'Ich schlage vor …', 'Ein Angebot wäre …', 'In der nächsten Woche / den nächsten Tagen könnten Sie …', 'Vielleicht mögen Sie …'. Formuliere sie aktiv ('Frau M. wurde eingeladen, …', 'Als Übung wurde vereinbart, …'; verwende den tatsächlichen Namen, NICHT einen Platzhalter in eckigen Klammern). ERFINDE KEINE Aufgaben, Übungen oder Impulse - insbesondere KEINE generischen Standard-Hausaufgaben wie 'ein Notizbuch/Tagebuch führen', 'Beobachtungen aufschreiben', 'Achtsamkeitsübungen machen', es sei denn, der/die Therapeut/in hat GENAU DAS wörtlich ausgesprochen. In den meisten Gesprächen wird KEINE explizite Einladung formuliert - dann ist der korrekte und vollständige Abschluss dieses Abschnitts schlicht: 'Es wurde keine konkrete Einladung oder Aufgabe vereinbart.' Das ist KEINE Lücke, sondern die treue Wiedergabe des Gesprächs. Lieber dieser eine Satz als irgendeine erfundene Aufgabe. Rein organisatorische Absprachen (Termine, Kontaktwege, Transfergespraech-Modalitaeten) gehoeren NICHT hierher, sondern in 'Organisatorisches'.

**Organisatorisches**
NUR anlegen, wenn im Gespraech tatsaechlich administrative Punkte besprochen wurden. Fasse hier - knapp und getrennt vom therapeutischen Auftrag - die rein organisatorischen Absprachen zusammen: vereinbarte oder geplante Termine (mit korrektem Tempus: was GEPLANT ist, nicht als bereits geschehen darstellen), Modalitaeten von Transfer-/Angehoerigengespraechen, Kontakt- und Verwaltungswege. Ein bis wenige Saetze genuegen; keine therapeutische Deutung. Gab es nichts Organisatorisches, LASSE diesen Abschnitt komplett weg (keine Ueberschrift, kein Platzhaltersatz).

**Zum Schluss: Suizidalität**
Wurde im Gespräch Suizidalität, Lebensmüdigkeit, ein Todeswunsch oder die Absprachefähigkeit thematisiert, gib das als letzten Absatz der Dokumentation wieder - ohne eigene Überschrift, im Fliesstext, ausschliesslich das, was tatsächlich gesagt wurde (auch eine ausdrückliche Verneinung durch die Klientin/den Klienten gehört hierher). Ergänze KEINE eigene Einschätzung zu Absprachefähigkeit oder Suizidalität, die im Gespräch nicht gefallen ist. War es KEIN Thema, schreibe dazu nichts - der Standardsatz wird in diesem Fall automatisch angehängt.`;

// anamnese
const P_ANAMNESE = `Erstelle eine vollständige psychotherapeutische Anamnese auf Basis der bereitgestellten Unterlagen.

TON UND STIL:
Schreibe einen erzaehlerischen, biographisch eingebetteten Bericht. Die Anamnese ist KEINE Symptom-Liste – sie ist die Lebensgeschichte des Patienten in seinem Kontext. Lass die Lebenswelt, die Bezugspersonen und die Entwicklungslinien sichtbar werden. Vermeide pathologisierende Sprache ('Defizit', 'gestoert', 'auffaellig'), wo eine beschreibende Formulierung moeglich ist ('hat Schwierigkeiten mit...', 'erlebt sich als...', 'schildert, dass...').

ANAMNESE als durchgehender FLIESSTEXT (KEINE Unterüberschriften!):
Schreibe die Anamnese als einen zusammenhängenden Fließtext OHNE Zwischenüberschriften wie 'Vorstellungsanlass', 'Aktuelle Erkrankung' etc. Der Text soll natürlich von Thema zu Thema fließen, wie ein erfahrener Therapeut einen Bericht diktieren würde.

Folgende Inhalte nahtlos in den Fließtext einarbeiten (KEINE Überschriften dafür!):
- Vorstellungsanlass und Hauptbeschwerde in eigenen Worten des Patienten
- Beginn, Verlauf, auslösende und aufrechterhaltende Faktoren
- Psychiatrische Vorgeschichte
- Somatische Vorgeschichte und Medikation
- Familienanamnese
- Sozialanamnese (Herkunft, Bildung, Beruf, Beziehungsstatus, Kinder)
- Vegetativum (Schlaf, Appetit, Schmerzen) – nur kurz erwaehnen,   NICHT als eigene Bullet-Liste
- Suchtmittelanamnese – nur falls relevant, kurz im Fluss
- Ressourcen`;

// verlaengerung
const P_VERL = `Verfasse den Abschnitt 'Bisheriger Verlauf und Begründung der Verlängerung' (auch: 'Verlauf und Begründung der weiteren Verlängerung') für einen Antrag auf Verlängerung der Kostenzusage bei der Krankenversicherung.

INHALT (Reihenfolge einhalten):
- Bisheriger Verlauf: was wurde konkret bearbeitet, welche Methoden eingesetzt (IFS, Anteilearbeit, Hypnosystemik, Körperarbeit, Gruppenarbeit)
- Konkrete Fortschritte – spezifisch und belegbar aus der Verlaufsdokumentation, keine allgemeinen Behauptungen
- Noch ausstehende Therapieziele: was bleibt zu tun, warum ist weitere stationäre Behandlung notwendig
- Medizinische Begründung: Belastbarkeit, Stabilität, soziale Integration, Entlassfähigkeit noch nicht erreicht
- Geplante Maßnahmen und Prognose für den Verlängerungszeitraum`;

// folgeverlaengerung
const P_VERL_FOLGE = `Verfasse den Abschnitt 'Verlauf und Begründung der weiteren Verlängerung' für einen FOLGE-Verlängerungsantrag bei der Krankenversicherung.

INHALT (Reihenfolge einhalten):
- Kurzer Rückbezug auf den bisherigen Verlauf (1–2 Sätze, aus dem vorherigen Antrag)
- Entwicklung SEIT dem letzten Antrag: neue Themen, vertiefte Arbeit, Wendepunkte
- Konkrete Fortschritte seit dem letzten Antrag – spezifisch und belegbar
- Was bleibt noch zu tun? Warum ist weitere stationäre Behandlung notwendig?
- Geplante Maßnahmen und Prognose`;

// akutantrag
const P_AKUT = `Verfasse die 'Begründung für Akutaufnahme' eines AKUTANTRAGS an die Krankenversicherung für die Erstattung einer stationären Akutaufnahme.

KONTEXT:
Die Antragsvorlage enthält bereits Aktuelle Anamnese, Problemrelevante Vorgeschichte, Psychischen Befund und Einweisungsdiagnosen. Diese Informationen sind deine QUELLEN.

INHALT der Begründung (zusammenhängende ARGUMENTATION, keine Symptomliste):
- Kernbegründung: warum ist ein stationäres Setting medizinisch AKUT notwendig?
- Symptome, Vorgeschichte und Risiken aus den Quellen NUR als Beleg der Indikation anführen (nicht als eigenständige Symptom-Aufzählung)
- Ambulante Insuffizienz begründen (warum reicht ambulant nicht (mehr)?)
- Dekompensationszeichen und aktuelle Krise als Nachweis der Dringlichkeit
- Verdichtet und begründend, nicht narrativ-beschreibend`;

// entlassbericht
const P_ENTL = `Schreibe den psychotherapeutischen Verlaufsteil eines Entlassberichts als zusammenhängenden Fließtext ohne Überschriften, ohne Aufzählungen, ohne Einleitung und ohne Abschluss.

INHALT – drei Teile nahtlos als Fließtext ineinander (ALLE DREI MÜSSEN VORKOMMEN):

Teil 1 – BEHANDLUNGSVERLAUF (Hauptteil, ausführlich):
Beschreibe ausführlich den therapeutischen Verlauf. Eingesetzte Methoden (IFS/Anteilearbeit, hypnosystemisch, Stuhlarbeit, Biographiearbeit, Gruppenarbeit), konkrete Wendepunkte und Entwicklungsschritte. Der Einzeltherapie, der Gruppentherapie und den nonverbalen Therapien (Kunst-, Musik-, Körperpsychotherapie/Körperarbeit) jeweils einen eigenen Absatz widmen, sofern sie in den Quellen dokumentiert sind – nur die tatsächlich dokumentierten Verfahren nennen. Stil folgt der Vorlage (Wir-Sicht oder empathische 3.-Person), NIE objektiv-distanzierter Berichtston ('Der Patient zeigte X').

Teil 2 – EPIKRISE (kompakte Gesamtbewertung):
Symptomatik-Entwicklung im Vergleich zu Aufnahme, entlastete Schutzanteile, verbliebener Bedarf, Ressourcen, Prognose. Sofern die Berichtsvorlage Prä-/Post-Testwerte enthält, diese explizit mit den konkreten Werten referenzieren.

Teil 3 – THERAPIEEMPFEHLUNGEN (kompakter Abschluss, DARF NICHT FEHLEN):
Konkrete Empfehlungen für die ambulante Weiterbehandlung: Therapieform, Schwerpunkte, Frequenz, Nachsorge.`;

// ism_fragebogen
const P_ISM = `Erstelle aus dem Therapiegespräch einen individualisierten ISM-Fragebogen für das tägliche Prozessmonitoring des Klienten.

ITEM-FORM:
- Jedes Item ist eine Selbstauskunft in der Ich-Perspektive des Klienten, meist im Heute-Format ('Heute konnte ich ...', 'Heute ist es mir gelungen ...', 'Wie sehr hat ... heute noch eine Rolle gespielt?').
- Verwende die eigene Sprache des Klienten aus dem Gespräch: seine Bilder, Metaphern, Anteile-Namen und Schlüsselformulierungen machen das Item wiedererkennbar und wirksam.
- Jedes Item bekommt zwei individuelle Pol-Labels: der linke Pol (Wert 0) ist validierend und einladend formuliert - nie abwertend, nie beschämend ('ich übe noch...', '...und das ist ok'). Der rechte Pol (Wert 100) bestätigt die Ressource oder den gelungenen Schritt.

TONALITÄT:
- Hypnosystemisch-ressourcenorientiert: würdigend, einladend, humorvoll wo es zum Klienten passt.
- Beschreibend statt pathologisierend; Entwicklungsrichtung statt Defizit.

BEGRÜSSUNG UND VERABSCHIEDUNG:
- Formuliere eine kurze, persönliche Begrüßung (1-2 Sätze) und Verabschiedung (1-2 Sätze) für den täglichen Fragebogen - warm, einladend, gerne mit einem Motiv aus dem Gespräch des Klienten.`;

// entlassbericht (Struktur: thematisch, v19.28)
const P_ENTL_THEMATISCH = `Schreibe den psychotherapeutischen Verlaufsteil eines Entlassberichts als zusammenhängenden Fließtext ohne Überschriften, ohne Aufzählungen, ohne Einleitung und ohne Abschluss.

Der Bericht folgt NICHT der Reihenfolge der Therapieformen, sondern dem therapeutischen Prozess des Klienten/der Klientin. Er hat FÜNF Teile, die nahtlos ineinander übergehen (ALLE FÜNF MÜSSEN VORKOMMEN):

Teil 1 – AUFTRAG (kurz, 1 Absatz):
Mit welchem Anliegen und welchem Veränderungswunsch kam der Klient/die Klientin – in seinen/ihren eigenen Worten, wie im Aufnahmegespräch und in den Auftragsklärungen dokumentiert. Dazu der Zustand zu Therapiebeginn.

Teil 2 – ERARBEITUNG DES ZENTRALEN THEMAS (1–2 Absätze):
Welches Muster wurde im Verlauf als zentral erkannt (siehe FALLFORMEL, falls vorhanden; sonst aus den dokumentierten Hypothesen der Verlaufsdoku). Beschreibe, wie sich dieses Muster gezeigt hat, mit welcher Sinnhaftigkeit es gewürdigt wurde (Schutzfunktion, biographischer Kontext) und wann/wo im Verlauf es erarbeitet wurde. Erkläre das Muster HIER EINMAL vollständig – in den folgenden Teilen wird es nicht neu hergeleitet, sondern nur weitergeführt.

Teil 3 – PROZESSFORTSCHRITTE (Hauptteil):
Für JEDE dokumentierte Therapieform – Einzeltherapie, Gruppentherapie, nonverbale Therapien (Kunst-, Musik-, Körperpsychotherapie/Körperarbeit) – ein EIGENER Absatz. Jeder Absatz beantwortet: Welcher neue Schritt im Umgang mit dem zentralen Thema wurde GENAU DORT möglich? Welcher Wendepunkt, welche konkrete Erfahrung, welche Beziehungsdynamik? Nur dokumentierte Verfahren nennen. Keine Wiederholung dessen, was Teil 2 schon erklärt hat – ein kurzer Rückbezug („dieses Muster zeigte sich in der Gruppe darin, dass …“) genügt. Eine Therapieform darf nur fehlen, wenn die Quellen sie nicht dokumentieren.

Teil 4 – REFLEXION UND SYMPTOMVERÄNDERUNG (kompakt):
Wie der Klient/die Klientin den eigenen Prozess zum Abschluss reflektiert (sofern eine Prozessreflexion vorliegt: in indirekter Rede, ohne Zitate, ohne Dank/Feedback ans Team). Dann die Symptomatik im Vergleich zur Aufnahme, verbliebener Bedarf, Ressourcen, Prognose. Prä-/Post-Testwerte vollständig, wenn die Antragsvorlage sie enthält – auch ungünstige.

Teil 5 – THERAPIEEMPFEHLUNGEN (kompakter Abschluss, DARF NICHT FEHLEN):
Empfehlungen für die ambulante Weiterbehandlung als Vertiefung des in Teil 2–3 beschriebenen Weges: Therapieform, Schwerpunkte, Frequenz, Nachsorge.

Zuordnung zur Gesamtstruktur: Teil 1–3 bilden den Behandlungsverlauf (~70 %), Teil 4 die Epikrise (~20 %), Teil 5 die Empfehlungen (~10 %). Stil folgt der Vorlage (Wir-Sicht oder empathische 3. Person), NIE objektiv-distanzierter Berichtston.`;

// Befundvorlage (Anamnese, Psychischer Befund)
const P_BEFUND_VORLAGE = `Im Gespräch offen, wach, bewusstseinsklar, zu allen Qualitäten orientiert. Konzentration subjektiv {konzentration}. Auffassung, Merkfähigkeit und Gedächtnis intakt. Formalgedanklich {formalgedanke}, keine Denkverlangsamung, {fokus_denken}. {phobien_angst}. {Zwänge}. {vermeidung}. Kein Anhalt für Wahn oder Sinnestäuschungen, keine Ich-Störungen (z.B. Depersonalisation, Derealisation, Dissoziation). Stimmungslage {stimmung}, affektive Schwingungsfähigkeit {schwingung} bei insgesamt {affektlage} Affektlage. {freud_interessen}. {erschöpfung}. Antrieb {antrieb}. {hoffnung_insuffizienz}. {schuldgefühle}. Selbstwertgefühl ist {selbstwert}. Gefühlsregulation ist {gefühlsregulation}. Impulskontrolle ist {impulskontrolle}. {ambivalenz}. {innere_unruhe}. {zirkadian}. {schlaf}. Appetenz {appetenz}. {aggressiv_selbstverletzend}. {sozialer_rückzug}. Essverhalten {essverhalten}. {suchtverhalten}. {somatisierung}. {suizidalität_vergangenheit}. Aktuelle Verneinung von lebensüberdrüssigen und suizidalen Gedanken, keine suizidale Handlungsplanung oder Handlungsvorbereitung. Zum Zeitpunkt der Aufnahme von akuter Suizidalität klar distanziert.`;

export { P_DOKU, P_ANAMNESE, P_VERL, P_VERL_FOLGE, P_AKUT, P_ENTL, P_ISM, P_ENTL_THEMATISCH, P_BEFUND_VORLAGE };
