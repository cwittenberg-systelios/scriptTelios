# Changelog — scriptTelios

Alle nennenswerten Aenderungen am Backend werden hier festgehalten.

Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/);
das Projekt nutzt Sprint-Versionen (v18, v19, v19.1, …) statt SemVer-Patch-Counter.

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
