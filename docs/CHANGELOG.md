# Changelog — scriptTelios

Alle nennenswerten Aenderungen am Backend werden hier festgehalten.

Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/);
das Projekt nutzt Sprint-Versionen (v18, v19, v19.1, …) statt SemVer-Patch-Counter.

---

## [v19.29] — Live-QC für den ISM-Fragebogen (P6) + S6c-Rest (2026-09-22)

Basis: `v19_QA_v02` @ `ff58d0d` (nach v19.27/v19.28). Ein Patch (Backend +
Frontend, `systelios.js` neu gebaut).

Ausgangslage: Der ISM-QC lief nur einmal nach der Generierung auf dem
Modell-JSON und wurde in P6 als gelber Hinweiskasten gezeigt; der vom
Therapeuten editierte Stand wurde vor dem SNS-XML-Export nur strukturell
(Pydantic) geprüft. Identische Pole, Duplikate oder Wir-Form aus dem
Editieren gingen unbemerkt ins XML. `checks_run` stand für ISM auf 34.

### S0 — Rest von S6c (v19.21)
- Re-Export-Shim am Kopf von `app/api/jobs.py` entfernt; die Tests
  `test_jobs_repair`, `test_jobs_stage1_phases`, `test_v1916_transcript_guard`,
  `test_v1919_sprint_r`, `test_v1921_s6_pipeline`, `test_v1920_log_therapeut` importieren aus
  `app.services.repair/stage1/generation_pipeline/prompt_log`. `jobs.py` importiert nur
  noch `_setup_prompt_logger` aus `prompt_log`. Kein Verhalten geändert.

### Backend
- `services/ism.py`: Regelkatalog `ISM_CHECKS` (json_valid, pole_identisch,
  wir_form, item_duplikat, **frage_kurz** (warning, < `ISM_FRAGE_MIN_CHARS`=12),
  **pol_zu_lang** (info, > `ISM_POL_MAX_CHARS`=60), faktor_unbesetzt) mit
  `run_ism_checks(fb)` und `ism_issues_for_payload(dict)` — Strukturfehler
  werden als `ISM_JSON_INVALID`-Issue mit `code_detail.fields` gemeldet, nie
  als Exception (D1=A). `run_ism_quality_check(text)` delegiert dorthin.
- `quality_check.serialize_issues`: `checks_run` für `ism_fragebogen` =
  `ISM_CHECKS_RUN` (7) statt Registry-Länge.
- `api/ism.py`: neu `POST /api/ism/check` (`{fragebogen: any}` → 200 mit
  `serialize_issues`-Format, auch bei ungültiger Struktur);
  `POST /api/ism/xml` liefert zusätzlich `quality_check` auf genau dem
  exportierten Stand (S4).

### Frontend (D3–D5)
- Neu `src/ism-check.jsx`: `useIsmLiveCheck` (POST `/ism/check`, Abbruch
  laufender Requests, fehlertolerant), `issuesForItem`, `exportNeedsConfirm`,
  `confirmExportText`.
