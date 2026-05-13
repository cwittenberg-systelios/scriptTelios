# Two-Stage-Pipeline (v19.2) — Architektur

> **Scope**: technische Architektur-Dokumentation der Verlauf-Verdichtung
> (Stage 1) und ihrer Integration in den bestehenden Job-Workflow.
> Datenschutz-Aspekte: siehe `docs/dsgvo/verlauf_summary_audit.md`.

---

## Motivation

scriptTelios generiert klinische Antrags-Dokumente (Verlängerungsanträge,
Entlassberichte) aus Verlaufsdokumentationen. Bei realen Behandlungen ist
die Roh-Verlaufsdoku ca. 10–13 000 Wörter lang (50–80 Sitzungen über
mehrere Wochen). Direkt in den Antrags-Prompt geschoben führte das zu:

1. **VRAM-Druck**: Inputs > 17 000 Tokens trafen das KV-Cache-Limit auf
   der RTX Pro 4500 (32 GB) trotz `OLLAMA_KV_CACHE_TYPE=q8_0`. Der
   bestehende OOM-Fallback (Retry mit `num_ctx=8192`) brach in dem Fall
   die Generierungsqualität deutlich ein.
2. **Strukturarmut**: Qwen3 wurde mit der Detailfülle überfordert,
   verlor sich in Listen statt strukturierten Antrags-Abschnitten und
   neigte zu Wiederholungen.
3. **Halluzinationen**: ohne dedizierten Anti-Halluzinations-Pass
   verschmolzen Quellenangaben unkontrolliert mit Modell-Wissen
   (erfundene Therapieverfahren, ICD-Codes, Patienten-Zitate).

Stage 1 löst alle drei Probleme: sie verdichtet auf ~4 000 Wörter (Faktor
2.5–3 Kompression), erzwingt eine vorgegebene 4-Section-Struktur und
prüft den Output gegen die Quelle auf typische Halluzinations-Muster.

---

## Pipeline-Ablauf

```
┌────────────────────────────────────────────────────────────────────┐
│ jobs.py::_run (FastAPI background_task)                            │
│                                                                    │
│  1. Audio-Transkription (faster-whisper, optional)                 │
│  2. Dokument-Extraktion (pdfplumber + OCR-Fallback)                │
│       └─ verlaufsdoku_text = clean_verlauf_text(extract_text(…))   │
│                                                                    │
│  ┌─────────── v19.2 Stage 1 ───────────────────────┐               │
│  │ if STAGE1_ENABLED                               │               │
│  │    and workflow ∈ {verl., folgev., entlassb.}  │               │
│  │    and len(verlaufsdoku_text.split()) ≥ 1500:   │               │
│  │                                                 │               │
│  │   stage1_result = await summarize_verlauf(      │               │
│  │       verlauf_text     = verlaufsdoku_text,     │               │
│  │       workflow         = workflow,              │               │
│  │       target_words     = STAGE1_TARGET_WORDS,   │               │
│  │   )                                             │               │
│  │                                                 │               │
│  │   verlaufsdoku_text  = stage1_result["summary"] │               │
│  │   _stage1_audit      = { applied: True, … }     │               │
│  │                                                 │               │
│  │ except RuntimeError:                            │               │
│  │   verlaufsdoku_text stays raw                   │               │
│  │   _stage1_audit = { applied: False, reason… }   │               │
│  └─────────────────────────────────────────────────┘               │
│                                                                    │
│  3. build_system_prompt(workflow, …)                               │
│  4. build_user_content(verlaufsdoku_text=…, …)                     │
│  5. await generate_text(…)        ← Stage 2: Antrags-Generierung   │
│                                                                    │
│  6. return { text, …, verlauf_summary_text, verlauf_summary_audit }│
└────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────────────────────────┐
│ job_queue.py::run_job                                              │
│   state.verlauf_summary_text  = result["verlauf_summary_text"]     │
│   state.verlauf_summary_audit = result["verlauf_summary_audit"]    │
│       …                                                            │
│   await _persist_job(state)    → DB                                │
│   _log_performance(state)      → /workspace/performance.log        │
└────────────────────────────────────────────────────────────────────┘
```

---

## Stage 1: Verlauf-Verdichtung

### Prompts

**System-Prompt** = `VERLAUF_SUMMARY_SYSTEM_PROMPT` +
`VERLAUF_SUMMARY_STRUCTURE` + optional `FOCUS: …` je Workflow.

