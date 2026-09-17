# Interview-Modus der Gesprächsdokumentation (v19.23)

## Zweck

Dokumentation ohne Transkript: nonverbale Verfahren (Kunst, Musik,
Körperarbeit, Körpertherapie) und Gespräche ohne Aufzeichnungseinwilligung.
Das System stellt nach der Sitzung Fragen, der Behandler antwortet per
Mikrofon oder Tastatur, das Protokoll wird zur Quelle der Doku.

## Ablauf

```
P1 → Tab „Interview“ → Set wählen (wird gemerkt) → [Fragen anpassen] → Interview starten
   Frage 1 (Text + Vorlesen) → Antwort (Diktat/Text) → Weiter
      → POST /api/interview/turn → Rückfrage? → Antwort → Weiter
   … → Pflichtfrage Selbstgefährdung → Abschließen → Übersicht (nachbearbeitbar)
   → „Verlaufsnotiz generieren“ → POST /api/jobs/generate (interview_protokoll=JSON)
```

Regeln: höchstens eine Rückfrage je Frage; Rückfrage nur, wenn ein
Pflichtaspekt der Frage in der Antwort fehlt (LLM-Check, Structured Output);
leere Pflichtantwort → Rückfrage ohne LLM; Fehler im Check → weiter ohne
Rückfrage. Nicht-Pflichtfragen dürfen übersprungen werden („nicht erhoben“).

## Endpoints

| Endpoint | Zweck |
|---|---|
| `GET /api/interview/sets` | Manifest der Sets (Server-Defaults, Reset-Punkt) |
| `POST /api/interview/transcribe` | Kurzdiktat (≤ 5 min, ≤ 20 MB) → Text; Whisper ohne Diarisierung, ohne Füllwortfilter; Tempdatei wird sofort gelöscht |
| `POST /api/interview/turn` | Rückfrage-Entscheidung zu einer Antwort |
| `POST /api/jobs/generate` | Form-Feld `interview_protokoll` (nur `dokumentation`) |

Protokoll-Format:

```json
{"set": "kunst", "set_label": "Kunsttherapie",
 "eintraege": [{"key": "anliegen", "frage": "…", "antwort": "…",
                "rueckfrage": "…", "rueckfrage_antwort": "…", "ziel_abschnitt": "auftragsklaerung"}]}
```

Validierung vor dem Job-Anlegen (422): gültiges JSON, mindestens eine
Antwort, Pflichtfrage `selbstgefaehrdung` beantwortet.

## Prompt

- User-Content: Block `PROTOKOLL DER THERAPEUTISCHEN NACHBEFRAGUNG`, jede
  Frage mit `[Zielabschnitt: …]`, unbeantwortet = `(nicht erhoben)`.
- System-Prompt: `INTERVIEW_MODUS_REGELN` – keine erfundenen Klientenzitate,
  Beobachtung und Deutung sprachlich trennen, Lücken nicht auffüllen,
  Antwort zur Selbstgefährdung als eigener Schlussabsatz.
- Für Glossar-Wahl, Stil-Retrieval und (nach v19.22-Verdrahtung) den
  Suizid-Quellcheck wird nur der Antworttext verwendet
  (`protokoll_plaintext`), nicht die Fragen.

## Fragen-Sets

Single Source of Truth: `backend/app/core/interview_sets.py`. Jedes Set
endet mit der nicht editierbaren Pflichtfrage zur Selbstgefährdung. Alle
anderen Fragen sind im UI je Nutzer editierbar (Draft-Cache), Reset auf
Server-Default über das Manifest.

## Vorlesen

Browser-TTS (`speechSynthesis`, deutsche Stimme bevorzugt), Schalter
„Fragen vorlesen“ (localStorage). Kein Server-TTS: die Fragen enthalten
keine Klientendaten und sind auch bei ausgeschaltetem Pod vorlesbar.

## Datenschutz

Es spricht nur der Behandler. Diktate werden nach der Transkription
gelöscht; nichts davon liegt in der DB. Das fertige Protokoll wird wie ein
Transkript im Job gespeichert (`result_transcript`) und unterliegt der
Job-Retention. Kürzel-Regel unverändert. Nachtrag für Datenschutzaudit v3.

## Tests

- `backend/tests/unit/test_v1923_interview.py`
- `frontend/tests/interview.test.jsx`
