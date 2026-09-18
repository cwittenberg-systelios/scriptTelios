# Changelog — scriptTelios

Alle nennenswerten Aenderungen am Backend werden hier festgehalten.

Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/);
das Projekt nutzt Sprint-Versionen (v18, v19, v19.1, …) statt SemVer-Patch-Counter.

---

## [v19.25] — Log-Runde 10.–17.09.: Befund-Slots, Stage-1-Robustheit, Grammatik, Quellen-Plausibilitaet, EB-Laenge (2026-09-18)

Basis: `v19_QA_v02` @ `052221c` (nach v19.24 Interview-Dialog). Fuenf Patches (B, S5, G, Q, L; Q enthaelt
Frontend) plus `backend/static/systelios.js`. Evaluation der Logs
10.–17.09.2026: `input_truncated`/`tokens_hit_cap` 0/26, Wir-Form P1 0/14,
Modalitaeten im EB 11/11 – die v19.19–v19.22-Massnahmen halten.

### Sprint B — Structured-Befund ohne Satzfragmente (P2)

Befund vom 15.09. (Job 7d548664): „nicht erhoben. reduziert. ausgeprägt. nein.“
als eigene Saetze – 16 Slots der `BEFUND_VORLAGE` bilden eigene Saetze, das
Modell liefert dort Kurzwerte; `fill_befund_vorlage()` setzte sie 1:1 ein
(alle 3 Structured-Befunde im Log betroffen).

- `prompts.py`: `classify_befund_slots()` (standalone/inline),
  `befund_value_is_empty()`, `BEFUND_SLOT_LABELS`. **B1** Standalone-Slots ohne
  belastbaren Wert werden samt Satzpunkt weggelassen (A3a-Regel), Kurzwerte
  bekommen ein Label („Freude und Interessen: reduziert.“), „nein“ → „verneint“,
  Satzanfang gross, kein doppelter Punkt. Inline-Slots unveraendert
  („nicht erhoben“). **B2** `build_befund_slot_schema(slots, vorlage)`: pro
  Feld eine `description` (Satzform vs. Kurzform mit Satzkontext); Prompt-
  Regeln entsprechend. **B3** `_dedupe_slot_value()`: wiederholte
  Nachbarwoerter („subjektiv subjektiv“, „Appetenz Appetitlosigkeit“).
- `quality_check.py`: **B4** `BEFUND_FRAGMENT` (warning) – Ein-Wort-Saetze,
  Leer-Marker, Kleinschreibung am Satzanfang im Befund-Teil.
- Test `test_structured_outputs.py::test_fixtext_wortidentisch` nutzt jetzt
  einen mehrwortigen Wert (Ein-Wort-Werte bekommen per B1 ein Label).

### Sprint S5 — Stage-1-Robustheit

Log: 16.09. Rest-Chunk 171w mit Minimum 300w (Fehlschlag garantiert, ganze
Stufe verworfen); 17.09. 363w < 393w nach Retry; 15.09. Verlaeufe 1.867w/
2.487w → 373w/364w < fix 400w.

- **S5-1** `chunk_text_by_blocks`: Rest-Chunk < 25 % der Chunk-Groesse wird mit
  dem Vorgaenger verschmolzen (`STAGE1_TAIL_MERGE_RATIO`); Aufrufer fallen bei
  nur einem Teil auf den Normalpfad zurueck.
- **S5-2** `compute_transcript_min_acceptable(..., raw_words)`: nie mehr als
  die Haelfte des Rohtexts verlangen.
- **S5-3** `run_chunked`: Teil-Fallback – gescheiterter Teil geht als Rohtext
  (Marker `[Teil i/n: Rohtext – Verdichtung fehlgeschlagen]`) in die
  Zusammenfassung, Telemetrie `chunks_failed`, Issue `stage1_chunk_failed`.
- **S5-4** Toleranzband `STAGE1_TOLERANCE = 0.8`: Ergebnis zwischen 80 % und
  100 % des Minimums wird akzeptiert und `degraded` markiert (Issue
  `stage1_short`) – Transkript (bester von Initial/Retry) und Verlauf.
- **S5-5** `STAGE1_VERLAUF_MIN_WORDS` 1500 → 2500;
  `compute_verlauf_min_acceptable(..., raw_words)` = max(250, min(400, 0.15·raw)).
  Golden-Fixture `stage1_prompts_v1920.json` (Keys `verlauf`, `verlauf_cap`)
  neu erzeugt: min 400 → 250 fuer den 1.200-Woerter-Testtext.
- **S5-6** `Stage1Error` (summary_runner) traegt Prompt + letzten Modell-Output;
  `stage1._log_stage1_failure()` schreibt beides ins prompts.log
  (`CALL: stage1_* (FAILED)`).

### Sprint G — Grammatik-Postfix

- **G1** `postprocessing.fix_herrn_deklination()`: Praeposition/Genitiv-Artikel
  + „Herr X.“ → „Herrn X.“ (8 Treffer in 4 P1-Dokus); Nominativ bleibt.
- **G2** Klebefehler „Aufenthaltsvon“ (2/2 Verlaengerungen) + generisch
  Genitiv-s + Funktionswort (`_FUNCTION_WORD_SUFFIXES`, nur ganze Woerter).
- **G3** Telemetrie `grammar_fixes {klebebugs, herrn, total}` → QC
  `GRAMMAR_AUTOFIXED` (info).