Die wichtigsten Anti-Halluzinations-Regeln:

1. Quellentreue — keine Aussagen die nicht im Quelltext stehen
2. Keine Interpretation, keine Wertung
3. Therapieverfahren nur übernehmen wenn **namentlich** in der Quelle
4. Unsicherheit explizit kennzeichnen ("im Protokoll unklar")
5. Sitzungsdatum oder Seitenzahl als Audit-Spur in Klammern

**Struktur-Prompt** schreibt vier `### `-Sections vor:

- `### Sitzungsübersicht` — Anzahl/Art/Zeitraum/Dichte
- `### Bearbeitete Themen` — chronologisch, mit Datums-Bezug
- `### Therapeutische Interventionen` — nur namentlich erwähnte Verfahren
- `### Beobachtete Entwicklung` — strikt nach Protokoll

**Focus-Hints** je Workflow:

| Workflow              | Fokus                                                       |
|-----------------------|-------------------------------------------------------------|
| `verlaengerung`       | aktuelle Belastung, offene Prozesse, Gruppendynamik         |
| `folgeverlaengerung`  | Veränderung seit letztem Antrag                             |
| `entlassbericht`      | Gesamtbogen Anfang → Wendepunkte → Ende                     |
| (alle anderen)        | _kein Focus-Hint, weil Stage 1 für sie nicht ausgelöst wird_|

### LLM-Parameter

| Parameter             | Wert                  | Begründung                                  |
|-----------------------|-----------------------|---------------------------------------------|
| `temperature`         | 0.2 (Retry: 0.1)      | Maximale Reproduktion, minimale Kreativität |
| `max_tokens`          | `target_words * 1.5`  | Token-Wort-Puffer für deutsche Texte        |
| `workflow`            | `None`                | Kein BASE_PROMPT, kein Primer, kein Style   |
| `temperature_override`| ✅                    | Stage-1-spezifisch, umgeht Profil-Default   |

### Plausibilität

Vor Halluzinations-Check prüft `summarize_verlauf()` die Ausgabe:

- Leerer Output → `RuntimeError`
- Wortzahl < 40 % des Targets → `RuntimeError` (Aufrufer fängt → Fallback
  auf Roh-Verlauf)
- Wortzahl > 200 % des Targets → Warning, aber Output wird genutzt

### Halluzinations-Detektion

`detect_summary_hallucination_signals(summary, source_text)` liefert eine
Liste von Issue-Dicts mit drei aktiv genutzten Schweregraden:

| Severity   | Issue-Type                  | Trigger                                                    |
|------------|-----------------------------|------------------------------------------------------------|
| `critical` | `icd_halluzination`         | ICD-Code (`F33.1`) in Summary, nicht in Quelle             |
| `high`     | `verfahren_halluzination`   | Bekanntes Therapieverfahren in Summary, nicht in Quelle    |
| `medium`   | `wortlaut_halluzination`    | Direkt-Zitat-Wendung (`sagte: "…"`) in Summary, nicht in Quelle |
| `medium`   | `anzahl_implausibel`        | "N Sitzungen" mit N > 3×Datums-Anker in Quelle             |

**Bekannte Therapieverfahren** (KNOWN_VERFAHREN, conservativ erweiterbar):
IFS, Anteilearbeit, Stuhlarbeit, EMDR, Schematherapie, Hypnose,
Hypnosystemik, DBT, Skills-Training, Achtsamkeit, MBSR, Imagination,
Trauma-Konfrontation.

### Retry-Logik

Wenn `critical`-Issues gefunden werden, läuft genau **ein** Retry:

```python
hard_system = system + (
    "\n\nWICHTIG: In einem vorherigen Versuch traten folgende "
    f"Halluzinations-Probleme auf: {issue_summary}. "
    "Vermeide diese diesmal strikt. Wenn du unsicher bist ob etwas in "
    "der Quelle steht, lass es weg."
)
result = await generate_text(
    system_prompt=hard_system,
    user_content=…,
    temperature_override=0.1,   # noch niedriger als Erst-Pass
)
```

Endergebnis:

- Retry-Output sauber + lang genug → übernehmen
- Retry-Output immer noch `critical` ODER zu kurz ODER leer → Original
  behalten, `degraded=True` setzen

Der Aufrufer (jobs.py-Pipeline) entscheidet anhand `degraded`, ob die
Summary trotzdem in Stage 2 fließt (aktuell: ja, mit Audit-Markierung).
Die Eval-Suite werted `degraded=True` als Hard-Fail.

