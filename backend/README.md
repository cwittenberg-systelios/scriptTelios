# scriptTelios – Backend

FastAPI-Backend für die KI-gestützte klinische Dokumentation der
**sysTelios Klinik für Psychosomatik und Psychotherapie**.

Alle Verarbeitungsschritte (Transkription, LLM-Generierung, OCR) laufen
ausschließlich lokal – keine Patientendaten verlassen den Server.

---

## Verzeichnisstruktur

```
backend/
├── app/
│   ├── main.py                  # FastAPI-App, CORS, Router
│   ├── core/
│   │   ├── config.py            # Alle Einstellungen (via .env)
│   │   ├── database.py          # SQLAlchemy async (SQLite/PostgreSQL)
│   │   ├── files.py             # Upload-Validierung, Dateiverwaltung
│   │   └── logging.py           # Logging-Konfiguration
│   ├── api/
│   │   ├── health.py            # GET  /api/health
│   │   ├── transcribe.py        # POST /api/transcribe
│   │   ├── generate.py          # POST /api/generate[/with-files]
│   │   ├── documents.py         # POST /api/documents/fill|style|extract
│   │   ├── jobs.py              # POST /api/jobs/generate
│   │   │                        # GET  /api/jobs/{job_id}
│   │   └── style_embeddings.py  # POST /api/style/upload
│   │                            # GET  /api/style/{therapeut_id}
│   ├── models/
│   │   ├── db.py                # SQLAlchemy-Tabellen
│   │   └── schemas.py           # Pydantic Request/Response-Schemas
│   └── services/
│       ├── transcription.py     # faster-whisper (lokal, CUDA/CPU)
│       │                        # Automatisches Chunking für lange Aufnahmen
│       │                        # Sprecher-Heuristik ([A]/[B]-Markierung)
│       ├── llm.py               # Ollama (lokal)
│       ├── extraction.py        # PDF/DOCX/Bild → Text (pdfplumber + OCR)
│       ├── docx_fill.py         # DOCX-Vorlage befüllen
│       ├── embeddings.py        # pgvector Stilprofil-Retrieval
│       ├── job_queue.py         # Asynchrone Job-Queue (In-Memory)
│       └── prompts.py           # System-Prompts für alle 4 Workflows
├── tests/
│   ├── conftest.py              # Fixtures, Mocks, Test-DB
│   ├── test_suite.py            # Haupt-Testsuite (81+ Tests)
│   ├── test_api.py              # API-Integrationstests
│   ├── test_extraction.py       # Extraktions-Unit-Tests
│   ├── test_services.py         # Service-Unit-Tests
│   └── fixtures/                # Testdaten (Audio, PDF, DOCX, TXT)
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── runpod-start.sh              # Start-Script für RunPod (ohne Docker)
└── requirements.txt
```

---

## Server starten

### Entwicklung (lokal)

```bash
# 1. Virtualenv anlegen und aktivieren
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Abhängigkeiten installieren
pip install -r requirements.txt

# 3. Konfiguration anlegen
cp .env.example .env
# .env anpassen (CONFLUENCE_URL, Whisper-Modell, ...)

# 4. Server starten (mit Auto-Reload)
uvicorn app.main:app --reload --port 8000
```

Interaktive API-Dokumentation: **http://localhost:8000/docs**

### Docker (Entwicklung / Produktion)

```bash
cp .env.example .env             # anpassen

docker compose up -d             # Backend + Ollama + PostgreSQL starten

# Ollama-Modelle einmalig laden:
docker compose exec ollama ollama pull mistral-nemo       # LLM (~7.5 GB)
docker compose exec ollama ollama pull nomic-embed-text   # Embeddings (~0.3 GB)
docker compose exec ollama ollama pull llava              # OCR Vision-Fallback (~4.5 GB)

curl http://localhost:8000/api/health                     # Statuscheck
```

### RunPod (ohne Docker)

```bash
bash /workspace/scriptTelios/backend/runpod-start.sh
```

Das Skript installiert alle Systemabhängigkeiten, startet PostgreSQL und
Ollama, lädt die benötigten Modelle, baut das Frontend und startet das
Backend. Die öffentliche Backend-URL (Cloudflare-Tunnel) wird am Ende
im Terminal angezeigt und ins Confluence-Frontend eingetragen.