### Sprint Q — Quellen-Plausibilitaet

Job 0780690823 (13.09.): Kammer-Formular als Verlaufsdoku, Vorlage mit HTML +
Mojibake – kein QC schlug an. c.saur: Stilvorlage nur Ueberschriften.

- Neu `services/source_plausibility.py`: **Q1** `VERLAUF_UNPLAUSIBEL`
  (Therapie-Vokabular < 12/1.000 Woerter; echte Verlaeufe 41–57, Formular 2,6),
  **Q2** `SOURCE_ENCODING_DAMAGED` (≥ 3 HTML-Tags oder ≥ 20 Mojibake-Woerter
  ≥ 1 %) + `strip_html_tags()` vor dem Prompt, **Q3** `STYLE_EXAMPLE_TOO_SHORT`
  (info; < 60 Woerter oder nur Ueberschriften).
- `generation_pipeline`: `job.source_warnings` nach der Extraktion;
  `job_queue.to_dict()` liefert `source_warnings`; SSE-Event
  `{type:"source_warnings"}` einmalig; QC-Check `source_plausibility`
  (Severity warning – Job laeuft weiter, D4).
- **Q4** Frontend `ui.jsx`: `SourceWarningsBox` im `JobProgressBar` (gelb ab
  warning, grau bei info) – sichtbar schon waehrend des Laufs.
- Nebenbei: ESLint-Altfehler `tests/worker_lifecycle.test.js:106`
  (`no-unexpected-multiline`) behoben.

### Sprint L — Entlassbericht-Laenge

10 EB (11.–15.09.) mit 464–649 Woertern bei Ziel 500–900.

- **L1** QC `LENGTH_BELOW_TARGET` (warning, nur entlassbericht < 550 Woerter)
  mit Repair-Hint „nur quellengedeckt erweitern“ – kein automatischer Repair.
- **L2** `render_length_anchor_block`: explizite Mindestlaenge
  (`EB_MIN_WORDS_EXPLICIT = 600`, gedeckelt auf Obergrenze − 100), Richtwert
  ≥ Floor + 100, Aufforderung zu je einem Absatz pro Modalitaet.

### QC-Registry (Reihenfolge)

`… input_truncated, source_plausibility, grammar_autofixed, konjunktiv, …,
befund_separator, befund_fragment, length, eb_length_below_target, …`

### Tests

Neu: `test_v1925_befund_slots.py`, `test_v1925_stage1_robustheit.py`,
`test_v1925_grammatik.py`, `test_v1925_source_plausibility.py`,
`test_v1925_eb_laenge.py`, Frontend `tests/source_warnings.test.jsx`.
Backend 1207 Unit + 296 Integration gruen, ruff 0, ESLint 0 Fehler, Jest 94.

---

## [v19.24] — Interview-Dialog: Gespraechsfuehrung, Trigger, Abschluss-Check (2026-09-18)

Basis: `v19_QA_v02` @ `1f5d935` + v19.23. Ein Patch (Backend + Frontend)
plus `backend/static/systelios.js`.

**Warum.** Der Dialog nach der Sitzung soll sich wie ein Gespraech anfuehlen,
fachlich zwingende Nachfragen zuverlaessig stellen und am Ende einmal ueber
das Ganze schauen - ohne dass das LLM Fachurteile faellt.

### Backend

- `core/interview_phrasen.py` (B1/C2) - Quittungen (nur vor Rueckfragen,
  ruhigere Varianten vor Trigger-Nachfragen) und Ueberleitungen (nur bei
  gegebener Antwort), 3-5 Varianten, ohne direkte Wiederholung, kein LLM.
- `core/interview_sets.py` (B2) - Frage `klient` als erste Frage jedes Sets
  (Pflicht, Abschnitt `meta`, nicht im Prompt). `interview_protokoll.
  extract_klient()` liefert Anrede/Initiale/Geschlecht; voller Nachname wird
  auf die Initiale gekuerzt (Kuerzel-Regel). `jobs.py` uebernimmt Kuerzel und
  Geschlecht aus der Klient-Frage, wenn das UI nichts schickt.
- `services/interview_trigger.py` (B3) - Suizidalitaets-Kette, regelbasiert
  (Marker aus v19.22 + NSSV): Glied 1 Plaene/Handlungen + Absprachefaehigkeit,
  Glied 2 Vereinbarung (Kooperationsbedingung, aerztliche Information,
  Nachtdienst). Laeuft fuer jede Frage; Trigger vor Aspekt-Check (C1);
  Nachfragen zaehlen nicht auf die Grenze "eine LLM-Rueckfrage je Frage".
- `services/interview_dialog.py` - `TurnResult` um `rueckfrage_typ`,
  `trigger_stufe`, `quittung`, `ueberleitung`, `klient` erweitert; Anrede
  geht als `PERSON:` in den LLM-Kontext der Aspekt-Rueckfragen.
- `services/interview_abschluss.py` + `POST /api/interview/abschluss` (B4/C3)
  - einmaliger Check ueber das ganze Protokoll, bis zu drei Punkte
  (widerspruch | luecke | plausibilitaet), ausschliesslich Fragen; LLM-Fehler
  -> keine Punkte.
