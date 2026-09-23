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

## Gesprächsführung, Trigger, Abschluss (v19.24)

- Erste Frage jedes Sets: „Um wen geht es?“ – füllt Kürzel und Geschlecht;
  Rückfragen verwenden danach die Anrede. Nicht im Prompt.
- Quittung (nur vor Rückfragen) und Überleitung (nur bei gegebener Antwort)
  kommen vorlagenbasiert vom Backend (`interview_phrasen.py`); das Frontend
  spricht sie mit der Frage als Sequenz (`speech.js`).
- Suizidalitäts-Trigger (regelbasiert, für jede Frage): Glied 1 „Pläne oder
  Handlungen, absprachefähig?“, Glied 2 „Was wurde vereinbart –
  Kooperationsbedingung, ärztliche Information, Nachtdienst?“. Trigger vor
  Aspekt-Check; Nachfragen stehen im Protokoll als „NACHFRAGE (Suizidalität)“.
- Abschluss-Check (`POST /api/interview/abschluss`): bis zu drei Fragen
  (Widerspruch, Lücke, Plausibilität) über das ganze Protokoll; jeder Punkt
  wird beantwortet oder „so gelassen“ – belassene Punkte stehen im Prompt als
  „Bewusst offen gelassen – NICHT ergänzen“.
- Feedback-Button am Ende (`job_id = interview-<session>`, `context =
  interview_dialog`); Prompt-Log-Einträge des Dialogs laufen unter derselben ID.

## Dialog-Modus (v19.31)

Zweite Form des Interviews: das Modell führt das Gespräch, die Fragenliste
ist seine Checkliste. Je Turn schickt das Frontend die Historie an
`POST /api/interview/chat/stream`; vorher laufen deterministische
Leitplanken (`interview_chat.plan_turn`): Suizidalitäts-Trigger als
Regie-Anweisung, Pflichtpunkte per Marker-Check (`fertig` wird sonst
verweigert), Nachfrage-Budget je Thema/gesamt (`ChatConfig`, Default 4/24),
Redundanz-Erkennung. Das Modell antwortet als JSON (`sage`, `abgedeckt`,
`unklar`, `thema`, `fertig`); nur `sage` wird gestreamt und satzweise
vorgelesen. Am Ende geht das Gespräch als `interview_gespraech` an
`/jobs/generate`; nur Behandler-Turns sind Quelle, Klient erscheint in der
Doku ausschließlich als Kürzel. Eval: `scripts/eval_interview_chat.py`.

## Ohne laufenden Server (v19.24.1)

Die Fragen-Sets liegen im Bundle (`INTERVIEW_SETS_DEFAULT`, generiert aus
`interview_sets.py`). Beim Öffnen des Tabs wird der Server still gestartet;
bis er läuft, zeigt der Tab eine Statuszeile, Fragen anpassen und Tippen
sind sofort möglich, Diktat und Rückfrage-Check warten auf den Server.

## Endpoints

| Endpoint | Zweck |
|---|---|
| `GET /api/interview/sets` | Manifest der Sets (Server-Defaults, Reset-Punkt) |
| `POST /api/interview/transcribe` | Kurzdiktat (≤ 5 min, ≤ 20 MB) → Text; Whisper ohne Diarisierung, ohne Füllwortfilter; Tempdatei wird sofort gelöscht |
| `POST /api/interview/turn` | Rückfrage-Entscheidung zu einer Antwort (liefert `rueckfrage_typ`, `trigger_stufe`, `quittung`, `ueberleitung`, `klient`) |
| `POST /api/interview/abschluss` | Abschluss-Check über das ganze Protokoll (Fragenkarten) |
| `POST /api/interview/chat/stream` | Ein Turn des Dialog-Modus als SSE (`delta`, `meta`, `done`, `error`) |
| `POST /api/jobs/generate` | Form-Feld `interview_gespraech` (Dialog-Modus) oder `interview_protokoll` (Fragenkarten) |
| `POST /api/jobs/generate` | Form-Feld `interview_protokoll` (nur `dokumentation`) |

Protokoll-Format:

```json
{"set": "kunst", "set_label": "Kunsttherapie", "session_id": "s…",
 "eintraege": [{"key": "klient", "frage": "…", "antwort": "Herr M.", "nachfragen": []},
               {"key": "anliegen", "frage": "…", "antwort": "…", "ziel_abschnitt": "auftragsklaerung",
                "nachfragen": [{"typ": "aspekt|trigger:suizidalitaet|klient|pflicht", "frage": "…", "antwort": "…"}]}],
 "abschluss": [{"typ": "widerspruch|luecke|plausibilitaet", "bezug": ["2","5"], "frage": "…", "antwort": "…", "belassen": false}]}
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
