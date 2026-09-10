# Changelog — scriptTelios

Alle nennenswerten Aenderungen am Backend werden hier festgehalten.

Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/);
das Projekt nutzt Sprint-Versionen (v18, v19, v19.1, …) statt SemVer-Patch-Counter.

---

## [v19.21] — Refactoring-Sprint (Code-Review 2026-09-09)

Basis: `v19_QA_v02` @ `b89f6d8`. Schritte als Einzelpatches (S2, S3, …),
jeweils ohne Verhaltensaenderung fuer Therapeut:innen.

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