- `services/interview_protokoll.py` - `eintraege[].nachfragen[{typ, frage,
  antwort}]` (Legacy `rueckfrage`/`rueckfrage_antwort` bleiben lesbar),
  `abschluss[{typ, bezug, frage, antwort, belassen}]`, `session_id`.
  Rendering: "NACHFRAGE (Suizidalitaet)", "ABSCHLUSS-CHECK", belassene Punkte
  als "Bewusst offen gelassen - NICHT ergaenzen". `protokoll_plaintext`
  enthaelt alle Antworten (v19.22-Konfliktcheck sieht die Trigger-Antworten).
- `api/interview.py` - `session_id` buendelt die Prompt-Log-Eintraege eines
  Dialogs (`interview-<session>`), dieselbe ID nutzt der Feedback-Button.
- Tests: `tests/unit/test_v1924_dialog.py` (48), v19.23-Tests angepasst.

### Frontend

- `src/speech.js` - `SpeechProvider` mit Warteschlange (`say(parts)`,
  `cancel()`); Provider `browser` (speechSynthesis), Auswahl ueber
  `window.SYSTELIOS_TTS` (Server-TTS spaeter nur als weiterer Provider).
- `src/interview.jsx` - spricht Quittung + Rueckfrage bzw. Ueberleitung +
  naechste Frage als Sequenz; Nachfragen-Liste je Eintrag mit Typ-Badge
  "Suizidalitaet"; Klient-Frage fuellt Kuerzel/Geschlecht in P1
  (`onKlient`); Abschluss-Phase (Antworten / So lassen / Alles
  ueberspringen); Uebersicht inkl. Nachfragen und Abschluss-Check;
  `FeedbackButton` (`jobId = interview-<session>`, `context =
  interview_dialog`).
- `api.js` - `interviewAbschluss`. Tests: `tests/interview.test.jsx` (8).

### Bekannte Punkte

- Der Feedback-Endpoint akzeptiert unbekannte Job-IDs (job = null); die
  Fallkopie findet die Dialog-Prompts ueber `interview-<session>`.
- Der Eintrag in `docs/interview_modus.md` ist ergaenzt.

---

## [v19.23] — Interview-Modus der Gespraechsdokumentation (2026-09-16)

Basis: `v19_QA_v02` @ `1f5d935`. Ein Patch (Backend + Frontend) plus
`backend/static/systelios.js`.

**Warum.** Nonverbale Verfahren (Kunst, Musik, Koerperarbeit,
Koerpertherapie) liefern kein Transkript; ohne Aufzeichnungseinwilligung
gibt es keine Aufnahme. Statt Transkript fuehrt das System nach der Sitzung
einen kurzen Dialog mit dem Behandler (Frage – Antwort – ggf. EINE
Rueckfrage – Weiter). Das Protokoll ist die Quelle der Doku.

### Backend

- `core/interview_sets.py` — Single Source of Truth der fuenf Fragen-Sets
  (Gespraech, Kunst, Musik, Koerperarbeit, Koerpertherapie), Fragen mit
  Zielabschnitt der Hospidea-Gliederung und Pflichtaspekten; Pflichtfrage
  Selbstgefaehrdung in jedem Set (nicht editierbar).
- `services/interview_protokoll.py` — Validierung des Protokolls (422 ohne
  Pflichtfrage / ohne jede Antwort), `render_protokoll()` (Quellblock mit
  „(nicht erhoben)“), `protokoll_plaintext()` (nur Antworten – damit die
  Fragen selbst keine Marker der Glossar-/Suizid-Logik ausloesen).
- `services/interview_dialog.py` — Rueckfrage-Entscheidung (D1=C):
  Structured-Output-Call (JSON-Schema) prueft die Pflichtaspekte; leere
  Pflichtantwort -> deterministische Rueckfrage ohne LLM; LLM-Fehler ->
  keine Rueckfrage (der Dialog bleibt nie haengen). Max. eine Rueckfrage.
- `api/interview.py` — `GET /api/interview/sets`,
  `POST /api/interview/transcribe`, `POST /api/interview/turn`.
- `transcription.transcribe_dictation()` — Whisper ohne Diarisierung und
  ohne Fuellwortfilter (der haette „Nein.“ als Antwort geloescht), 5-Minuten-
  Limit, Tempdatei wird sofort geloescht.
- `prompts.py` — `INTERVIEW_MODUS_REGELN` (keine erfundenen Klientenzitate,
  Beobachtung/Deutung trennen, Luecken nicht auffuellen, Schlussabsatz
  Selbstgefaehrdung); `build_system_prompt(interview_mode=)`,
  `build_user_content(interview_text=)`.
- `generation_pipeline.py` / `jobs.py` — Form-Feld `interview_protokoll`
  (nur Workflow `dokumentation`), Validierung VOR dem Job-Anlegen, Quellen-
  Gate akzeptiert das Protokoll, `input_meta.has_interview`; ohne Transkript
  landet das Protokoll in `result_transcript` (Transkript-Tab, Repair-Kontext).
- Tests: `tests/unit/test_v1923_interview.py` (44 Tests).

### Frontend

- `src/interview.jsx` — `InterviewDialog`: Set-Auswahl (je Nutzer gemerkt),
  Fragen im UI editierbar mit Reset auf Server-Default, Push-to-talk je
  Antwort oder Text, Vorlesen ueber Browser-TTS (`speechSynthesis`,
  abschaltbar), Rueckfrage-Anzeige, Zurueck/Ueberspringen (nicht bei
  Pflicht), Abschluss-Uebersicht mit Nachbearbeitung. Zustand liegt im
  P1-Draft und ueberlebt Reload (Draft-Cache), kein Server-State (D4=A).
