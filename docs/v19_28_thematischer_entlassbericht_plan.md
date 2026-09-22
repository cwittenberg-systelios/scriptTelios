# Sprintplan v19.28 — Thematischer Entlassbericht (ENTWURF)

Stand: 2026-09-22 · Basis: `v19_QA_v02` @ 5edeaae · Status: **S0 durchgeführt → GO → S1–S5 + D7/D8 implementiert (v19.28, 2026-09-22); S6 Eval über Rückmeldeplattform steht aus**

## 1. Ausgangslage (Ist)

| Ebene | Ist-Zustand | Fundstelle |
|---|---|---|
| Stage 1 (Verdichtung) | Strikt chronologisch, „KEINE INTERPRETATION“; Fokus-Hint EB: Gesamtbogen + Modalitäten erhalten (v19.20 M5) | `verlauf_summary.py` |
| Stage 2 Anweisung (editierbar) | Teil 1 Verlauf (je Modalität ein Absatz) · Teil 2 Epikrise · Teil 3 Empfehlungen | `prompts.py` `WORKFLOW_INSTRUCTIONS_DEFAULT["entlassbericht"]` |
| Pflichtkern (nicht editierbar) | MODALITÄTEN-Regel (v19.20 M2), Ressourcen-Tonalität, Testwerte-Guard, Few-Shot | `BASE_PROMPTS["entlassbericht"]` |
| User-Content | Antragsvorlage → Verlaufsdoku (Stage-1-Summary) → Prozessreflexion (optional, v19.13) → Diagnosen → `fokus_themen` | `build_user_content` |
| QC | REQUIRED: Anliegen/Ziele, Verlauf, Gesamtbewertung, Empfehlung · RECOMMENDED: Einzel, Gruppe, Nonverbal | `quality_specs.py` |

Bemerkenswert: Der bestehende Few-Shot ist **bereits thematisch** (Selbstwert/Leistung zieht sich durch alle Absätze) — nur die Anweisung ist modalitätsbasiert. Das Modell kennt die Zielform also schon.

## 2. Ziel-Struktur (Soll)

| # | Abschnitt | Quelle | Schwierigkeit fürs LLM |
|---|---|---|---|
| 1 | Auftrag des Klienten („wieder funktionieren“) | Antragsvorlage/Anamnese, erste Gesprächsdokus (Auftragsklärung) | gering |
| 2 | Erarbeitung des zentralen Themas / Musters (z. B. Selbstwert ↔ Leistung) | Hypothesen-Abschnitte der Gesprächsdokus, Wendepunkte | **hoch** (Abstraktion über 50–80 Sitzungen) |
| 3 | Prozessfortschritte in Einzel-, Gruppen-, Nonverbaler Therapie — jeweils rückgebunden ans Thema | Stage-1-Summary (Modalitätseinträge) | mittel (Modalitätsverlust-Risiko, s. v19.20) |
| 4 | Reflexion und Symptomveränderung | Prozessreflexion + Testwerte + Gesamtentwicklung | gering |
| 5 | Empfehlungen | wie bisher, als Vertiefung des Themas gerahmt | gering |

## 3. Lösungsansatz: Zwischenstufe „Fallformel“ (Stage 1b)

Die harte Aufgabe (Abstraktion: *Was ist das Muster?*) wird vom Schreiben getrennt.

```
Antragsvorlage + Stage-1-Summary
        │
        ▼
Stage 1b: FALLFORMEL (kurz, strukturiert, ~150–250 Wörter, temp 0.2)
   - Auftrag (in Worten des Klienten, mit Quellenbezug)
   - Zentrales Thema / Muster (mit Sitzungsbezug: "erstmals 14.03., vertieft 02.04.")
   - Wendepunkte je Modalität (Einzel / Gruppe / Nonverbal)
   - Symptomveränderung (Testwerte nur wörtlich aus Quelle)
   - Offene Themen → Empfehlungen
   + Halluzinationscheck (Verfahren, Daten, Zahlen müssen in Quelle stehen)
        │
        ▼  (optional: Therapeut:in korrigiert/ersetzt das Thema → D1)
Stage 2: Bericht in 5-teiliger thematischer Struktur, Fallformel als Leitfaden
```

Warum das dem Modell hilft: Stage 2 muss dann nicht mehr *finden*, sondern nur noch *entfalten*. Das Thema steht als expliziter roter Faden im Prompt, jeder Modalitätsabsatz kann daran anknüpfen, und QC bekommt einen Prüfanker (Thema muss in Abschnitt 2, 3 und 5 vorkommen).

Fallback ohne Stage 1b: Therapeut:in trägt Auftrag + Thema in `fokus_themen` ein (Feld existiert bereits). Das ist auch der schnellste Prototyp für S0.

## 4. Entscheidungen (getroffen 2026-09-22)

