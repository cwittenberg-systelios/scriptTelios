# Sprintplan v19.36 – Vision-OCR: kann gemma4 llava ersetzen?

Basis: `1c3a998`. Ziel: llava (4,7 GB) freigeben und den Modellwechsel beim
Scan-Fallback sparen, wenn gemma4:31b die Stufe 3 (Vision-OCR) mindestens
genauso gut kann, vor allem bei Checkboxen.

| # | Schritt | Test |
|---|---|---|
| S1 | `extraction.vision_payload()`: Modell-Override, `think: false`, fester `num_ctx` bei `LLM_FIXED_CTX` (sonst lädt Ollama gemma neu), `keep_alive: -1` nur für Routing-Modelle (llava behält den Standard). Timeout 120 → 300 s. | Unit |
| S2 | `scripts/eval_vision_ocr.py`: 6 synthetische Formularseiten (2 sauber, 2 leicht gescannt, 2 schlecht: Fax-Auflösung, flauer Kontrast, Drehung, Rauschen, JPEG). Angekreuzt wird mit X, Haken oder Ausfüllen, teils mit blassem Stift. Die Wahrheit ist bekannt. Kennzahlen: Checkboxen richtig, falsch an (gefährlich), falsch aus, fehlt, Textfelder, Sekunden pro Seite. Prüft die Vision-Fähigkeit per `/api/show`. Optional eine eigene anonymisierte Seite über `--pdf`/`--truth`. | Unit mit Attrappe |
| S3 | **Test auf dem Pod (du):** Lauf abends. Entscheidungsregel unten. | – |
| S4 | Bei Erfolg: `VISION_MODEL=gemma4:31b` in `/workspace/.env`, Neustart, `ollama rm llava`. Kein Code nötig. | – |

**Entscheidungsregel (Vorschlag):** gemma ersetzt llava, wenn
- Checkboxen richtig ≥ llava, und
- **falsch an = 0** (ein nicht angekreuztes „Suizidgedanken“ als [X] wäre schlimmer als eine Lücke), und
- die Textfelder ≥ llava sind.
Die Laufzeit pro Seite ist zweitrangig, die Stufe 3 läuft selten.
