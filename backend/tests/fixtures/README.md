# Test-Fixtures – sysTelios Backend

Dieses Verzeichnis enthält Testdaten für die Backend-Testsuite.

## Struktur

```
fixtures/
├── audio/
│   ├── gespraech_kurz.wav          # 1s Stille – für schnelle Tests
│   ├── gespraech_lang.wav          # 10s Stille – für Timeout-Tests
│   └── [ECHT] gespraech_real.mp3  # ← Echte Aufnahme hier ablegen
│
├── pdf/
│   ├── verlaufsbericht.pdf         # Maschinenlesbar, mehrere Einträge
│   ├── selbstauskunft_digital.pdf  # Maschinenlesbar, ausgefüllt
│   ├── selbstauskunft_leer.pdf     # Fast leer – für Fehlerbehandlung
│   └── [ECHT] selbstauskunft_handschrift.pdf  # ← Echter Scan hier
│
├── docx/
│   ├── entlassbericht_vorlage.docx     # Leer mit Platzhaltern
│   ├── entlassbericht_beispiel.docx    # Ausgefüllt, realistisch
│   ├── verlaengerungsantrag_vorlage.docx
│   ├── stilprofil_verlaufsnotiz.docx   # Für pgvector-Tests
│   └── [ECHT] entlassbericht_real.docx # ← Echtes Dokument hier
│
└── txt/
    ├── transkript_einzelgespraech.txt  # Therapeut-Patient-Dialog
    ├── stichpunkte_verlauf.txt         # Bullet-Points
    ├── selbstauskunft_text.txt         # Maschinenlesbare Selbstauskunft
    └── verlaufsdokumentation.txt       # Mehrere Einträge chronologisch
```

## Echte Dateien einbinden

Legt echte (anonymisierte!) Beispieldateien in die entsprechenden Verzeichnisse.
Die Tests erkennen sie automatisch wenn sie mit `_real` im Namen enden
oder in `conftest.py` als `REAL_FILES` eingetragen sind.

## Anonymisierung

Vor dem Einbinden echter Dateien:
- Namen durch Pseudonyme ersetzen (z.B. "Herr M.", "Patient K.")
- Geburtsdaten verfremden
- Keine echten Fallnummern oder Adressen

## Hinweis

Alle Dummy-Dateien in diesem Verzeichnis enthalten ausschließlich
fiktive Daten und dienen nur zu Testzwecken.