- `P6.jsx`: gelber Kasten ersetzt durch read-only `QualityCheckPanel` unter
  der Vorschau (grüner Status „alle 7 Checks bestanden"). Check-Trigger
  (D4=B): Blur von Frage/Polen/Begrüßung/Verabschiedung, Item hinzufügen/
  löschen, Export. Export: bei critical/warning Bestätigungsdialog, kein
  Blockieren (D3=A). Item-Bezug: `IsmItemEditor` mit `issues`/`focused`
  (Markierung + „n Hinweise"), Klick auf ein Issue scrollt zum Item
  (`QualityCheckPanel.onFocusItem`).
- `qa.jsx`: `QualityCheckPanel` optional `onFocusItem`; Styles
  `.qc-item-link`, `.ism-item-flagged/-critical/-focus/-marker`.

### Tests
- `test_ism.py` + 9 (Katalog/`checks_run`, neue Regeln, Endpoint, Export
  mit QC); `frontend/tests/ism_live_check.test.jsx` (8). Jest 124 passed;
  ESLint 0 Fehler; ruff sauber.

---

## [v19.28.1] — Fallformel als strukturierter Editor (2026-09-22)

Frontend-Patch auf v19.28 (`systelios.js` neu gebaut, kein Backend-Anteil).
Entscheidungen: Feld E „Aufbau des Berichts" ist eingeklappt (optional);
die Therapeut:in editiert nie Markdown (Drift-Risiko), sondern Felder;
abgewaehlte Themen werden beim Senden weggelassen.

- `src/fallformel.js`: `parseFallformel(text)` (Markdown → {auftrag,
  themen[{titel, desc, belege}], wendepunkte{Einzel/Gruppe/Nonverbal: []},
  symptom, offen, extra}) und `serializeFallformel(ff, selection)` (Struktur
  + Auswahl → dasselbe `###`-Markdown, das das Backend versteht; Wendepunkte
  je Modalitaet mit „; " gejoint, leere Modalitaet = „keine Wendepunkte
  dokumentiert", keine Auswahl = Standardsatz). Roundtrip-Test.
- `panels/P4.jsx`: Card H rendert jeden Abschnitt einzeln — Auftrag,
  Themen (Nummernkreis = an/ab + Reihenfolge im Bericht, ↑↓, ×, „+ Eigenes
  Thema"; Titel/Beschreibung/Belege als Felder), Wendepunkte je Modalitaet
  (Zeilen + / ×), Symptomveraenderung, Offene Themen. Auswahl wirkt sofort
  (Zaehler + aufklappbare Vorschau des gesendeten Markdown). „Vorschlag des
  Modells wiederherstellen" aus dem gespeicherten Rohtext. Draft-Cache:
  `fallformel` (Struktur), `fallformelSel`, `fallformelProposal`.

---

## [v19.28] — Thematischer Entlassbericht: Fallformel, Struktur-Schalter, Testwerte-Vollständigkeit (2026-09-22)

Basis: `v19_QA_v02` @ `1bcce0f`. Ein Patch (Backend + Frontend, `systelios.js`
neu gebaut, DB: zwei neue Spalten via `schema.sql`). Ausloeser: Wunsch,
den Entlassbericht statt in Modalitaetsabschnitten (Ankommen / Einzel /
Gruppe / Nonverbal / Empfehlungen) thematisch aufzubauen: Auftrag des
Klienten → Erarbeitung des zentralen Themas/Musters → Prozessfortschritte in
Einzel-, Gruppen- und nonverbaler Therapie → Reflexion und
Symptomveraenderung → Empfehlungen. S0-Prototyp (22.09., EB-FrauM/EB-HerrR,
gemma4:31b, `misc/s0_run.py`): thematische Variante in beiden Faellen klar
besser (Struktur, Wendepunkte, Empfehlungen), Status quo verlor bei HerrR
sogar Fakten (Stage-1-Summary liess den Dezember aus) und empfahl
„Stabilisierung der Partnerschaft" trotz dokumentierter Trennungsentscheidung.

### Entscheidungen
- D1=B: das Modell schlaegt eine **Fallformel** vor (Stage 1b), die
  Therapeut:in waehlt/editiert und generiert neu. D2: mehrere
  Themenkandidaten, max. 3 gewaehlt. D3=A: Modalitaeten bleiben eigene
  Absaetze innerhalb „Prozessfortschritte" (Anti-Wiederholungs-Regel im
  Prompt + Redundanz-Check). D4: Prozessreflexion in Teil 4. D5:
  **Schalter im Formular**, Default = Status quo. D6: Stage 1 erhaelt
  dokumentierte Hypothesen. D7: Testwerte-Vollstaendigkeitscheck. D8:
  Primer-Doppelung bei gemma behoben.

### S1 — Stage 1 (`services/verlauf_summary.py`)
- EB-Struktur bekommt einen vierten Abschnitt `### Dokumentierte Hypothesen
  und Muster` („Laut Protokoll (Datum): …"; nur Wiedergabe, keine eigene
  Deutung — Regel 2 bleibt). Fokus-Hint EB: Wendepunkte/benannte Anteile
  erhalten, **gesamte Zeitspanne** abdecken.
- Neu: `detect_coverage_gap()` — fehlt in der Summary ein Monat, der im
  Rohtext wesentlich vertreten ist (>= 15 % der Datumsmarker, >= 3), loest
  das denselben Retry aus wie ein critical-Signal; bleibt die Luecke →
  `degraded`, Issue `abdeckung_luecke` im Audit. Nur auf Gesamtebene (nicht
  je Chunk). Snapshot-Fixture `stage1_prompts_v1920.json` (verlauf_chunked)
  wegen des bewusst geaenderten EB-Prompts neu erzeugt.

### S2 — Stage 1b Fallformel (`services/fallformel.py`, neu)
- Ein LLM-Call (Hauptmodell des Jobs, temp 0.3) aus Stage-1-Summary +
  Antragsvorlage (+ Prozessreflexion) → Markdown mit fuenf `###`-Abschnitten:
  Auftrag · Themenkandidaten (1–3, je mit Sitzungsbelegen) · Wendepunkte je
  Modalitaet · Symptomveraenderung · Offene Themen. Keine Testwerte/
  Diagnosen (die kommen aus der Antragsvorlage). Kein Muster → Standardsatz.
- Checks: Abschnitte vollstaendig, <= 3 Kandidaten (hart gekuerzt),
  Verfahrens-/Zitat-Signale (wiederverwendet aus Stage 1), Datumsbelege ohne
  Quelle. Kein Retry — die Therapeut:in korrigiert im UI (D1=B).
- Pipeline: neue Phase `_run_fallformel` (nach Gates, vor Prompt-Bau), nur
  bei `workflow=entlassbericht` und `eb_struktur=thematisch`. Form-Feld
  `fallformel` (bestaetigte Fassung) ueberspringt den LLM-Call. Fehler in
  Stage 1b kippen den Job nicht (Audit `applied=False, fallback_reason`).
- Persistenz: `jobs.fallformel_text`, `jobs.fallformel_audit` (JSONB;
  `ADD COLUMN IF NOT EXISTS` in `scripts/schema.sql`); `to_dict`,
  DB-Load und Repair-Kontext durchgereicht. prompts.log: `stage1b_fallformel`.

### S3 — Stage 2 (`services/prompts.py`, `api/jobs.py`, `llm.py`)
- Neues Form-Feld `eb_struktur` (`modalitaet` | `thematisch`; alles andere
  = Status quo) und `fallformel`. `WORKFLOW_INSTRUCTIONS_EB_THEMATISCH`
  (fuenf Teile, „Thema in Teil 2 EINMAL erklaeren, Modalitaetsabsaetze
  bringen nur den neuen Schritt") — im Manifest als
  `instructions_thematisch`, im Export als `P_ENTL_THEMATISCH`.
- `build_system_prompt(eb_struktur=…)`: thematisch → Few-Shot
  `FEW_SHOT_ENTLASSBERICHT_THEMATISCH` (gleicher Fall, thematische
  Reihenfolge) + `BASE_PROMPT_EB_THEMATISCH_ZUSATZ`; das Stilbeispiel wird
  **nur als Schreibstil** eingehaengt (nicht als strukturelle Schablone —
  sonst dreht ein modalitaetsbasiertes Beispiel die Struktur zurueck).
- `build_user_content(eb_struktur=…, fallformel_text=…)`: FALLFORMEL-Block
  mit Einbau-Regeln (Teil 1–5 ← Abschnitte; Belege nicht uebernehmen)
  zwischen Quellen und Fokus-Themen; Prozessreflexion-Regel zeigt bei
  thematisch auf Teil 4; Schlusssatz je Struktur. Status-quo-Pfad
  byte-identisch (Test).
- D8 (`llm._postprocess_text`): Primer ohne abschliessendes Whitespace wird
  nicht mehr vorangestellt, wenn der Output mit einem Grossbuchstaben
  beginnt (gemma setzt den Prefill nicht fort → „AufenthaltsWir erlebten",
  „AufenthaltsZu Beginn seines" in 2 von 4 S0-Berichten).

### S4 — QC (`services/quality_check.py`, `quality_specs.py`)
- `required_sections_for(workflow, eb_struktur)`: thematisch → Anliegen ·
  **Zentrales Thema** · Behandlungsverlauf · **Reflexion und
  Symptomveraenderung** · Empfehlung (neue Synonymlisten); Status quo
  unveraendert. `QCContext.eb_struktur/fallformel_text`; run_job liest den
  Schalter vom Job (Repair: vom Parent/Audit).
- Neu (nur EB thematisch mit Fallformel): `THEMA_NICHT_AUFGEGRIFFEN`
  (warning, repair-faehig), `THEMA_KOHAERENZ` (info, Thema in < 3
  Absaetzen), `WENDEPUNKT_NICHT_AUFGEGRIFFEN_<MOD>` (info; S0:
  Elternbesuch ging verloren). Neu (EB beide Strukturen):
  `REDUNDANZ_ABSAETZE` (info, Jaccard >= 0.6 ueber Absatzgrenzen).
- D7 `TESTWERTE_*` (EB, mit Antragsvorlage): Prae/Post-Paare der Vorlage
  („Skala: prae; post (") werden geparst; fehlt eine **unguenstige**
  Veraenderung im Bericht, waehrend guenstige genannt werden →
  `TESTWERTE_UNGUENSTIG_VERSCHWIEGEN` (warning; S0: DASS-21 Angst 2 → 12 in
  beiden Varianten verschwiegen); sonstige fehlende → `_UNVOLLSTAENDIG`
  (info); gar keine genannt → `TESTWERTE_FEHLEN` (warning). „0; 0"-Skalen
  werden nie gefordert. Registry-Reihenfolge-Test aktualisiert.
- Fokus-Themen bleiben im Stichpunkt-Check; die Fallformel laeuft NICHT
  durch den Zeilenparser (S0-Befund: Fragment-Fehlalarme).

### S5 — Frontend (`panels/P4.jsx`, `fallformel.js`, `api.js`)
- Card E „Aufbau des Berichts": Segmented Control (Standard /
  Thematisch), Default Status quo; beim Umschalten wechselt der
  Prompt-Editor-Default mit (nur wenn er noch den Default zeigte).
  Fokus-Themen → F, Prompt/Modell → G.
- Card H „Fallformel": Themenkandidaten als Checkboxen (max. 3), Volltext
  editierbar, Stage-1b-Signale, „Mit dieser Fallformel neu generieren" (gleiche
  Dateien, Form-Feld `fallformel`). Persistenz im Draft-Cache
  (`struktur`, `fallformelText`, `fallformelSel`). `src/fallformel.js` =
  JS-Spiegel von `select_themen` (Jest).
- `buildJobFormData`: `eb_struktur`, `fallformel`.

### Sonstiges
- `misc/s0_run.py`: S0-Runner (zwei Faelle × zwei Strukturen gegen
  `/api/jobs/generate`, HMAC wie das Makro).
- Doku: `docs/two_stage_pipeline.md` (Stage 1b), Sprintplan
  `docs/v19_28_thematischer_entlassbericht_plan.md`.
- Tests: +73 Backend (S1/S2/S3/S4/D8/Pipeline), +13 Frontend.

---

## [v19.27] — Verfahrenswissen, Fokus-Treue und QC für die Gesprächsdokumentation (2026-09-22)

Basis: `v19_QA_v02` @ `5edeaae`. Ein Patch (Backend + Frontend, `systelios.js`
neu gebaut). Ausloeser: Job `89276fb6` (22.09.), Fokus-Eingabe „IRRT
Traumasitzung …" — der System-Prompt sagte gleichzeitig „Benenne KEIN
Therapieverfahren namentlich" (neutrales Glossar, Schalter sah die
Stichpunkte nicht und kannte IRRT nicht), Stage-1 machte ein Randthema zum
Hauptanliegen, der Stichpunkte-Check meldete nichts (Akronym „IRRT" fiel an
der Mindestlaenge 5 aus der Pruefung, ODER-Logik durch woertlich
uebernommenes „Operation … Geburt … Tochter" erfuellt). QC: 0 Issues,
Feedback Rating 2.

### Teil 0 — Verfahrensregister (`services/verfahren.py`, D10/D11)
- Neu: `Verfahren`-Dataclass (Stämme, Phasen, Doku-/Stage-1-Hinweise,
  Phasen-Marker, `parts_work`) und `VERFAHREN_REGISTER` (IRRT nach
  Schmucker/Köster: Vorbereitung · Phase 1 Wiedererleben 1a/1b · Phase 2
  Täterkonfrontation am Hot Spot · Phase 3 Zuwendung zum Damaligen Ich,
  Abschlussbild · Nachbesprechung; EMDR, IFS/Anteilearbeit, Ego-State/
  Stuhlarbeit, Schematherapie, Hypnose, Traumakonfrontation).
  `erkannte_verfahren()` = Substring-Erkennung, kein LLM.
- Phasen sind ein **Beobachtungsraster**, kein Soll-Verlauf: Prompt und QC
  bewerten nie, ob eine Phase „gelungen" ist (im Ausloeserfall war die
  nicht vollzogene Entmachtung fachlich der Erfolg — Täter = lebensrettender
  Arzt; Einordnung als Trauer-/Integrationssitzung kommt vom Therapeuten).
- `build_system_prompt`: dreistufige Quellentreue-Feststellung fuer alle
  Workflows ausser `anamnese`/`befund` (D11): nichts belegt → neutrales
  Glossar wie bisher; Verfahren woertlich in Transkript/Unterlagen/
  **Stichpunkten** → Whitelist „In den Quellen … genannt: IRRT. Benenne
  AUSSCHLIESSLICH dieses Verfahren"; `parts_work` (IFS, Ego-State,
  Schematherapie) → volles Glossar + Whitelist. Phasenblock
  `VERFAHRENSSTRUKTUR …` nur in P1. Anamnese behaelt
  `source_mentions_parts_work`/`PARTS_WORK_STEMS` unveraendert.
- `generation_pipeline`: `_glossar_source` += `ctx.bullets`; erkannte
  Verfahren in `st._verfahren`, Result-Key `verfahren_keys`;
  `build_user_content(verfahren_labels=…)` nennt das Verfahren in der
  Sandwich-Erinnerung.
- Stage-1 (`transcript_summary.summarize_transcript`, `stage1.
  _run_transcript_stage1`): neue optionale Parameter `fokus_themen`,
  `verfahren` → Block „SCHWERPUNKTE DES THERAPEUTEN (verbindlich fuer die
  Gewichtung)" + „ANGEWENDETES VERFAHREN" im System-Prompt. Ohne beide ist
  der Prompt byte-identisch (Snapshot `test_v1921_r7` unveraendert).

### Teil A — Stage-1-Audit sichtbar (D1=A, D2=B)
- `run_job`: `transcript_summary_audit` (bisher verworfen), `verfahren_keys`
  und `fokus_themen` landen in `generation_telemetry` (`transcript_stage1`,
  `verfahren`, `fokus_themen`) — persistiert, keine Migration. Vorab
  gesetzte Telemetrie (Repair-Job) wird gemergt statt ueberschrieben.
- QC `stage1_audit` (Transkript + Verlauf): `VERDICHTUNG_DEGRADED`
  (warning), `VERDICHTUNG_HALLUZINATION` (warning, critical bei ICD),
  `VERDICHTUNG_FALLBACK` (info, nur bei `exception:`). Still bei sauberem
  Lauf und Skip wegen Kuerze.

### Teil B — P1-Struktur (`services/quality_check_doku.py`, D3–D5)
- `doku_length_below_target`: `LENGTH_BELOW_TARGET` bei 75–149 Woertern (D3=A).
- `doku_struktur` (nur dokumentation): `ORGANISATORISCHES_PLATZHALTER`
  (warning, D4=A), `EINLADUNG_GENERISCH` (warning, nur mit Quelle),
  `EINLADUNG_FALLBACK` (info), `DOKU_LISTENFORMAT` (warning),
  `ABSCHNITT_DUENN` (info; Einladungen/Organisatorisches ausgenommen, D5=B).
  Abschnitts-Parser `doku_sections()`.

### Teil C — Fokus-Treue (D6=A, D7=A, D13=A)
- `quality_specs`: `stichpunkt_akronyme()` (Grossbuchstaben ≥ 3 sind immer
  Pflicht-Terme), Token-Regex mit Ziffern/Akzenten, Fuellwort-Fallback nur
  wenn kein Fuellwort (sonst „unpruefbar", kein Issue),
  `stichpunkt_coverage()` (Akronyme/Komposita ≥ 10 zaehlen doppelt).
  `stichpunkt_present` = Quote ≥ 0,5 UND kein fehlendes Akronym; ≤ 2 Terme:
  alle. Kein Komma-Split.
- QC `stichpunkte`: `MISSING_STICHPUNKT` mit `code_detail.fehlend/hits/
  total/akronym_fehlt`; neu `STICHPUNKTE_IGNORIERT` (critical) ab ≥ 2
  Punkten und ≥ 50 % fehlend; Zusatz „(Transkript wurde … verdichtet)" wenn
  Stage-1 lief.
- QC `verfahren`: `VERFAHREN_NICHT_BENANNT` (warning), `VERFAHREN_PHASE_FEHLT`
  (info, nur P1, reine Marker-Beobachtung).
- `_qc_fidelity_source` += Fokus-Themen (korrekt uebernommenes „IRRT" ist
  keine Erfindung); `_job_fokus_themen()` mit Telemetrie-Fallback fuer Jobs/
  Repairs nach Pod-Neustart; `jobs.repair_execute` erbt `fokus_themen` und
  `verfahren` aus der Parent-Telemetrie.
- Registry: 34 Regeln (+ `stage1_audit`, `doku_length_below_target`,
  `doku_struktur`, `verfahren`); `serialize_issues` traegt
  `summary.checks_run` (additiv, Schema-Version 1).

### Frontend (D8)
- `qa.jsx`: leere Issue-Liste → gruener Status „Interne Qualitaetspruefung —
  alle n Checks bestanden" (ohne Repair-Formular; ohne `checks_run`: „keine
  Beanstandungen"); `QcFehlendDetail` listet `code_detail.fehlend`.
  Styles `.qc-ok`, `.qc-fehlend`.

### Kalibrierung
- Regressionsfall `89276fb6` (echter Output): vorher 0 Issues, jetzt
  `MISSING_STICHPUNKT` (fehlend: irrt, traumasitzung; akronym_fehlt) +
  `VERFAHREN_NICHT_BENANNT`. Der zweite Job im vorliegenden Log
  (`5a3425f9`, Anamnese) bleibt bei 0 neuen Codes. Breitere Kalibrierung
  ueber weitere `prompts.log`-Runden steht aus.

### Tests
- Neu: `test_v1927_verfahren.py` (19), `test_v1927_fokus.py` (22),
  `test_v1927_doku_qc.py` (23), `frontend/tests/qc_status.test.jsx` (5).
  `test_v1921_qc_registry.py` (Reihenfolge) angepasst. Unit-Suite 1360
  passed; Jest 103 passed; ESLint 0 Fehler; ruff sauber.

---

## [v19.26b] — Vorher/Nachher im QualityCheck-Panel (2026-09-18)

Kleine Ergaenzung zu v19.26 (Backend + Frontend, `systelios.js` neu gebaut).

- `qa.jsx`: `QcPairsDetail` – QC-Eintraege mit `code_detail.pairs` bekommen ein
  aufklappbares „Vorher/Nachher anzeigen (n)“: alter Satz durchgestrichen,
  neuer Satz gruen, Kennzeichnung Modell / deterministisch / abgelehnt
  (`after == null` → „belassen“). Styles `.qc-pairs*` in `styles.jsx`.
- `postprocessing.fix_herrn_deklination(text, pairs=…)`: sammelt
  Vorher/Nachher-Schnipsel (Satzkontext); `postprocess_output(stats)` legt sie
  als `stats["pairs"]` ab → Telemetrie `grammar_fixes.pairs` →
  `GRAMMAR_AUTOFIXED.code_detail.pairs` (gleiche Anzeige wie `DIAGNOSE_ENTFERNT`).
- Tests: `tests/qc_pairs.test.jsx` (Jest), `test_v1925_grammatik.py` angepasst.

---

## [v19.26] — Zirkuläre Diagnose-Erklärung in der Anamnese (2026-09-18)

Basis: `v19_QA_v02` @ `052221c` + v19.25. Ein Patch (nur Backend; kein
Frontend-Rebuild noetig). Regressionsfall prompts.log.2026-09-08, Job
`cae59639`: „Hinzu kommen Flashbacks und ein starkes Grübeln **im Rahmen
einer Posttraumatischen Belastungsstörung (PTBS) und einer rezidivierenden
depressiven Störung**.“ – die Diagnose erklaert das Symptom, das sie
begruenden soll. Die A3a-Regel (v19.19) griff nicht sicher, der QC kannte
weder „PTBS“ noch „Depression“.

### Ebene 1 — Prompt (A3c, `prompts.py`)
- Verbotene Erklaerungsrahmen als Negativbeispiele („im Rahmen einer“, „vor
  dem Hintergrund“, „aufgrund ihrer“, „bedingt durch“, „infolge“, „als
  Ausdruck der“, „typisch für“ + Diagnose), Positivbeispiel (Beschwerde mit
  Verlauf/Ausloeser/Beeintraechtigung), Vordiagnosen-Regel (nur attribuiert,
  Vergangenheitsform, im Kontext Vorbehandlungen). Ausserhalb
  `WORKFLOW_INSTRUCTIONS_DEFAULT` → `prompt-defaults.jsx` unveraendert.

### Ebene 2 — QC satzweise (`quality_check.py`)
- `_DX_LABEL_IN_TEXT_RE` erweitert: Abkuerzungen (PTBS, kPTBS, GAS, ADHS,
  BPS), Depression, Dysthymie, bipolar, Angst-/Zwangs-/Essstoerung,
  Traumafolgestoerung, Borderline, Alkoholabhaengigkeit. Lay-Begriff
  „Burnout“ bewusst nicht (Selbstbericht).
- `diagnose_sentences()`: je Satz `zirkulaer` (Erklaerungsrahmen
  `_DX_FRAME_RE`), `attribuiert` (`_DX_ATTRIBUTION_RE`: diagnostiziert,
  Vorbefund, laut, Verdacht auf, in Behandlung wegen, familiaer „bei seiner
  Mutter“; ICD-Codes nie) oder `nennung`.
- Neu `DIAGNOSE_ZIRKULAER` (**critical**), `DIAGNOSE_IM_TEXT` (warning) jetzt
  unabhaengig von uebergebenen Diagnosen; `diagnosekriterien` meldet nur noch
  die Kriterien-Abdeckung. Neu `DIAGNOSE_ENTFERNT` (info, Vorher/Nachher).
- Kalibrierung: `e55cda0c` (28.07.) → ZIRKULAER; `f67635dc` (Burnout / Verdacht
  auf ADHS / Depression der Mutter) → 0; `7d548664` (15.09.) → 0.

### Ebene 3 — satzgenaue Entdiagnostizierung (`services/diagnose_rewrite.py`)
- Nur `zirkulaer`-Saetze (max. 6) werden einzeln per Structured-Output-Call
  (`{"satz"}`, temperature 0.2, `workflow="anamnese_dx_rewrite"`) umformuliert.
- `verify_rewrite()`: kein Label, kein Rahmen, kein Meta-Text, Laenge 40–160 %,
  keine neuen Zahlen/Namen, ein Satz. Abgelehnt/Fehler → `strip_frame_clause()`
  (Erklaerungsklausel am Satzende streichen, Restsatz ≥ 5 Woerter) → sonst
  Original + kritisches Issue bleibt.
- `generation_pipeline` (P2, zwischen Anamnese- und Befund-Call): Telemetrie
  `generation_telemetry.dx_rewrite {sentences, replaced, kept, mode, pairs}`;
  prompts.log `CALL: anamnese_dx_rewrite` mit Vorher/Nachher; QC-Info
  `DIAGNOSE_ENTFERNT`. Nie eine Exception nach aussen.
- Entscheidungen: D1 Rewrite ja, D2 Fallback ja, D3 Vordiagnosen attribuiert
  erlaubt, D4 nur P2.

### QC-Registry
`… konjunktiv, diagnosekriterien, diagnose_nennung, diagnose_entfernt, repair_flags, …`

### Tests
Neu `test_v1926_diagnose_zirkulaer.py` (Klassifikation, QC, Prompt,
Verifikation, Fallback, Rewrite mit gemocktem LLM); `test_v1919_sprint_r.py`
angepasst (Rahmen-Satz ist jetzt ZIRKULAER). Unit 1290 gruen, ruff 0.

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

## [v19.31] — Dialog-Modus: das Modell fuehrt das Interview (2026-09-23)

Basis: `v19_QA_v02` @ `58aaeeb` + v19.24.1. Ein Patch (Backend + Frontend)
plus `backend/static/systelios.js`.

**Warum.** Statt Fragekarten ein Gespraech mit dem System, so wie man mit
einem Kollegen spricht: Der Behandler erzaehlt, das Modell fragt entlang
der Fragenliste nach - mit therapeutischem Hintergrundwissen, ohne etwas
hinzuzuerfinden. Fragekarten bleiben als zweite Form erhalten.

### Backend

- `services/llm_chat.py` (S1) - `generate_chat_stream()` ueber Ollama
  `/api/chat` mit `stream: true`; bei Structured Output wird nur das Feld
  `sage` als Delta gestreamt (`SageExtractor`, escape-sicher ueber
  Chunk-Grenzen), das JSON kommt am Ende geparst.
- `services/interview_chat.py` (S2) - Prompt (Auftrag, Regeln in Du-Form,
  Fragenliste mit Status je Punkt, Budget), `plan_turn()` mit Leitplanken
  (Suizidalitaets-Trigger -> Regie, Pflichtpunkte per Marker-Check, Budget
  je Thema/gesamt, Notbremse), `apply_turn()` (Checkliste monoton,
  `fertig` nur mit Pflichtpunkten, Redundanz-Erkennung), Verdichtung der
  Historie ab 20 Turns. `ChatConfig` fuer Experimente (G4).
- `POST /api/interview/chat/stream` (S3) - zustandsloser SSE-Turn
  (`delta`, `meta`, `done`, `error`); Historie kommt vom Frontend (D2=B);
  Prompt-Log unter `interview-<session>`.
- Gespraech als Quelle (S4) - `InterviewGespraech` + `parse_gespraech()`
  (422 ohne Behandler-Antworten oder ohne Aussage zur Selbstgefaehrdung),
  `render_gespraech()` ([Interviewer]/[Behandler]), `gespraech_plaintext()`
  = nur Behandler-Turns; Form-Feld `interview_gespraech` in `/jobs/generate`
  (nur eine Interview-Quelle je Job); `INTERVIEW_MODUS_REGELN`: Fragen des
  Interviewers sind keine Inhalte, Klient in der Doku nur als Kuerzel.
- `scripts/eval_interview_chat.py` (S6) - drei synthetische Behandler-
  Skripte gegen den Dialog (Turns, Nachfragen je Thema, Abdeckung,
  Redundanz, Einschmuggeln); braucht Ollama.
- Tests: `tests/unit/test_v1931_dialog_modus.py` (34).

### Frontend

- `src/interview-chat.jsx` - Chat-Panel: Sprechblasen, Push-to-talk, Enter
  sendet, gestreamter Interviewer-Satz wird sofort vorgelesen
  (`speech.sayStream`, satzweise), Checkliste am Rand (offen/unklar/
  abgedeckt), Abbrechen, „Abschliessen“ (Backend verweigert ohne
  Pflichtpunkte), „Noch etwas ergaenzen“, Feedback-Button.
- `src/speech.js` - `sayStream()` mit Satzsegmentierung (`splitSentences`,
  Abkuerzungen/Kuerzel bleiben zusammen) und Warteschlange.
- `src/panels/P1.jsx` - Quelle als zwei Kacheln „Aufzeichnung“ (Aufnahme/
  Datei/Text darunter) und „Interview“ (Form: Gespraech | Fragenkarten,
  je Nutzer gemerkt) statt vier Tabs; `quelle` bleibt der feine Wert fuer
  den Job (`chat` -> `interview_gespraech`).
- `api.js` - `interviewChatStream()` (POST + SSE-Reader, Start-on-Intent).
- Tests: `tests/interview-chat.test.jsx` (7).

### Bekannte Punkte

- v19.31.1: Sie->Du in den UI-Hilfetexten (P4 Fallformel-Hinweise; alle
  anderen Panels waren bereits ohne Anrede oder in Du-Form).
- Gemma4 als Gespraechsfuehrer ist ungeprueft; vor dem Alltag die Eval
  gegen die drei Skripte fahren und die Regeln (Ueberfragen, Einschmuggeln)
  nachziehen.

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

### v19.24.1 — Interview-Tab ohne laufenden Server nutzbar

- Fragen-Sets liegen als `INTERVIEW_SETS_DEFAULT` im Bundle
  (`export_prompt_defaults.py` generiert sie aus `interview_sets.py`; Sync-Test
  erweitert). `fetchInterviewSets()` faellt darauf zurueck, wenn der Server
  aus ist (`source: "bundle"`), und laedt bei `st-health-ok` vom Server nach.
- `warmupInterviewServer()` stoesst beim Oeffnen des Tabs still den
  Start-on-Intent an; Statuszeile im Tab („Server startet – …“, „Kein
  Server verfuegbar“, Nachtsperre). Tippen und „Interview starten“ gehen
  sofort; Diktat und Rueckfrage-Check warten ueber `_postEnsured` auf den
  Server.

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