- `P1.jsx` — vierter Quell-Tab „Interview“; der aktive Tab bestimmt die
  Gespraechsquelle des Jobs (Interview-Protokoll statt Audio/Transkript/
  Text); Generieren erst nach abgeschlossenem Interview.
- `api.js` — `fetchInterviewSets`, `interviewTranscribe`, `interviewTurn`
  (mit Start-on-Intent wie `startJob`), `interviewProtokoll` in
  `buildJobFormData`. `ui.InputTabs` bekommt `onChange`.
- Tests: `tests/interview.test.jsx` (6 Tests).

### Bekannte Punkte

- Suizid-Pflichthinweis (v19.22): Quelle fuer den Konflikt-Check ist im
  Interview-Modus `st.interview_plain` (nur Antworten), nicht das gerenderte
  Protokoll - die Pflichtfrage selbst enthaelt das Wort „Suizidalitaet“.
- Gliederung (E2) bleibt ueber den vorhandenen Prompt-Editor je Nutzer
  aenderbar; die Fragen tragen nur den Zielabschnitt.
- Datenschutzaudit: neuer Quelltyp (nur Behandlerstimme, keine
  Klientenaufzeichnung) ist in v3 nachzutragen.

---

## [v19.22] — Pflicht-Hinweis Suizidalitaet in der Gespraechsdokumentation

Basis: `v19_QA_v02` @ `712c4f2`. Featurerequest c.wittenberg 2026-09-11.

### Regel

Jede Gespraechsdokumentation (Workflow `dokumentation`) enthaelt einen
Hinweis zur Suizidalitaet. Entweder der Text gibt Gespraechsinhalte dazu
wieder - oder es wird als letzter Absatz ergaenzt:

> `Frau M. ist glaubhaft absprachefaehig, keine Anzeichen von akuter Suizidalitaet.`

Gilt ausschliesslich fuer `dokumentation`; Anamnese/Befund haben mit
`BEFUND_VORLAGE` bereits eine eigene Formulierung.

### Umsetzung

**`services/suizidalitaet.py` (neu).** Regelbasiert, kein LLM, idempotent.
`resolve_suizid_note(text, source_text, patient_name)` liefert Text und
einen Status: `present` (Inhalt schon da), `appended` (Satz ergaenzt),
`source_conflict`, `no_name`.

**Marker bewusst eng.** Ein Falsch-Positiv wuerde den Pflichtsatz
unterdruecken, ein Falsch-Negativ ergaenzt ihn nur zusaetzlich. Deshalb
KEINE Marker: `sterben`/`tod` (Trauerkontext) und `krisenplan` (wird auch
fuer nicht-suizidale Krisen vereinbart). Selbstverletzung/NSSV erfuellt die
Anforderung nicht (klinisch etwas anderes).

**Aufrufstellen.** `generation_pipeline._finalize` (nach der Platzhalter-
Substitution und nach dem Hard-Cap) und `repair._run_repair_coroutine` -
der Repair-Pfad laeuft nicht durch `_finalize` und koennte den Satz sonst
ersatzlos wegschreiben.

**Prompt.** `WORKFLOW_INSTRUCTIONS_DEFAULT["dokumentation"]` weist das
Modell an, tatsaechlich besprochene Suizidalitaet (auch eine ausdrueckliche
Verneinung) als Schlussabsatz wiederzugeben und KEINE eigene Einschaetzung
zu ergaenzen. Den Standardsatz schreibt nicht das Modell, sondern der
deterministische Pfad.

### Getroffene Entscheidungen

| # | Entscheidung |
|---|---|
| D1 | Ergaenzung laeuft still - kein QC-Hinweis im Normalfall |
| D2 | Quelle thematisiert Suizidalitaet, Output nicht -> NICHT ergaenzen (der Satz waere falsch), stattdessen QC-critical |
| D3 | Freier Schlusssatz, keine eigene Ueberschrift, keine Pflichtsektion |
| D4 | Ohne belastbares Namenskuerzel keine Ergaenzung, QC-warning |
| D5 | Selbstverletzung/NSSV erfuellt die Anforderung nicht |

### QualityCheck

Zwei neue Codes, beide nur fuer `dokumentation`:
- `SUIZIDALITAET_QUELLE_NICHT_UEBERNOMMEN` (critical) - sicherheits-
  relevanter Gespraechsinhalt ging verloren; per Repair behebbar.
- `SUIZIDHINWEIS_FEHLT` (warning) - Namenskuerzel fehlt; nicht durch
  Neu-Generierung behebbar.

Registry-Regel `suizid_note` (letzter Eintrag in `CHECK_REGISTRY`).

---

## [v19.21] — Refactoring-Sprint (Code-Review 2026-09-09)

Basis: `v19_QA_v02` @ `b89f6d8`. Schritte als Einzelpatches (S2, S3, …),
jeweils ohne Verhaltensaenderung fuer Therapeut:innen.

### QC-Registry + Frontend-Lint/-Tests

**QualityCheck-Registry.** `quality_check.CHECK_REGISTRY` listet alle 23
Regeln in Ausfuehrungsreihenfolge mit den Issue-Codes, die sie erzeugen
koennen; `run_quality_check()` baut einen `QCContext` und delegiert an
`run_checks(ctx, only=...)`. `list_checks()` liefert den Katalog. Vorher
standen Reihenfolge und Bedingungen als 24 `issues.extend`-Zeilen plus einem
auseinandergelaufenen Docstring in der Funktion. Tests sichern Reihenfolge
(identisch zu v19.20) und dass jede `ISSUE_CODE_*`-Konstante einer Regel
zugeordnet ist.