**Nach einem Neustart des Pods** muss das Skript erneut ausgeführt werden.
Die Tunnel-URL ändert sich dabei – im Frontend unter ⚙ Backend-URL anpassen.

---

## Tests ausführen

```bash
# Alle Tests
pytest -v

# Nur eine Testsuite
pytest tests/test_suite.py -v
pytest tests/test_services.py -v

# Bestimmte Klassen oder Tests
pytest tests/test_suite.py -v -k "workflow"
pytest tests/test_suite.py -v -k "TestJobQueue"

# Mit Coverage-Auswertung (pytest-cov muss installiert sein)
pip install pytest-cov
pytest --cov=app --cov-report=term-missing --cov-fail-under=70

# Kurze Tracebacks
pytest -v --tb=short
```

### Tests mit echten Dateien

Drei Tests benötigen echte Patientendateien (aus Datenschutzgründen
nicht im Repository) und werden ohne sie automatisch übersprungen:

| Test | Benötigte Datei |
|------|----------------|
| `test_echte_audio_transkription` | `tests/fixtures/audio/gespraech_real.mp3` |
| `test_echte_selbstauskunft_handschrift` | `tests/fixtures/pdf/selbstauskunft_handschrift.pdf` |
| `test_echter_entlassbericht_stilextraktion` | `tests/fixtures/docx/entlassbericht_real.docx` |

Dateien einfach an den jeweiligen Pfad kopieren – die Tests laufen dann
automatisch mit.

---

## Logging

Das Backend schreibt in zwei separate Logfiles:

### Hauptlog (`systelios.log` / `backend.log`)

Alle Laufzeit-Ereignisse: Server-Start, Job-Erstellung, Transkriptions-
fortschritt, Chunking, Fehler.

```bash
# Live mitlesen
tail -f /workspace/backend.log

# Nur Fehler und Warnungen
grep -E "ERROR|WARNING" /workspace/backend.log

# Transkriptions-Fortschritt eines Jobs verfolgen
grep "transcription\|Chunk\|Whisper" /workspace/backend.log

# Alle abgeschlossenen Jobs
grep "Job abgeschlossen" /workspace/backend.log
```

Beispielausgabe:
```
2026-03-20 08:15:10  INFO   app.services.transcription  Audio-Dauer: 4262.8s (71.0 Min)
2026-03-20 08:15:10  INFO   app.services.transcription  Lange Aufnahme – splitte in 10-Min-Chunks
2026-03-20 08:15:12  INFO   app.services.transcription  Audio aufgeteilt in 8 Chunks
2026-03-20 08:15:12  INFO   app.services.transcription  Whisper-Modell laden: large-v3 auf cuda (float16)
2026-03-20 08:15:14  INFO   app.services.transcription  Transkribiere Chunk 1/8 ...
2026-03-20 08:17:05  INFO   app.services.transcription  Transkript bereinigt: 52430 → 44180 Zeichen (15.7% reduziert)
2026-03-20 08:17:23  INFO   app.services.llm            Generierung: 412 Tokens in 18.3s (Modell: ollama/mistral-nemo)
2026-03-20 08:17:23  INFO   app.services.job_queue      Job abgeschlossen: abc123 (dokumentation) in 133.1s
```

### Performance-Log (`performance.log`)

Maschinenlesbares JSON – ein Eintrag pro abgeschlossenem Job.
Liegt im selben Verzeichnis wie das Hauptlog (Standard: `/workspace/`).

```bash
# Live mitlesen
tail -f /workspace/performance.log

# Letzten 20 Jobs übersichtlich anzeigen
tail -20 /workspace/performance.log | python3 -c "
import sys, json
for l in sys.stdin:
    j = json.loads(l)
    status = 'OK' if j['status'] == 'done' else 'ERR'
    print(f\"{j['ts'][:16]}  {status}  {j['workflow']:20}  {j['duration_s']:6.1f}s  queue:{j['queue_size']}\")
"

# Durchschnittliche Dauer pro Workflow
cat /workspace/performance.log | python3 -c "
import sys, json
from collections import defaultdict
times = defaultdict(list)
for l in sys.stdin:
    j = json.loads(l)
    if j['status'] == 'done' and j['duration_s']:
        times[j['workflow']].append(j['duration_s'])
for wf, durations in sorted(times.items()):
    print(f'{wf:25}  Ø {sum(durations)/len(durations):.1f}s  (n={len(durations)})')
"

# Fehlerhafte Jobs anzeigen
grep '"status": "error"' /workspace/performance.log | python3 -c "
import sys, json
for l in sys.stdin:
    j = json.loads(l)
    print(f\"{j['ts'][:16]}  {j['workflow']:20}  {j['error']}\")
"
```