---

## Datenfluss zur DB

```
                        result_dict
                            │
                            ▼
        ┌───────────────────┴────────────────────────┐
        │ verlauf_summary_text    verlauf_summary_audit│
        │ (TEXT)                  (JSONB)              │
        └───────────────────┬────────────────────────┘
                            ▼
                    JobState (RAM)
                            ▼
                  _persist_job(state)
                            ▼
              UPDATE jobs SET … WHERE id = …
                            ▼
              ┌─────────────┴─────────────┐
              │ Spalte                Typ │
              ├───────────────────────────┤
              │ verlauf_summary_text TEXT │
              │ verlauf_summary_audit JSONB│
              └───────────────────────────┘
```

### Audit-Bundle-Schema

```jsonc
{
  "applied":              true,        // Stage 1 ausgeführt?
  "raw_word_count":       12345,
  "summary_word_count":   3987,
  "compression_ratio":    0.32,        // = summary/raw
  "duration_s":           22.4,        // Stage-1-Wallclock
  "telemetry": {                       // direkt aus llm.generate_text
    "think_ratio":            0.05,
    "tokens_hit_cap":         false,
    "used_thinking_fallback": false,
    "eval_count":             4500
  },
  "retry_used":           false,       // Stage-1-Retry gelaufen?
  "retry_telemetry":      {},          // Telemetrie des Retry-Calls
  "degraded":             false,       // Beide Versuche scheiterten?
  "issues": [                          // Halluzinations-Signale (auch bei Erfolg)
    { "type": "verfahren_halluzination",
      "severity": "high",
      "detail": "Verfahren 'EMDR' in Zusammenfassung aber nicht in Quelle" }
  ],
  "target_words":         4000,        // Konfiguration zum Zeitpunkt
  "fallback_reason":      null         // Begründung wenn applied=false
}
```

### Mini-Audit für nicht-ausgelöste Stage 1

Wenn der Workflow auf der Whitelist steht aber Stage 1 nicht angesprungen
ist (zu kurzer Verlauf, Notabschalter), wird trotzdem ein Mini-Audit
geschrieben:

```jsonc
{
  "applied":            false,
  "raw_word_count":     842,
  "summary_word_count": null,
  "compression_ratio":  null,
  "duration_s":         null,
  "issues":             [],
  "fallback_reason":    "verlauf_kurz_842w"   // oder "stage1_disabled"
}
```

So sieht man im Performance-Log _warum_ Stage 1 nicht griff — wichtig
für die Eval-Auswertung und Produktions-Monitoring.

---

## Performance-Log-Format

`/workspace/performance.log` (ein JSON pro abgeschlossenem Job):

```jsonc
{
  "ts":           "2026-05-13T10:42:00+00:00",
  "job_id":       "abc123…",
  "workflow":     "verlaengerung",
  "status":       "done",
  "duration_s":   125.7,         // Gesamtdauer (Stage 1 + Stage 2)
  "model_used":   "ollama/qwen3:32b",
  "output_words": 567,
  "output_chars": 4123,
  "queue_size":   0,
  "telemetry": { "think_ratio": 0.0, "retry_used": false, "degraded": false, … },
  "stage1": {                    // ← v19.2 neu, nur wenn Stage 1 lief
    "applied":          true,
    "raw_words":        12345,
    "summary_words":    3987,
    "compression_ratio": 0.32,
    "duration_s":       22.4,
    "retry_used":       false,
    "degraded":         false,
    "issue_count":      0,
    "fallback_reason":  null
  }
}
```

Auswertung etwa:
```bash
# Wie oft hat Stage 1 angeschlagen?
jq -r 'select(.stage1.applied==true) | .job_id' /workspace/performance.log | wc -l

# Wie viele Jobs hatten Stage-1-Probleme?
jq -r 'select(.stage1.degraded==true)' /workspace/performance.log
```

---

## Workflow-Whitelist & Mindest-Wörter

| Konstante (jobs.py)   | Wert                                                |
|-----------------------|-----------------------------------------------------|
| `_STAGE1_WORKFLOWS`   | `{verlaengerung, folgeverlaengerung, entlassbericht}` |
| `_STAGE1_MIN_WORDS`   | `1500`                                              |

