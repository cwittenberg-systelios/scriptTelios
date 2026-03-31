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

# Stilvorlagen von verschiedenen Therapeuten (fuer Stil-Evaluation)
mkdir -p /workspace/eval_data/styles/{TherapeutA,TherapeutB,TherapeutC}

# Pro Therapeut: echte Berichte als DOCX ablegen
cp /pfad/zu/EntlassberichtTherapeutA.docx      /workspace/eval_data/styles/TherapeutA/Entlassbericht.docx
cp /pfad/zu/VerlängerungsantragTherapeutA.docx  /workspace/eval_data/styles/TherapeutA/Verlängerungsantrag.docx
cp /pfad/zu/GesprächszusammenfassungA.docx      /workspace/eval_data/styles/TherapeutA/Gesprächszusammenfassung.docx

# Analog fuer TherapeutB und TherapeutC
cp /pfad/zu/EntlassberichtTherapeutB.docx      /workspace/eval_data/styles/TherapeutB/Entlassbericht.docx
# ... etc.
```

Das Eval-Framework extrahiert automatisch den relevanten Abschnitt aus
jedem DOCX anhand der Überschriften:

| Workflow | Abschnitt im DOCX |
|---|---|
| Entlassbericht | "Psychotherapeutischer Verlauf" |
| Verlängerung | "Bisheriger Verlauf und Begründung der Verlängerung" |
| Anamnese | "Aktuelle Anamnese" |
| Dokumentation | Gesamtes Dokument (Gesprächszusammenfassung) |

### 2. Tests ausführen

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

# ── Stil-Evaluation ──────────────────────────────────────

# Stil-Varianz-Test: prüft ob verschiedene Therapeuten-Stile
# zu unterschiedlichen Outputs führen (braucht mind. 2 Therapeuten in styles/)
pytest tests/test_eval.py -v -k "variance"

# LLM-als-Jury: bewertet wie gut der Output zur Stilvorlage passt
# Braucht vorherigen Run mit --eval-output
pytest tests/test_eval.py -v -k "test_eval_workflow" --eval-output /workspace/eval_results/
pytest tests/test_eval.py -v -k "jury" --eval-output /workspace/eval_results/

# Alle Stil-Tests zusammen
pytest tests/test_eval.py -v -k "variance or jury" --eval-output /workspace/eval_results/
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
    vorlage.txt               ← Stilvorlage (Beispieltext eines anderen Therapeuten)
  EB-HerrR/
    tpVerlaufsdokumentation.pdf
    vorlage.txt
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
  styles/                   ← Therapeuten-Stilvorlagen (DOCX)
    TherapeutA/
      Entlassbericht.docx
      Verlängerungsantrag.docx
      Gesprächszusammenfassung.docx
    TherapeutB/
      Entlassbericht.docx
      Verlängerungsantrag.docx
      Gesprächszusammenfassung.docx
    TherapeutC/             ← optional, 3. Therapeut für breitere Varianz
      ...

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
  "prompt": "Der custom_prompt des Therapeuten – mit Patientenname und Schwerpunkten",
  "diagnosen": ["F32.1"],
  "input_files": {
    "vorbefunde": "MeinOrdner/verlauf.pdf",
    "style_file": "MeinOrdner/vorlage.txt",
    "audio": "MeinOrdner/aufnahme.mp3",
    "selbstauskunft": "MeinOrdner/selbstauskunft.pdf"
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

Input-Felder:
- `prompt` – Der Therapeuten-Auftrag (Pflicht). Enthält patientenspezifische Schwerpunkte.
- `input_files.vorbefunde` – Verlaufsdokumentation (PDF) für EB/VA
- `input_files.selbstauskunft` – Selbstauskunft (PDF) für Anamnese
- `input_files.audio` – Aufnahme (MP3) für Anamnese und Gesprächsdoku
- `input_files.style_file` – Stilvorlage (.txt) eines anderen Therapeuten. Wird als `style_text` gesendet.

Dann die zugehoerigen Dateien nach `/workspace/eval_data/MeinOrdner/` kopieren.
