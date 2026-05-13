# DSGVO-Hinweis: Stage-1-Audit der Verlauf-Verdichtung (v19.2)

> **Scope**: Datenschutz-relevante Aspekte der Two-Stage-Pipeline.
> Technische Architektur: siehe `docs/architecture/two_stage_pipeline.md`.

---

## Kurzfassung

Mit v19.2 verarbeitet scriptTelios klinische Verlaufsdokumentationen in
einer zusätzlichen Zwischenstufe (Stage 1), die eine quellentreue
Zusammenfassung erzeugt. **Die Verarbeitung bleibt vollständig
on-premise** — kein Datenverlassen, keine zusätzlichen externen
Dienste. Es entstehen aber zwei neue persistente Datenartefakte pro Job:

1. `jobs.verlauf_summary_text` (TEXT) — die verdichtete Zusammenfassung
2. `jobs.verlauf_summary_audit` (JSONB) — Metadaten der Stage-1-Ausführung

Beide enthalten **keine Roh-Patientendaten** zusätzlich zu dem was vor
v19.2 schon in der DB lag, aber sie verlängern den Audit-Trail. Dieses
Dokument beschreibt was, wo und wie lange.

---

## Was wird gespeichert?

### `verlauf_summary_text`

**Inhalt.** Ein verdichteter Text in vier Sections (Sitzungsübersicht,
Bearbeitete Themen, Therapeutische Interventionen, Beobachtete
Entwicklung). Wortzahl ≈ `STAGE1_TARGET_WORDS` (Default 4 000).

**Datenklasse.** Klinische Behandlungsdaten — gleiche Schutzklasse wie
`jobs.result_transcript` und `jobs.result_text`. Enthält:

- Sitzungsdaten (Datum, Datum-Bezüge wie "12.01.")
- Therapeutische Interventionen (sofern namentlich in der Quelle)
- Aussagen zu Symptomen, Themen, Entwicklung — paraphrasiert aus der
  Quelle, nicht wörtlich

**Patient-Identifier.** Aktuell verwendet Stage 1 keinen direkten
Patientennamen (`patient_initial=None` zum Zeitpunkt des Calls, weil die
Name-Extraktion in `jobs.py` erst später läuft). Patient-Bezüge in der
Summary sind generisch ("die Patientin", "der Klient").

### `verlauf_summary_audit`

**Inhalt** (JSONB):

| Feld                  | Inhalt                                          | DSGVO-Relevanz                |
|-----------------------|-------------------------------------------------|-------------------------------|
| `applied`             | Boolean                                         | keine                         |
| `raw_word_count`      | Int                                             | keine                         |
| `summary_word_count`  | Int                                             | keine                         |
| `compression_ratio`   | Float                                           | keine                         |
| `duration_s`          | Float                                           | keine                         |
| `telemetry.*`         | LLM-Counters (think_ratio, eval_count, …)       | keine                         |
| `retry_used`          | Boolean                                         | keine                         |
| `degraded`            | Boolean                                         | keine                         |
| `issues[].type`       | Enum-Strings (`icd_halluzination`, …)           | keine                         |
| `issues[].severity`   | `critical` \| `high` \| `medium`                | keine                         |
| `issues[].detail`     | **freier Text, kann Quell-Fragmente enthalten** | ⚠ siehe unten                 |
| `fallback_reason`     | Strings wie `"verlauf_kurz_842w"`               | keine (nur Counter)           |
| `target_words`        | Int (Konfiguration)                             | keine                         |

**⚠ Achtung — `issues[].detail`.** Die Detail-Strings enthalten in einigen
Fällen Bruchteile von Quelle und Summary, z.B. *"Verfahren 'EMDR' in
Zusammenfassung aber nicht in Quelle"* oder *"ICD-Code 'F33.2' in
Zusammenfassung aber nicht in Quelle"*. Das sind keine Personenangaben,
aber **theoretisch könnten sie Hinweise auf den Patienten enthalten** wenn
sich der Detektor in Zukunft erweitert (z.B. um Diagnose-Listen-
Halluzinationen). Aktuell ist das nicht der Fall, aber Erweiterungen des
Detektors müssen das im Blick behalten.