Beispiel-Eintrag:
```json
{
  "ts": "2026-03-20T08:17:23+00:00",
  "job_id": "abc123def456",
  "workflow": "dokumentation",
  "status": "done",
  "duration_s": 133.1,
  "queue_size": 0,
  "model": "ollama/mistral-nemo",
  "error": null
}
```

| Feld | Beschreibung |
|------|-------------|
| `ts` | Abschluss-Zeitstempel (ISO 8601, UTC) |
| `job_id` | Eindeutige Job-ID (32 Zeichen Hex) |
| `workflow` | `dokumentation` / `anamnese` / `verlaengerung` / `entlassbericht` |
| `status` | `done` oder `error` |
| `duration_s` | Gesamtdauer in Sekunden (inkl. Transkription + Generierung) |
| `queue_size` | Noch laufende/wartende Jobs zum Abschlusszeitpunkt |
| `model` | Verwendetes LLM-Modell |
| `error` | Fehlermeldung bei `status=error`, sonst `null` |

---

## API-Endpunkte

| Method | Pfad | Beschreibung |
|--------|------|-------------|
| GET | `/api/health` | Systemstatus, aktive Modelle |
| POST | `/api/transcribe` | Audio → Transkript (Whisper) |
| POST | `/api/generate` | Text generieren (JSON, kein Upload) |
| POST | `/api/generate/with-files` | Text generieren + Datei-Uploads |
| POST | `/api/jobs/generate` | Job asynchron starten, gibt sofort `job_id` zurück |
| GET | `/api/jobs/{job_id}` | Job-Status abfragen (Frontend pollt alle 2s) |
| GET | `/api/jobs` | Alle Jobs auflisten (max. 50) |
| POST | `/api/documents/fill` | DOCX-Vorlage mit generiertem Text befüllen |
| POST | `/api/documents/style` | Stilprofil aus Beispieltext extrahieren und speichern |
| GET | `/api/documents/download/{fn}` | Befülltes DOCX herunterladen |
| POST | `/api/style/upload` | Stilprofil-Beispiel hochladen (pgvector) |
| GET | `/api/style/{therapeut_id}` | Stilprofil-Bibliothek eines Therapeuten abrufen |

---

## Konfiguration (`.env`)

| Variable | Standard | Beschreibung |
|----------|----------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama-Server-URL |
| `OLLAMA_MODEL` | `mistral-nemo` | LLM-Modell (beliebiges Ollama-Modell) |
| `WHISPER_MODEL` | `large-v3` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `WHISPER_DEVICE` | `cuda` | `cpu` oder `cuda` |
| `WHISPER_COMPUTE_TYPE` | `float16` | `int8` (CPU) / `float16` (GPU) |
| `DATABASE_URL` | PostgreSQL | SQLite für Entwicklung, PostgreSQL für Produktion |
| `CONFLUENCE_URL` | – | Intranet-URL für CORS, z.B. `http://intranet.systelios.local` |
| `ALLOW_CLOUDFLARE_TUNNEL` | `false` | `true` für Testphase via `cloudflared` |
| `ALLOW_RUNPOD_PROXY` | `false` | `true` für Testphase via RunPod-Proxy |
| `DELETE_AUDIO_AFTER_TRANSCRIPTION` | `true` | Audiodateien nach Transkription löschen (DSGVO) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FILE` | `systelios.log` | Pfad zur Haupt-Logdatei |

---

## Datenschutz

- Alle Modelle laufen lokal (Ollama + faster-whisper) – keine Daten verlassen den Server
- Audiodateien werden nach Transkription automatisch gelöscht (`DELETE_AUDIO_AFTER_TRANSCRIPTION=true`)
- Uploads erhalten UUID-basierte Namen (keine Originalfilenames im Dateisystem)
- Alle Dateipfade werden per Whitelist validiert (kein Path-Traversal möglich)
- Testphase: ausschließlich anonymisierte/synthetische Daten verwenden