**ESLint (Frontend).** `frontend/eslint.config.js` (Flat Config: recommended
+ react + react-hooks; React-Compiler-Regeln bewusst aus), `npm run lint`
als Pflichtschritt fuer Frontend-Patches (0 Fehler; 6 Warnungen
`exhaustive-deps` sind dokumentierte Absicht). Der erste Lauf fand:
- **Regression aus S5 behoben:** `P6.jsx` (ISM) hatte nach der Skelett-
  Migration keine Imports mehr fuer `apiFetch`/`getApiBase`/`friendlyError`
  - der XML-Export war seit S5 kaputt (ReferenceError). Mit dem S5-Bundle
  noch nicht ausgerollt? Bitte pruefen; dieses Bundle enthaelt den Fix.
- Toter Code: `saveUrl`/`urlInput` im App-Root (Settings-Dialog hat kein
  URL-Feld mehr; die Funktion referenzierte ein nicht existierendes
  `setBackendOffline`), Datei-Upload-Pfad in `AudioInput` (`handleFile`,
  `switchMode`, drei States), ungenutzte React-Imports in sechs Modulen,
  `ladebusy`/`structuralWfs` in P5.

**React-Tests.** Jest laeuft jetzt ueber babel-jest (JSX), mit
`@testing-library/react` + `jest-dom`. Neu `tests/workflow-run.test.jsx`
(10 Tests): Start -> Poll -> Ergebnis, onResult/onError, Abbruch, Resume-
Banner, Doppel-Attach-Schutz, Reset, ActionBar, KlientControls. 60 Tests
gesamt.

### R7 — Stage-1-Verdichter auf gemeinsamem Geruest

Neu `app/services/summary_runner.py`: `anti_think_suffix()`,
`source_block()`, `wrap_no_think()`, `stage1_generate()` (gemeinsame
generate_text-Parameter, Modell-Aufloesung), `run_chunked()` (Chunk-
Orchestrierung mit Telemetrie-Merge), `word_count()`. `transcript_summary`,
`verlauf_summary` und `document_summary` nutzen das Geruest; ihre fachlichen
Unterschiede (Prompt-Texte, Zielwortzahlen, Retry-Politik, Audit-Felder)
bleiben als Strategie im jeweiligen Modul. 1.436 -> 1.284 Zeilen, keine
dreifachen Kopien mehr.

**Regressionsnachweis:** Snapshot-Test `tests/unit/test_v1921_r7_summary_runner.py`
- 8 Pfade (Haupt-Call, Chunking, Laengen-/Cap-Retry) x 3 Verdichter, 19
LLM-Calls: System-/User-Prompts (SHA-256), max_tokens, Temperatur, Flags,
Modell und Ergebnis-Kennzahlen sind byte-identisch zu v19.20. Einzige
Aenderung: das Chunk-Ergebnis des Transkript-Verdichters liefert jetzt wie
das des Verlauf-Verdichters `system_prompt`/`user_content` mit (prompts.log
sieht damit auch gechunkte Transkript-Verdichtungen).

### S6 — `create_generate_job` zerlegt (Backend)

**S6a** Neu `app/services/generation_pipeline.py`: `UploadBundle.read(**uploads)`
ersetzt die 18-Zeilen-`*_bytes/*_name`-Leiter, `PipelineInput` buendelt alle
(validierten) Form-Felder + Uploads, `input_meta()`/`patient_kuerzel`,
`parse_dx_list()`, `normalize_geschlecht()`.

**S6b** Das 1.000-Zeilen-Closure `_run()` ist jetzt `run_generation(ctx, job)`
mit sieben Phasenfunktionen (`_resolve_transcript`, `_extract_sources`,
`_resolve_style`, `_resolve_patient_and_gates`, `_build_prompts`, `_generate`,
`_finalize`) und einem `PipelineState` fuer den phasenuebergreifenden
Zwischenstand. Logik byte-nah uebernommen (Closure-Variablen -> `ctx.*`,
uebergreifende Locals -> `st.*`; Token-basiert umgeschrieben, keine
String-Treffer). Komplexitaet: `create_generate_job` 104 -> < 10, groesste
Phase 21 (vorher `_run` 101). Phasen sind einzeln aufrufbar - neue Tests
in `tests/unit/test_v1921_s6_pipeline.py` (Transkript-Phase, Orchestrierung,
ISM-Kurzpfad).

**S6c** `jobs.py` ist reines Routing (2.889 -> 674 Zeilen). Verschoben nach
`app/services/`: `generation_pipeline.py` (run_generation + Phasen,
Quellen-Gate, Budget-Guard, ISM-Kurzpfad), `stage1.py` (Verlauf-/Transkript-
Verdichtung), `repair.py` (Repair-Flow), `prompt_log.py` (prompts.log).
`jobs.py` re-exportiert die Unterstrich-Namen weiter (Tests/Skripte, die
`from app.api.jobs import _x` nutzen, laufen unveraendert). Test-Patches auf
Pipeline-Interna zeigen jetzt auf das Modul, das den Namen aufloest
(`app.services.generation_pipeline.generate_text`, `app.services.stage1.
summarize_verlauf`, `app.services.repair.generate_text`).