---

## Wo werden die Daten gespeichert?

| Ort                              | Inhalt                          | Zugriff       |
|----------------------------------|---------------------------------|---------------|
| PostgreSQL, Tabelle `jobs`       | beide Spalten                   | `systelios_app`-User |
| `GET /api/jobs/{id}`             | beide Felder im JSON-Response   | Auth über CONFLUENCE_SHARED_SECRET |
| `/workspace/performance.log`     | `stage1`-Kompaktblock (Counter, keine Patientendaten) | Filesystem |
| `/workspace/systelios.log`       | INFO-Logs mit `raw_words→summary_words`-Counts | Filesystem |
| `/workspace/prompts.log`         | _nicht_ — Stage 1 loggt nicht in prompts.log | — |

**Was nicht persistiert wird:**

- Der genaue Stage-1-System-Prompt (statische Konstante in Code,
  nicht patientenbezogen)
- Der Stage-1-User-Prompt (enthält den Roh-Verlauf — bewusst nicht
  geloggt, der landet bereits in `verlaufsdoku_text`-Verarbeitung)
- Zwischenergebnisse des Retry-Pfads (nur das endgültige Result wird
  persistiert, der Retry-Text der nicht übernommen wurde verfällt)

---

## Wie lange werden die Daten aufbewahrt?

Die Stage-1-Daten unterliegen der **gleichen Retention-Policy wie der
Job selbst.** Aktuell:

- **In-Memory-Cache** (`JobQueue._cache`): max. 500 Jobs, älteste
  abgeschlossene Jobs werden verdrängt
- **PostgreSQL `jobs`-Tabelle**: nach aktuellem Stand keine
  automatische Löschung. Manuelle Bereinigung via SQL nötig wenn
  Klinikvorgaben das fordern
- **`performance.log`**: kein Auto-Rotate (Stand v19.2), wächst
  bis Operator es rotiert/löscht
- **Audio-Dateien**: 24h via `retention.py` (unverändert seit v18)

> **Aktion für die Klinik**: Wenn Klinikvorgaben Patientendatenlöschung
> nach z.B. 90 Tagen fordern, muss ein zusätzlicher Retention-Job für
> die `jobs`-Tabelle eingerichtet werden. Die zwei v19.2-Spalten
> verschlimmern das Problem nicht (sie sind kleiner als
> `result_transcript` und `result_text`), aber sie machen es nicht
> besser.

---

## Datenfluss vs. Pre-v19.2

| Schritt                        | Pre-v19.2                     | v19.2                                       |
|--------------------------------|-------------------------------|---------------------------------------------|
| PDF-Upload                     | → `uploads/` (24h-Retention)  | unverändert                                 |
| Text-Extraktion                | → in-memory                   | unverändert                                 |
| `clean_verlauf_text`           | in-memory                     | unverändert (aber additiv erweitert)        |
| **Stage 1 LLM-Call**           | —                             | **neu** — lokal an Ollama, on-premise       |
| Stage-1-Halluzinations-Check   | —                             | **neu** — in-memory Regex/Pattern-Matching  |
| Stage-1-Result                 | —                             | **neu** → `jobs.verlauf_summary_*` (DB)     |
| Stage 2 LLM-Call               | mit voller Verlaufsdoku       | mit verdichteter Summary statt Rohdoku      |

**Externe Datenflüsse.** Keine. Stage 1 nutzt denselben lokalen
Ollama-Endpoint wie Stage 2 — kein externer API-Call, keine
Telemetrie-Übertragung, keine Cloud-Dienste.

**Zusätzlicher Energieverbrauch.** Stage 1 ist ein zusätzlicher
LLM-Inference-Call (~15–30s GPU-Zeit pro Verlängerungsantrag mit großer
Verlaufsdoku). Das verdoppelt sich beim Retry. Operativ: pro Job mit
Stage 1 entstehen 1–2 zusätzliche Ollama-Inferences gegenüber dem
Pre-v19.2-Verhalten.

---

## Zugriff & Rollen

