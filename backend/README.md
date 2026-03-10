# sysTelios KI-Dokumentation – Backend

FastAPI-Backend für die KI-gestützte klinische Dokumentation der
**sysTelios Klinik für Psychosomatik und Psychotherapie**.

---

## Architektur

```
backend/
├── app/
│   ├── main.py                  # FastAPI-App, CORS, Router-Einbindung
│   ├── core/
│   │   ├── config.py            # Alle Einstellungen (via .env)
│   │   ├── database.py          # SQLAlchemy async (SQLite/PostgreSQL)
│   │   ├── files.py             # Upload-Validierung, Dateiverwaltung
│   │   └── logging.py           # Logging-Konfiguration
│   ├── api/
│   │   ├── health.py            # GET  /api/health
│   │   ├── transcribe.py        # POST /api/transcribe
│   │   ├── generate.py          # POST /api/generate[/with-files]
│   │   └── documents.py         # POST /api/documents/fill
│   │                            # POST /api/documents/style
│   │                            # GET  /api/documents/download/{name}
│   ├── models/
│   │   ├── db.py                # SQLAlchemy-Tabellen
│   │   └── schemas.py           # Pydantic Request/Response-Schemas
│   └── services/
│       ├── transcription.py     # faster-whisper / OpenAI Whisper API
│       ├── llm.py               # Ollama / Anthropic Claude API
│       ├── extraction.py        # PDF, DOCX, Bild → Text + Stilprofil
│       ├── docx_fill.py         # DOCX-Vorlage befüllen
│       └── prompts.py           # System-Prompts für alle 4 Workflows
├── tests/
│   └── test_api.py
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## API-Endpunkte

| Method | Pfad                           | Beschreibung                              |
|--------|--------------------------------|-------------------------------------------|
| GET    | `/api/health`                  | Systemstatus, aktive Backends             |
| POST   | `/api/transcribe`              | Audio → Transkript (Whisper)              |
| POST   | `/api/generate`                | Text generieren (JSON, kein Upload)       |
| POST   | `/api/generate/with-files`     | Text generieren + Datei-Uploads           |
| POST   | `/api/documents/fill`          | DOCX-Vorlage befüllen (WF 3+4)           |
| POST   | `/api/documents/style`         | Stilprofil aus Beispieltext extrahieren   |
| GET    | `/api/documents/download/{fn}` | Befülltes DOCX herunterladen             |

Interaktive Dokumentation: **http://localhost:8000/docs**

---

## Workflows

### Workflow 1 – Gesprächsdokumentation
```
POST /api/generate/with-files
  audio:      <mp3/wav>   (optional)
  transcript: <text>      (alternativ/ergänzend)
  bullets:    <text>      (Stichpunkte, optional)
  style_file: <pdf/docx>  (Stilvorlage, optional)
  workflow:   dokumentation
  prompt:     <angepasster Prompt>
→ GenerateResponse { text, job_id, model_used, duration_seconds }
```

### Workflow 2 – Anamnese & Psychopathologischer Befund
```
POST /api/generate/with-files
  selbstauskunft: <pdf>    (Pflicht)
  vorbefunde:     <pdf>    (optional)
  audio:          <mp3>    (Aufnahmegespräch, optional)
  diagnosen:      "F32.1,F41.1"
  workflow:       anamnese
→ GenerateResponse
```

### Workflow 3 – Verlängerungsantrag
```
POST /api/documents/fill
  template:  <docx>   (Pflicht – Antragsvorlage)
  verlauf:   <pdf>    (Pflicht – Verlaufsdokumentation)
  style_file: <pdf>   (optional)
  workflow:  verlaengerung
→ DocProcessResponse { download_url, preview_text }
```

### Workflow 4 – Entlassbericht
```
POST /api/documents/fill
  template:  <docx>   (Pflicht – Berichtsvorlage)
  verlauf:   <pdf>    (Pflicht – gesamte Verlaufsdokumentation)
  workflow:  entlassbericht
→ DocProcessResponse { download_url, preview_text }
```

---

## Schnellstart (Entwicklung)

```bash
# 1. Repository klonen
git clone <repo>
cd backend

# 2. Virtualenv erstellen
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. Konfiguration anlegen
cp .env.example .env
# .env anpassen (API-Keys, Modellauswahl, ...)

# 5. Server starten
uvicorn app.main:app --reload --port 8000
```

---

## Schnellstart (Docker)

```bash
# .env anlegen
cp .env.example .env

# Stack starten (Backend + Ollama)
docker compose up -d

# Ollama-Modell herunterladen (einmalig, ~4 GB)
docker compose exec ollama ollama pull llama3.2

# Status prüfen
curl http://localhost:8000/api/health
```

---

## Konfiguration (`.env`)

| Variable                            | Standard         | Beschreibung                                  |
|-------------------------------------|------------------|-----------------------------------------------|
| `LLM_BACKEND`                       | `ollama`         | `ollama` (Produktion) / `anthropic` (Test)    |
| `OLLAMA_MODEL`                      | `llama3.2`       | Beliebiges Ollama-Modell                      |
| `ANTHROPIC_API_KEY`                 | –                | Nur für `LLM_BACKEND=anthropic`               |
| `WHISPER_BACKEND`                   | `local`          | `local` / `openai`                            |
| `WHISPER_MODEL`                     | `medium`         | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `WHISPER_DEVICE`                    | `cpu`            | `cpu` / `cuda`                                |
| `DELETE_AUDIO_AFTER_TRANSCRIPTION`  | `true`           | Audiodateien nach Transkription löschen       |
| `DATABASE_URL`                      | SQLite           | SQLite (Dev) / PostgreSQL+asyncpg (Prod)      |

---

## Testphase (Cloud)

Für die Testphase auf Hetzner VPS oder RunPod:

```bash
# .env anpassen:
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
WHISPER_BACKEND=openai       # oder local
OPENAI_API_KEY=sk-...

# WICHTIG: Nur anonymisierte/synthetische Testdaten verwenden!
```

---

## Tests ausführen

```bash
pytest -v
```

---

## Datenschutz & Sicherheit

- **Alle Dateipfade** werden per Whitelist validiert (kein Path-Traversal möglich)
- **Audiodateien** werden nach Transkription automatisch gelöscht (`DELETE_AUDIO_AFTER_TRANSCRIPTION=true`)
- **Uploads** erhalten UUID-basierte Namen (keine Originalfilenames im Dateisystem)
- **Produktion**: Alle Modelle laufen lokal (Ollama + faster-whisper) – keine Patientendaten verlassen das Kliniknetz
- **Testphase**: Ausschliesslich anonymisierte/synthetische Daten verwenden

---

## Skalierungspfad

| Stufe | Hardware        | Modell             | Transkription  | Durchsatz |
|-------|-----------------|--------------------|----------------|-----------|
| 1     | CPU-only        | Llama 3.2 7B       | Whisper medium | ~3 min/Job|
| 2     | RTX 4070 Ti     | Llama 3.1 8B       | Whisper large  | ~30 sek   |
| 3     | 2× RTX 4090     | Llama 3.1 70B q4   | Whisper large  | ~15 sek   |