| ID | Entscheidung |
|---|---|
| D1 | **B** — LLM schlägt Fallformel vor, Therapeut:in editiert, Rerun |
| D2 | LLM schlägt **mehrere** Themen vor; Therapeut:in wählt — je weniger desto besser, **max. 3** |
| D3 | **A** — Modalitäten bleiben eigene Absätze in Abschnitt 3. Wiederholungsrisiko anerkannt → Prompt-Regel „Muster in Abschnitt 2 einmal erklären, Modalitätsabsätze bringen nur den neuen Schritt“ + Redundanz-Check in QC (S4) |
| D4 | **ja** — Prozessreflexion in Abschnitt 4 (Reflexion + Symptomveränderung/Testwerte) |
| D5 | **Schalter im Formular** (kein eigener Workflow, keine Code-Duplikation); **Default = Status quo** (modalitätsbasiert) |
| D6 | **ja** — Stage 1 erhält dokumentierte Therapeut:innen-Hypothesen als „laut Protokoll“; eigene Deutungen bleiben verboten |

Kleine Entscheidungen (Claude): Fallformel als Markdown mit festen `###`-Überschriften (nicht JSON — robuster bei gemma/mistral); Themenvorschläge als nummerierte Liste `### Themenkandidaten` (1–3, je mit Sitzungsbezug und einem Satz Begründung), Auswahl im Frontend per Checkbox, gewählte Themen gehen als `FALLFORMEL` in Stage 2; Persistenz im Job analog `verlauf_summary_text`; Stage 1b läuft nur bei Schalter = thematisch.

## 5. Schritte (in sich abgeschlossen, testbar)

| Schritt | Inhalt | Test / Abnahme |
|---|---|---|
| **S0 Prototyp** | Nur Prompt: thematische `WORKFLOW_INSTRUCTIONS_DEFAULT`, Thema über `fokus_themen` von Hand. Auf EB-FrauM / EB-HerrR (Eval-Daten) laufen lassen. | Sichtprüfung: trägt die Struktur? Modalitäten vollständig? → Go/No-Go für S1–S6 |
| **S1 Stage 1** | Fokus-Hint EB erweitert (D6): Abschnitt `### Dokumentierte Hypothesen und Muster` (nur EB), Quellenmarker Pflicht | pytest: Retention-Check auf Eval-Verläufen (Hypothesen-Sätze aus Gesprächsdokus tauchen in Summary auf) |
| **S2 Stage 1b** | `fallformel.py`: Prompt, Aufruf, Halluzinationscheck (wiederverwendet `detect_summary_hallucination_signals`), Persistenz `fallformel_text` im Job | pytest mit Mock-LLM; Signal-Tests (erfundenes Verfahren/Datum → Warnung) |
| **S3 Stage 2** | Neue Anweisung (5 Teile), MODALITÄTEN-Regel auf Abschnitt 3 bezogen, Anti-Wiederholungs-Regel (D3), Few-Shot leicht umgestellt (vorhandener ist fast passend), User-Content: FALLFORMEL-Block, Prozessreflexion → Abschnitt 4 (D4); Struktur-Schalter (D5) | pytest: Prompt-Registry-Tests, Schalter beide Pfade; Wortlimit-Floor unverändert |
| **S4 QC** | REQUIRED_SECTIONS EB: Auftrag · Thema/Muster · Prozess · Reflexion/Symptomveränderung · Empfehlung (Synonyme); RECOMMENDED Modalitäten bleiben; neuer Info-Check „Thema-Kohärenz“ (Kernbegriff der Fallformel in ≥ 3 Teilen); Redundanz-Check (Satzähnlichkeit zwischen Absätzen, Info-Level) | pytest QC; kein Regress bei modalitätsbasiertem Pfad |
| **S5 Frontend P4** | Struktur-Schalter (Default Status quo); Fallformel mit Themenkandidaten anzeigen, Auswahl (max. 3) + Freitext-Edit; „Mit angepasster Fallformel neu generieren“ (D1=B) | Jest/React-Tests, ESLint 0, `systelios.js` neu gebaut |
| **S6 Eval** | Beide Strukturen auf EB-FrauM/EB-HerrR + reale Fälle über Rückmeldeplattform; Metriken: Modalitätsabdeckung, Halluzinationssignale, Therapeut:innen-Rating | Kein Regress Modalitätsabdeckung vs. v19.20; Rating thematisch ≥ modalitätsbasiert |
| **S7 Doku** | `two_stage_pipeline.md` (Stage 1b), CHANGELOG, Prompt-Defaults-Export | — |

Auslieferung: S1–S5 als **ein** Patch (deploybares Stück), S0 vorab als reiner Prompt-Test ohne Deploy.