### S5 — Frontend-Panel-Skelett (P2, P2b, P3, P3b, P4, P6)

Neu `frontend/src/workflow-run.jsx`: `useWorkflowRun()` (Output-State,
attach/pollJob, Resume-Banner + Auto-Resume, cancel, start, reset, mit
`onResult`/`onError`-Hooks fuer Panel-Spezifisches), `<WorkflowActionBar>`
(Start/Abbrechen + optionale linke Controls) und `<KlientControls>`
(♀/♂ + Kuerzel). `StyleSourceCard` (Stilvorlage Datei/Text) in `ui.jsx`,
`buildPatientName()` in `shared.js` (P1/P2 nutzen es). Panels enthalten nur
noch Formular, `run()` mit ihrer Feldzuordnung und Output-Komposition:
P4 227→149, P3 441→281, P2 555→360, P6 507→461 Zeilen; jscpd 21→6 Klone
(371→72 Zeilen). Kein Verhaltenswechsel; Nebeneffekt: P2 hat jetzt wie die
anderen Panels den Doppel-Attach-Schutz (attachedRef). P1 bleibt auf seinem
Multi-Draft-Modell (eigener Sprint).

### S1 — Prompt-Defaults: eine Quelle, Drift-Schutz **(fachliche Aenderung!)**

**Befund.** Seit v18 gab es zwei handgepflegte Fassungen der Workflow-
Default-Anweisungen: `prompts.py::WORKFLOW_INSTRUCTIONS_DEFAULT` (Eval, Tests,
ISM) und `frontend/src/prompt-defaults.jsx` (das, was Therapeut:innen im
Prompt-Editor sehen und was produktiv ans LLM geht). Die Backend-Fassung
enthielt spaetere Fixes, die das Frontend nie bekam.

**Entscheidung (2026-09-10): Backend-Fassung gilt.** Damit aendern sich die
produktiven Default-Prompts - bitte vor Rollout an echten Faellen pruefen:

- *Dokumentation*: Abschnitt **Organisatorisches** (nur bei administrativen
  Absprachen), Trennung Auftrag vs. Organisatorisches, **Einladungen nur wenn
  tatsaechlich ausgesprochen** (Anti-Halluzination, Issue 1), jeder Abschnitt
  als dichter Fliesstext. Der Frontend-Block "PERSPEKTIVE/Keine Wir-Form"
  entfaellt - er ist seit v19.17 nicht editierbarer Pflichtkern des
  System-Prompts.
- *Akutantrag*: Argumentationsstruktur (Kernbegruendung, Belege, ambulante
  Insuffizienz, Dringlichkeit) statt Symptomliste. Die Anweisung "Beginne
  WOERTLICH mit der Standardformulierung" entfaellt - das Backend gibt den
  Satz bereits als Primer vor (Completion-Modus); die Doppelung konnte den
  Satz zweimal im Output erzeugen.
- *Entlassbericht*: Hinweis auf Prae-/Post-Testwerte aus der Berichtsvorlage.
- *Anamnese, Verlaengerung, Folgeverlaengerung*: nur Typografie; der
  Rollensatz steht in `BASE_PROMPTS`.
- *ISM, Befundvorlage*: unveraendert.

**Mechanik.** `prompts.py` ist die einzige Quelle. `backend/scripts/
export_prompt_defaults.py` generiert `prompt-defaults.jsx` (Kopfzeile
"GENERIERT, NICHT EDITIEREN"); `npm run build` ruft ihn als `prebuild`;
`lint_gate.sh` und `tests/unit/test_prompt_defaults_sync.py` schlagen bei
Drift fehl. `GET /api/workflows` liefert zusaetzlich `instructions_default`
je Workflow und `befund_vorlage`.

### S4 — Kleine Struktur-Fixes (Backend)

- `JobQueue.get_job_from_db()` nutzt `_db_job_to_dict()` (vorher zwei
  handgepflegte Kopien desselben 33-Feld-Dicts fuer Einzel-GET und Liste).
- `recordings.py`: `_load_owned_recording(session, rec_id, user)` ersetzt
  sechs identische Query+404+Owner-Bloecke.
- `extraction.py`: `_match_heading()` / `_is_section_end()` als gemeinsame
  Bausteine von `extract_docx_section` und `_extract_section_by_text`.
- Upload-Bereinigung nur noch im Retention-Task (`retention.cleanup_uploads`,
  alle 6h, Datei-I/O per `asyncio.to_thread`); die zweite stuendliche
  Schleife in `main.py` entfaellt.

### S3 — Toter Code, Test-Infrastruktur

**Entfernt (Backend).** `api/generate.py`, `api/documents.py`,
`api/transcribe.py`, `services/docx_fill.py` — Router waren in `main.py` nie
gemountet, die importierten Schemas existierten nicht mehr (Module waren nicht
importierbar). ORM-Klasse `StyleProfile` samt `schema.sql`-DDL (bestehende
Tabelle `style_profiles` in Produktions-DBs bleibt unberuehrt; manuell:
`DROP TABLE IF EXISTS style_profiles;`). Ungenutzte Settings `SECRET_KEY`,
`RETENTION_INTERVAL_HOURS`, `RATE_LIMIT_PARALLEL_JOBS`,
`ACCESS_TOKEN_EXPIRE_MINUTES` (auch aus `.env.example`, `runpod-start.sh`,
`testrun.py`). `workflows.py`-Helfer ohne Aufrufer. Leere `eval_data.tar.gz`.

