# Sprintplan — §6a-Retention: automatische 90-Tage-Löschung

**Base:** `v19_QA_v02` @ `b9deb0f` (frischer Clone, Remote-HEAD verifiziert unverändert)
**Anlass:** Einwilligung v1.1 §6a — Verarbeitungsdaten max. 90 Tage, danach Löschung/Anonymisierung. Backend-only, kein Frontend-Build.

## Ist-Analyse

| Datenbestand | Aktuelle Retention | §6a-konform? |
|---|---|---|
| Audio (recordings/uploads) | 24 h | ✓ |
| prompts.log (Transkripte/Prompts/Outputs) | Tagesrotation, 14 Archive | ✓ (14 < 90) — keine Änderung |
| audit.log / performance.log | 90 / 180 Tage (nur Metadaten, keine Inhalte) | ✓ |
| **feedback.log (Freitext!)** | **3650 Tage** | ✗ → 90 |
| **jobs (result_transcript, transcript_summary_text, Entwürfe, Telemetrie)** | **unbegrenzt** — RETENTION-Keys jobs_done/jobs_error existieren, aber kein Cleanup nutzt sie | ✗ → 90 |
| **recordings (transcript in DB, Soft-Delete-Reste)** | **unbegrenzt** (nur Audio-Datei wird nach 24 h entfernt) | ✗ → 90 |
| ISM (P6) | persistiert in jobs → durch Jobs-Cleanup abgedeckt | ✓ via S1 |
| style_embeddings | 1 Jahr Inaktivität (Stilwissen der Therapeut:innen, keine Patientendaten) | unverändert |

## Schritte

**S1 — retention.py:** Neuer RETENTION-Key `qa_artifacts = 90 Tage` (Kommentar: §6a Einwilligung v1.1). Zwei neue Cleanups: `cleanup_jobs_db()` (DELETE jobs mit `created_at < cutoff`) und `cleanup_recordings_db()` (Dateireste entfernen, dann DELETE recordings mit `created_at < cutoff`, inkl. Soft-Deleted). Beide in `retention_task()` aufnehmen.
Testbar: Zeilen älter 90 Tage verschwinden, jüngere bleiben.

**S2 — feedback.py:** `_FEEDBACK_LOG_BACKUP_DAYS` 3650 → 90; Docstring-Design-Entscheidung entsprechend fortschreiben (Auswertung muss innerhalb der §6a-Frist erfolgen oder anonymisiert exportiert werden).

**S3 — Tests:** Neue Integrationstests `test_retention_db.py` (SQLite-Fixture aus integration/conftest): alt/jung-Grenzfälle für Jobs und Recordings, Datei-Entfernung; Unit-Assert für Feedback-Konstante. Bestehende Suite als Regressionscheck.

**S4 — Auslieferung:** Patch (verifiziert gegen frischen Stand) + `__`-Drop-ins. Kein systelios.js (kein Frontend-Touch).

**Nicht im Sprint:** Anonymisierter Export vor Löschung (falls Auswertungsdaten >90 Tage gebraucht werden → eigener Sprint nach DSB-Klärung); UI-Hinweis auf 90-Tage-Verfall in der Entwurfsliste (optional, bei Bedarf).