## 6. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Modell erfindet ein „schönes“ Muster, das die Quellen nicht tragen | Fallformel mit Sitzungsbezug-Pflicht + Halluzinationscheck; Therapeut:in sieht und korrigiert sie (D1=B) |
| Modalitäten gehen im thematischen Fluss verloren (v19.20-Regress) | D3=A: eigene Absätze bleiben Pflichtkern; QC RECOMMENDED bleibt |
| Ein Zusatz-LLM-Aufruf → Laufzeit/Kosten | Fallformel ist kurz (~300 Token Output, Input = Summary, nicht Roh-Verlauf); `progress_bands` EB anpassen |
| Fälle ohne klares Thema (kurze Aufenthalte, Abbrüche) | Fallformel darf „kein durchgängiges Muster dokumentiert“ ausgeben → Stage 2 fällt auf chronologische Prozessdarstellung zurück |

## 7. S0-Ergebnis (2026-09-22, gemma4:31b, je Fall 1 Lauf)

| # | Kriterium | FrauM SQ | FrauM TH | HerrR SQ | HerrR TH |
|---|---|---|---|---|---|
| 1 | 5 Teile in Reihenfolge | – | ✓ | – | ✓ |
| 2 | Auftrag in Worten des Klienten | ○ | ✓ | ○ | ✓ |
| 3 | Thema einmal erklärt, nicht je Absatz neu | – | ✓ (leicht doppelt in Einzel) | – | ✓ |
| 4 | Einzel / Gruppe / Nonverbal je eigener Absatz | ○ (Gruppe+Nonverbal gemischt) | ✓ (4 Absätze) | ○ (Körper+Gruppe gemischt) | ✓ (4 Absätze) |
| 5 | Modalitätsabsätze bringen neuen Schritt | ○ | ○ (Gruppe generisch) | ○ | ✓ |
| 6 | Wendepunkte korrekt zugeordnet | ○ | ✗ Elternbesuch fehlt | ✗ Türsteher/Fatman/Trennung fehlen | ✓ Fatman→Kunst, Trennung→Einzel |
| 7 | Nichts erfunden | ✓ | ○ „Schwächen kaschieren“ (Gruppenthema als Klientin) | ✗ empfiehlt „Stabilisierung der Partnerschaft“ trotz Trennungsentscheidung | ○ „Achtsamkeitsübungen“ (Stage-1-Halluzination, QC hat's gefangen) |
| 8 | Reflexion in Teil 4, indirekte Rede | – | ✓ | – | ✓ |
| 9 | Empfehlungen greifen Thema auf | ○ | ✓ | ✗ (s. 7) | ✓ |
| 10 | Eleganz (Claude-Schätzung, 1–5) | 3 | 4 | 3 | 4,5 |

Go-Kriterium erfüllt: TH ≥ SQ bei 4 und 7, besser bei 1, 3, 5, 10. Laufzeit TH nicht länger (235/179 s vs. 321/215 s), Text 15–30 % länger.

### Nebenbefunde aus S0 (Ergänzungen für S1–S4)

| Befund | Konsequenz |
|---|---|
| Stage-1-Summary HerrR-SQ deckte nur 02.01.–29.01. ab (Dezember komplett weg, Übersicht behauptet „69 Sitzungen vom 02.01.“) → Türsteher-Einzel 15.12. fehlte im Bericht | **S1**: Abdeckungs-Guard — Datumsspanne der Summary vs. Rohtext prüfen, bei Lücke Retry/Chunking; Fallformel darf nur auf vollständiger Summary aufsetzen |
| MISSING_STICHPUNKT zerlegt den Fokus-Prosa-Block in Fragmente („Angstkrise mit“, „fühlt sich“) | **S4**: Fallformel-Block nicht durch den Stichpunkt-Zeilenparser; eigener Thema-Kohärenz-Check |
| MISSING_SECTION_GESAMTBEWERTUNG bei TH (erwartet) | **S4**: REQUIRED_SECTIONS je Schalter |
| Elternbesuch-Wendepunkt (Fokus) nicht aufgegriffen | **S2/S3**: Fallformel führt „Wendepunkte je Modalität“ als eigenen Abschnitt; Anweisung Teil 3 verweist explizit darauf |
| DASS-21 Angst 2 → 12 (Verschlechterung) in BEIDEN Varianten verschwiegen, günstige Werte genannt | unabhängig vom Sprint; Vorschlag: deterministischer Testwerte-Vollständigkeitscheck (alle prä/post-Paare der Vorlage im Output) — **Scope-Entscheidung D7** |
| Primer-Doppelung „Zu Beginn des stationären AufenthaltsWir erlebten …“ in 2/4 Läufen (gemma4 setzt Assistant-Prefill nicht fort) | unabhängig vom Sprint; kleiner Postprocessing-Fix in `llm.py` — **D8** |