Die zwei neuen Spalten haben **keine eigene Zugriffsbeschränkung** —
sie folgen dem Zugriffsmodell der `jobs`-Tabelle:

- `systelios_app`-DB-User hat SELECT/INSERT/UPDATE auf die Spalten
  (gleicher Privilege-Level wie bisher)
- Im API-Layer schützt `Depends(get_current_user)` die Endpunkte
- Job-Sichtbarkeit pro Therapeut: derzeit über `therapeut_id` in der
  `jobs`-Tabelle — das gilt automatisch auch für die neuen Felder

Wenn die Klinik künftig granularer trennen will (z.B. Stage-1-Audit nur
für Admins sichtbar), muss eine Spalten-Level-Policy oder ein
separater Endpoint hinzu — aktuell nicht implementiert.

---

## Auswirkung auf das laufende DSGVO-Audit

Im Datenschutzaudit (`datenschutzaudit_scriptTelios_v2.pdf`) sind die
folgenden Punkte im Hinblick auf v19.2 zu prüfen / aktualisieren:

| Audit-Punkt                          | Status v19.2                                  |
|--------------------------------------|-----------------------------------------------|
| K1 — API-Authentifizierung           | unverändert; Stage 1 nutzt keinen neuen Endpoint |
| K2 — TLS in Produktion               | unverändert                                   |
| K3 — CORS-Hardening                  | unverändert                                   |
| E1 — Audit-Log                       | erweitert: Stage 1 schreibt in `systelios.log` und `performance.log` |
| E2 — Retention                       | **unklar — siehe oben**, Stage-1-Spalten brauchen die gleiche Behandlung wie bestehende Job-Felder |
| O1 — Rate-Limit                      | unverändert; Stage 1 läuft im selben Job, kein neuer Endpoint |
| Datenminimierung                     | **verbessert**: Stage 2 sieht jetzt nur die Summary statt der vollen Doku — weniger Roh-Patientendaten im LLM-Context |
| Zweckbindung                         | Stage 1 dient demselben Zweck wie die Antrags-Generierung — keine neue Zweckverwendung |

---

## Empfehlungen

1. **Retention-Policy für `jobs`-Tabelle** einrichten, falls noch nicht
   geschehen. Die neuen Spalten verstärken die Notwendigkeit nicht, aber
   sie sind ein guter Anlass das nachzuholen.
2. **Issue-Detail-Strings beobachten.** Wenn der
   Halluzinations-Detektor künftig erweitert wird, prüfen ob in
   `issues[].detail` patientenbezogene Fragmente landen könnten. Bei
   Bedarf Detail-Strings auf Counter reduzieren ohne Quell-Fragmente.
3. **performance.log rotieren.** Mit dem zusätzlichen `stage1`-Block
   wachsen die Einträge minimal — keine Größenordnung, aber ein Anlass
   eine Log-Rotation einzurichten falls noch nicht geschehen.
4. **Backup-Prüfung.** Falls PG-Backups gemacht werden, sind die
   zwei neuen Spalten automatisch mit dabei — eine Aktion ist nicht
   nötig, aber das DSGVO-Verzeichnis von Verarbeitungstätigkeiten sollte
   das vermerken.

---

## Datenschutz-Inventar (Δ gegenüber Pre-v19.2)

| Tabelle/Datei                   | Neu in v19.2?    | Beschreibung                                  |
|---------------------------------|------------------|-----------------------------------------------|
| `jobs.verlauf_summary_text`     | ✅ neu           | Verdichteter Verlauf (klin. Behandlungsdaten) |
| `jobs.verlauf_summary_audit`    | ✅ neu           | Metadaten der Stage-1-Ausführung              |
| `/workspace/performance.log`    | erweitert        | + `stage1`-Block pro Job                      |
| `/workspace/systelios.log`      | erweitert        | + Stage-1 INFO-Zeilen                         |
| Externe Datenflüsse             | unverändert      | weiterhin keine                               |
| Audio-Retention                 | unverändert      | weiterhin 24h via `retention.py`              |
| Auth-Modell                     | unverändert      | weiterhin HMAC + CONFLUENCE_SHARED_SECRET     |