**Begründung Whitelist**: Anamnese, Akutantrag und Dokumentation
bekommen keine große Verlaufsdoku (oder gar keine) — Stage 1 wäre
überflüssiger Aufwand.

**Begründung Mindest-Wörter**: Unter 1 500 Wörter kann Stage 2 die Doku
ohnehin komplett verarbeiten; eine Verdichtung wäre reine Verlust-
komprimierung ohne Nutzen.

---

## Konfiguration

| Setting                  | Default | Beschreibung                                    |
|--------------------------|---------|-------------------------------------------------|
| `STAGE1_ENABLED`         | `true`  | Notabschalter, schaltet Stage 1 global ab       |
| `STAGE1_TARGET_WORDS`    | `4000`  | Zielwortzahl der Zusammenfassung                |

Beide in `.env` setzbar; werden über `Settings` (pydantic-settings)
ausgewertet.

---

## Eval-Integration

Der `test_eval.py`-CLI-Flag `--summary-mode` steuert die Eval-Erwartung:

| Wert                  | Verhalten                                                     |
|-----------------------|---------------------------------------------------------------|
| `auto` (default)      | Nimmt was das Backend liefert, nur reporten                   |
| `require_stage1`      | Fail wenn Whitelist-Workflow nicht `applied=true`             |
| `require_no_stage1`   | Fail wenn irgendein Workflow `applied=true`                   |

Anwendungsfall A/B-Vergleich:
```bash
# Pre-v19.2-Baseline (Backend: STAGE1_ENABLED=false)
pytest tests/test_eval.py --summary-mode=require_no_stage1 \
  --eval-output /workspace/eval_v192_off/

# v19.2-Variante (Backend: STAGE1_ENABLED=true)
pytest tests/test_eval.py --summary-mode=require_stage1 \
  --eval-output /workspace/eval_v192_on/

# Anschließend Reports vergleichen
```

`EvalResult` hat fünf neue Felder die im PDF-Report auftauchen:
`stage1_applied`, `stage1_compression_ratio`, `stage1_retry_used`,
`stage1_degraded`, `stage1_issue_count`. `stage1_degraded=true` setzt den
Score auf 0 (analog zu `generation_degraded`).

---

## Bekannte Einschränkungen

- **Verfahrens-Whitelist statisch.** `KNOWN_VERFAHREN` muss manuell
  gepflegt werden. Wenn ein neues Verfahren in der Klinik etabliert wird
  und im Source steht, der Detektor es aber noch nicht kennt, würde es
  ungeprüft durchgehen. Erweiterung: einfach den Tuple ergänzen.
- **ICD-Regex grob.** `[FZGH]\d{2}\.\d` matched ICD-10 dreistellig-mit-
  Dezimal. Vier-Dezimal-Codes (`F33.10`) werden auch erkannt; spezielle
  Sub-Codes (`F33.10 G`) nur grob. Falsch-Negative bei sehr exotischen
  ICD-Formaten sind möglich.
- **patient_initial in Stage 1.** Aktuell `None`, weil
  `extract_patient_name()` in `jobs.py` erst nach der Stage-1-Stelle
  läuft. Stage 1 nennt den Patienten in der Summary über generische
  Marker ("die Patientin", "der Klient"). Bei späterer Reorganisation
  könnte man das Initial vorziehen.
- **Stage 1 nutzt Workflow=None in generate_text.** Damit greift kein
  Workflow-spezifisches Postprocessing (`postprocess_output`). Das ist
  gewollt — Stage 1 ist nur Verdichtung, keine Antrags-Generierung.
- **Eval-Mode-Flag ist softer Switch.** Das Backend wird nicht
  umkonfiguriert; das Flag enforced nur die Erwartung in den Tests. Für
  echte A/B-Vergleiche muss zusätzlich `STAGE1_ENABLED` im Backend
  passend gesetzt werden.

---

## Migration & Rollback

**Schema-Migration** läuft beim Pod-Start automatisch via
`scripts/schema.sql` (Block C, `ADD COLUMN IF NOT EXISTS …`). Idempotent.

**Code-Rollback** (auf Pre-v19.2):
1. `STAGE1_ENABLED=false` in `.env`
2. Backend neu starten

Die zwei DB-Spalten bleiben — sie sind `NULL` für alle neuen Jobs und
beeinträchtigen nichts. Bei vollem Code-Rollback (Git revert) sollten die
Spalten manuell entfernt werden (oder schlicht bleiben — leere Spalten
sind harmlos).