**Entfernt (Frontend).** `entry.jsx` und der Inline-Mount in
`klinische-dokumentation.jsx` — `main.jsx` ist jetzt einziger Einstieg
(mountet auf `#systelios-app`, wie das Confluence-Makro es liefert).
`api.js::generate()` (blockierender Flow, kein Aufrufer, Feld-Drift gegenueber
`startJob()`), ersetzt durch die pure Funktion `buildJobFormData()`.
Ungenutzte Exporte in `shared.js`, `hooks.jsx`, `joblist.jsx`, `qa.jsx`
(`useQualityCheck`-Alias).

**Behoben.** `JobQueue._persist_job()` legt die Job-Zeile per INSERT an,
wenn das UPDATE keine Zeile trifft (der initiale INSERT laeuft als
Fire-and-forget-Task; fiel er aus, fehlte der fertige Job in `/api/jobs` und
nach Restart).

**Tests.** `tests/conftest.py` rekonstruiert (lag nicht im Repo; die
Integration-Suite war ab Clone nicht startbar). Zwei veraltete Prompt-Tests
an v19.20-M4 (EB-Laengenanker) und Issue-2 (konditionales Glossar)
angepasst. `test_eval.py` Transkript-Cache auf die Recordings-API (P0)
umgestellt. Jest: `buildJobFormData`/`startJob` statt `generate()`.

### S2 — Lint-Gate + mechanische Fixes

`scripts/lint_gate.sh` (ruff, Config `ruff.toml`, `F841` aktiv) als
Pflichtschritt vor jedem Patch. 20x `B904` (`raise … from`), 7x `B905`
(`zip(strict=True)`), `RUF006` in `recordings.py` (Task-Referenz +
Fehler-Logging), `ASYNC240` (blockierende Datei-I/O in `retention`, `main`,
`extraction` per `asyncio.to_thread`). Tote Zweige in `jobs.py`,
`testrun.py`, `prompts.py`.

---

## [v19.2] — Two-Stage-Pipeline (Verlauf-Verdichtung)

**Motivation.** Bei Verlängerungs- und Entlassberichten lieferte das LLM
(Qwen3:32b) auf Rohdokumenten mit 10–13k Wörtern wiederholt zu
oberflächliche, halluzinationsanfällige Antrags-Texte. Der Kontext war zu
voll für die strukturierte Synthese, gleichzeitig waren wichtige
Sitzungsdetails durch PDF-Header und leere Sitzungs-Anker übertüncht. Die
Two-Stage-Pipeline löst das, indem Stage 1 die Verlaufsdoku zuerst auf eine
strikt quellentreue, strukturierte Zusammenfassung (~4 000 Wörter)
verdichtet, die dann als Input in die eigentliche Antrags-Generierung
(Stage 2) wandert.

### Hinzugefuegt

- **`backend/app/services/verlauf_summary.py`**: Stage-1-Service mit:
  - `VERLAUF_SUMMARY_SYSTEM_PROMPT` (Anti-Halluzination, Quellentreue, keine
    Interpretation/Wertung, namentliche Verfahrens-Übernahme).
  - `VERLAUF_SUMMARY_STRUCTURE` (vier Pflicht-Sections: Sitzungsübersicht,
    Bearbeitete Themen, Therapeutische Interventionen, Beobachtete
    Entwicklung).
  - `_build_focus_hint()` mit workflow-spezifischen Hinweisen für
    Verlängerung, Folgeverlängerung und Entlassbericht.
  - `summarize_verlauf()` als Service-Entry-Point: niedrige Temperatur
    (0.2), Plausibilitätsprüfung (40 %–200 % Zielwortzahl),
    Kompressions-Ratio im Result.
  - `detect_summary_hallucination_signals()` mit vier Schweregraden:
    `critical` (erfundene ICD-Codes), `high` (erfundene Therapieverfahren),
    `medium` (erfundene Patienten-Zitat-Wendungen, implausible
    Sitzungs-Anzahl).
  - `_retry_stricter_summary()`: ein einzelner Retry bei `critical`-Signalen
    mit `temperature=0.1` und expliziter Issue-Nennung im Prompt.
- **`backend/app/services/llm.py`**: `clean_verlauf_text()` additiv um
  neues PDF-Layout erweitert — OCR-Klebebug-Repair
  (`Aufwecken, Anregen09:30` → `Aufwecken, Anregen 09:30`), Seiten-Marker
  (`--- Seite N ---`, `[Pseudonymisiertes Dokument …]`), inhaltslose
  Sitzungs-Header (Typ+Zeit ohne Folge-Inhalt), Datums-Normalisierung zu
  `### DD.MM.YYYY`, Doppel-Datum-Dedup an Seitengrenzen. Alle bisherigen
  Patterns bleiben unverändert.
- **`backend/app/services/llm.py::generate_text()`**: neuer Parameter
  `temperature_override: Optional[float] = None`, durchgereicht zu
  `_generate_ollama`. Wird von Stage 1 genutzt um Quellentreue zu
  erzwingen.
- **`backend/app/api/jobs.py`**: Stage-1-Integration in der `_run`-Pipeline.
  Aktivierungs-Bedingungen: `settings.STAGE1_ENABLED`, Workflow in
  `{verlaengerung, folgeverlaengerung, entlassbericht}`, bereinigter
  Verlauf ≥ 1 500 Wörter. Bei Erfolg wird `verlaufsdoku_text` durch die
  Summary ersetzt; bei Fehler greift ein Fallback auf den Roh-Verlauf.
