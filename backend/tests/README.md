# scriptTelios Backend Tests

Unit-Tests fuer kritische Backend-Logik. Diese Tests sind **schnell** (laufen in Sekunden) weil sie ohne LLM, ohne DB und ohne Whisper auskommen.

## Test-Files Übersicht

| File | Tests | Was wird getestet |
|------|-------|-------------------|
| `test_extraction_unit.py` | 31 | Patientennamen-Extraktion (Briefkopf, Selbstauskunft, Form-Feld) |
| `test_extraction_docx.py` | 8 | DOCX-Section-Extraktion (Heading-Style, Bold-Heading, Substring-Regression) |
| `test_prompts_unit.py` | 23 | `derive_word_limits`, `_compute_style_constraints`, Patient-Substitution |
| `test_llm_postprocessing.py` | 22 | `truncate_style_context`, `deduplicate_paragraphs`, `clean_verlauf_text` |
| `test_eval_logic.py` | 22 | `StyleAnalyzer`, `EvalResult.check_*` (inkl. Bug-Fix #3b) |
| `test_jobs_logic.py` | 13 | Akut-Cap (Bug #2), Patient-Name-Fallback (Bug #4) |
| `test_admin_endpoint.py` | 9 | Whisper-Modell-Switch via Admin-Endpoint |

**Total: 128 Tests**

## Bug-Fix-Coverage

Diese Tests adressieren spezifisch die Bugs aus dem Eval-Run vom 24.04.2026:

| Bug | Test | Datei |
|-----|------|-------|
| #1 Selbst-widersprueglicher Patient-Name-Verbots-Text | `test_kein_selbstwidersprueglicher_verbotstext` | `test_prompts_unit.py` |
| #2 Akutantrag-Wortlimit absurd hoch | `TestAkutCap` (7 Tests) | `test_jobs_logic.py` |
| #3 Absatzlaengen-Toleranz zu eng | `TestCheckStyleConsistencyDowngrade` | `test_eval_logic.py` |
| #4 `[Patient/in]` ueberlebt im Output | `TestPatientNameFallback` + `TestSubstitutionMitFallback` | `test_jobs_logic.py` |

Plus Regression-Test fuer den `AKUTAUFNAHME`-Substring-Bug (2026-04-22):
- `test_kurzes_briefkopf_heading_matcht_nicht_langes_workflow_heading` in `test_extraction_docx.py`

## Installation

```bash
cd /workspace/scriptTelios/backend
source /workspace/venv/bin/activate
pip install pytest python-docx httpx --break-system-packages
```

## Ausfuehrung

### Alle Unit-Tests
```bash
cd /workspace/scriptTelios/backend
pytest tests/test_extraction_unit.py \
       tests/test_extraction_docx.py \
       tests/test_prompts_unit.py \
       tests/test_llm_postprocessing.py \
       tests/test_eval_logic.py \
       tests/test_jobs_logic.py \
       tests/test_admin_endpoint.py \
       -v --tb=short
```

### Einzelne Datei
```bash
pytest tests/test_extraction_unit.py -v
```

### Nur ein Test-Class
```bash
pytest tests/test_jobs_logic.py::TestAkutCap -v
```

### Nur ein Test
```bash
pytest tests/test_jobs_logic.py::TestAkutCap::test_eval_run_szenario_422_783 -v
```

## Ausfuehrungs-Zeit

Erwartete Dauer fuer alle 128 Tests: **5-15 Sekunden** (kein LLM, kein I/O).

## Setup-Hinweise

### conftest.py
Falls noch keine `conftest.py` im `tests/` Ordner existiert, kopiere `conftest_unit.py` als `conftest.py`. Falls schon eine existiert (z.B. die fuer `test_eval.py`), die folgenden Zeilen ergaenzen:

```python
import sys
from pathlib import Path
_BACKEND_ROOT = Path(__file__).parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
```

### Abhaengigkeiten
- `pytest >= 7.0`
- `python-docx >= 0.8` (fuer `test_extraction_docx.py`)
- `httpx` (fuer `test_admin_endpoint.py` via FastAPI TestClient)
- `fastapi` (sollte im Backend bereits vorhanden sein)

### Tests ausfuehren ohne Backend zu starten
Diese Tests **brauchen kein laufendes Backend**. Sie testen reine Funktionen direkt aus den Modulen. Der Admin-Endpoint-Test nutzt `TestClient` (in-process), kein echter HTTP-Call.

## Was die Tests NICHT abdecken

Diese Tests sind **Unit-Tests** — sie testen einzelne Funktionen isoliert. Sie ersetzen NICHT:

- **`test_eval.py`** — End-to-End-Eval mit LLM-Calls (laeuft 30+ Min)
- **API-Integration-Tests** — Komplette Job-Lifecycle ueber HTTP
- **DB-Tests** — pgvector, Job-Queue, Style-Embeddings

Diese tieferen Tests bleiben in `test_eval.py` / `test_api.py` / `test_services.py`.

## Architektur-Notiz

Tests in `test_jobs_logic.py` duplizieren bewusst die Cap-/Fallback-Logik aus `jobs.py` als reine Helper-Funktionen. Das macht es testbar **ohne** den ganzen `_run`-Job-Lifecycle aufzurufen. Wenn `jobs.py` refactored wird (z.B. die Cap-Logik in einen eigenen Helper extrahiert), muss `test_jobs_logic.py` entsprechend geupdatet werden — die dort kopierten Funktionen sind die SPEC.

## Bekannte Caveats

- `test_eval_logic.py` importiert per `importlib` aus `test_eval.py`. Falls dort die Klassen umbenannt werden, hier mit-aktualisieren.
- `test_admin_endpoint.py` nutzt `unittest.mock.patch` auf `app.api.admin.settings`. Falls die Imports in `admin.py` umgestellt werden (z.B. direkter Import von Settings-Werten), muss der Mock-Path angepasst werden.
- Die Tests fuer Regex-basierte Extraktion sind absichtlich konservativ — sie pruefen das **erwartete** Verhalten, nicht alle moeglichen Fehlerzustaende. Edge-Cases wie "Frau MüllerSchmidt" (ohne Bindestrich) werden vom Regex evtl. nicht erfasst, das ist aktuell kein Problem.
