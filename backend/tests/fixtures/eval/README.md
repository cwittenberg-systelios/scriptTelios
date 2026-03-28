# scriptTelios Evaluations-Framework

Automatisierte Qualitaetsmessung der LLM-Generierung.
Prueft Wortanzahl, Keywords, Datenschutz, Halluzinationen und Struktur.

## Quick Start (ohne Testdaten – nur Struktur-Checks)

```bash
cd /workspace/scriptTelios/backend
source /workspace/venv/bin/activate
pytest tests/test_eval.py -v --tb=short
```

## Vollstaendiger Test (mit echten Patientendaten)

### 1. Eval-Daten vorbereiten

Die Test-PDFs werden NICHT im Git gespeichert (Datenschutz).
Stattdessen auf dem Pod ablegen:

```bash
# Verzeichnisstruktur anlegen
mkdir -p /workspace/eval_data/{EB-FrauM,EB-HerrR,VA-Frau-v-d-A,Anamnese-FrauT,AnamneseFrauK,GESPRAECH-FrauK,GESPRAECH-HerrR}

# Verlaufsdokumentationen kopieren (aus den Testfaellen)
cp /pfad/zu/EB-FrauM/tpVerlaufsdokumentation.pdf     /workspace/eval_data/EB-FrauM/
cp /pfad/zu/EB-HerrR/tpVerlaufsdokumentation.pdf      /workspace/eval_data/EB-HerrR/
cp /pfad/zu/VA-Frau-v-d-A/tpVerlaufsdokumentation.pdf /workspace/eval_data/VA-Frau-v-d-A/

# Selbstauskunft-PDFs kopieren
cp /pfad/zu/Anamnese-FrauT/selbstauskunft.pdf          /workspace/eval_data/Anamnese-FrauT/
cp /pfad/zu/AnamneseFrauK/selbstauskunft.pdf            /workspace/eval_data/AnamneseFrauK/

# Aufnahmegespraech-Audio kopieren (optional, fuer Transkriptionstest)
cp /pfad/zu/Anamnese-FrauT/aufnahme.mp3                /workspace/eval_data/Anamnese-FrauT/
cp /pfad/zu/AnamneseFrauK/aufnahme.mp3                  /workspace/eval_data/AnamneseFrauK/

# Gespraechs-Audio kopieren (fuer Gespraechsdoku-Workflow)
cp /pfad/zu/GESPRAECH-FrauK/aufnahme.mp3               /workspace/eval_data/GESPRAECH-FrauK/
cp /pfad/zu/GESPRAECH-HerrR/aufnahme.mp3               /workspace/eval_data/GESPRAECH-HerrR/
```

### 2. Tests ausfuehren

```bash
cd /workspace/scriptTelios/backend
source /workspace/venv/bin/activate

# Alle Workflows (8 Tests, ~5-10 Min auf GPU)
pytest tests/test_eval.py -v --tb=short

# Nur einen Workflow
pytest tests/test_eval.py -v -k "entlassbericht"
pytest tests/test_eval.py -v -k "anamnese"
pytest tests/test_eval.py -v -k "verlaengerung"
pytest tests/test_eval.py -v -k "dokumentation"

# Nur einen spezifischen Testfall
pytest tests/test_eval.py -v -k "eb-01"

# Mit Ergebnis-Speicherung (fuer manuelles Review)
pytest tests/test_eval.py -v --eval-output /workspace/eval_results/

# Anderer Server
EVAL_BACKEND_URL=http://localhost:8000 pytest tests/test_eval.py -v

# Anderes Eval-Daten-Verzeichnis
EVAL_DATA_DIR=/pfad/zu/testdaten pytest tests/test_eval.py -v
```

### 3. Ergebnisse lesen

```
[PASS] entlassbericht/eb-01-depression-anteilearbeit (721w)
  ✓ 12 Checks bestanden

[FAIL] anamnese/an-02-schulangst-jugendliche (145w)
  ✓ 5 Checks bestanden
  ✗ 3 Probleme:
    - Zu kurz: 145w < 300w Minimum
    - HALLUZINATION: 'berufliche Überlastung' gefunden
    - Befund-Separator '###BEFUND###' fehlt
```

Kritische Fehler (DATENSCHUTZ, HALLUZINATION) lassen den Test sofort failen.
Andere Issues werden als Warnings geloggt; der Test failt wenn der
Gesamt-Score unter 70% faellt.

## Verzeichnisstruktur

```
tests/
  fixtures/
    eval/
      fixtures.json     ← Testfaelle mit erwarteten Werten
      README.md         ← Diese Datei
  test_eval.py          ← Pytest-Script

/workspace/eval_data/   ← Testdaten (NICHT im Git, nur auf dem Pod)
  EB-FrauM/
    tpVerlaufsdokumentation.pdf
  EB-HerrR/
    tpVerlaufsdokumentation.pdf
  VA-Frau-v-d-A/
    tpVerlaufsdokumentation.pdf
  Anamnese-FrauT/
    selbstauskunft.pdf
    aufnahme.mp3          ← Aufnahmegespraech (optional, fuer Transkriptionstest)
  AnamneseFrauK/
    selbstauskunft.pdf
    aufnahme.mp3          ← Aufnahmegespräch (optional)
  GESPRAECH-FrauK/
    aufnahme.mp3          ← Therapiegespräch-Aufnahme
  GESPRAECH-HerrR/
    aufnahme.mp3          ← Therapiegespräch-Aufnahme

/workspace/eval_results/ ← Generierte Ergebnisse (optional, via --eval-output)
  entlassbericht/
    eb-01-depression-anteilearbeit.txt
    eb-01-depression-anteilearbeit.eval.txt
  anamnese/
    ...
```

## Neue Testfaelle hinzufuegen

In `fixtures.json` einen neuen Eintrag im jeweiligen Workflow-Array:

```json
{
  "id": "eb-03-mein-neuer-test",
  "name": "Beschreibung",
  "prompt": "Der Prompt fuer das LLM",
  "diagnosen": ["F32.1"],
  "input_files": {
    "vorbefunde": "MeinOrdner/verlauf.pdf"
  },
  "expected": {
    "min_words": 500,
    "max_words": 900,
    "required_keywords": ["Anteile", "ambulant"],
    "forbidden_patterns": ["**", "##"],
    "forbidden_names": ["Nachname", "A-Nummer"],
    "must_not_hallucinate": ["erfundener Begriff"]
  }
}
```

Dann die zugehoerige PDF nach `/workspace/eval_data/MeinOrdner/` kopieren.