- **Audit-Bundle** `verlauf_summary_audit` mit Feldern `applied`,
  `raw_word_count`, `summary_word_count`, `compression_ratio`,
  `duration_s`, `telemetry`, `retry_used`, `retry_telemetry`, `degraded`,
  `issues`, `target_words`, `fallback_reason`. Bei nicht-ausgelöstem
  Stage 1 für einen Whitelist-Workflow wird ein
  `applied=False`-Mini-Audit mit `fallback_reason` geschrieben — damit
  ist im Performance-Log sichtbar, _warum_ Stage 1 nicht griff.
- **DB-Schema**: zwei neue Spalten in `jobs`:
  - `verlauf_summary_text TEXT` — die verdichtete Zusammenfassung selbst
  - `verlauf_summary_audit JSONB` — das Audit-Bundle
  Beide additiv via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in
  `scripts/schema.sql` (Block C). NULL bei Jobs aus Pre-v19.2-Zeit.
- **`backend/app/services/job_queue.py`**: `JobState`, `to_dict()`,
  `run_job()`, `_persist_job()` und `get_job_from_db()` um die zwei neuen
  Felder erweitert. `_log_performance()` schreibt zusätzlich einen
  kompakten `stage1`-Block ins Performance-Log
  (`applied`/`raw_words`/`summary_words`/`compression_ratio`/`duration_s`/
  `retry_used`/`degraded`/`issue_count`/`fallback_reason`).
- **`GET /api/jobs/{id}`** liefert automatisch die zwei neuen Felder mit —
  kein Endpoint-Patch nötig, da `to_dict()` und `get_job_from_db()`
  durchgereicht werden.
- **`backend/app/core/config.py`**: `STAGE1_ENABLED: bool = True` als
  Notabschalter, `STAGE1_TARGET_WORDS: int = 4000` als Zielwortzahl.
- **`backend/tests/test_eval.py`**: neuer CLI-Flag `--summary-mode` mit
  drei Modi (`auto`, `require_stage1`, `require_no_stage1`) für
  reproduzierbare Eval-Läufe und A/B-Vergleiche. `EvalResult` zeigt
  Stage-1-Status im Report-Output; `stage1_degraded` treibt den Score
  analog zu `degraded` auf 0.
- **Doku**: neue Dateien `docs/architecture/two_stage_pipeline.md` und
  `docs/dsgvo/verlauf_summary_audit.md`.

### Geaendert

- **`clean_verlauf_text()`**: Logging um Wort-Reduktions-Prozent und
  Counter für entfernte leere Sitzungs-Header / Seiten-Marker erweitert.
  Output-Format bleibt rückwärtskompatibel.
- **Performance-Log** (`/workspace/performance.log`): jeder
  abgeschlossene Job mit Stage 1 erhält einen zusätzlichen `stage1`-Block
  im JSON. Bestehende Auswertungen die nur Top-Level-Felder lesen sind
  nicht betroffen.

### Tests

99 neue/erweiterte Tests in fünf Files:
- `test_llm_postprocessing.py` (33): Klebebug-Repair, Seiten-Marker,
  leere Sitzungs-Header, Datums-Normalisierung, reales Format
- `test_verlauf_summary_prompt.py` (12): System-Prompt, Struktur,
  Workflow-Hints
- `test_verlauf_summary_halluzinations.py` (15): alle vier
  Issue-Severities + Edge-Cases
- `test_verlauf_summary_service.py` (12): Happy-Path, Plausibilität,
  Prompt-Shape
- `test_verlauf_summary_retry.py` (8): Trigger-Bedingungen, Failure-Modi,
  Temperatur-Stufung
- `test_job_queue_two_stage.py` (10): Aktivierungsregeln, Audit-Form
- `test_verlauf_summary_persistence.py` (9): JobState, to_dict, run_job,
  _persist_job (mit DB-Mock), _log_performance

### Operativ

- **Notabschalter.** Wenn Stage 1 in Produktion Probleme macht:
  `STAGE1_ENABLED=false` in `backend/.env` und Backend neu starten —
  Verhalten fällt zurück auf Pre-v19.2 (Roh-Verlauf direkt in Stage 2).
- **Eval-A/B-Vergleich.**
  `pytest tests/test_eval.py --summary-mode=require_no_stage1` (mit
  `STAGE1_ENABLED=false` im Backend) liefert die Pre-v19.2-Baseline.
  Dieselben Tests mit `--summary-mode=require_stage1` und
  `STAGE1_ENABLED=true` liefern die v19.2-Vergleichswerte.
- **Migration.** `scripts/schema.sql` wird beim Pod-Start ausgeführt und
  legt die zwei neuen Spalten idempotent an. Kein manueller Eingriff
  nötig. Bestehende Jobs haben NULL in beiden Spalten — das ist erwartet
  und korrekt.

---

## Frühere Versionen

Frühere Versionen (v17–v19.1) sind in diesem CHANGELOG nicht
zurückübertragen. Details zu Think-Block-Detection, Retry-Layer und
Telemetrie-JSON-Spalte stehen in den Code-Kommentaren der jeweiligen
Module (Suchpattern: `v19.1:` im Backend-Code).
