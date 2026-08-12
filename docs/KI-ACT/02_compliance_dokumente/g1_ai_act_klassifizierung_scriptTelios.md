# AI-Act-Klassifizierung: scriptTelios

**Version:** 1.0-Entwurf · 12.08.2026 · Zur Prüfung und Bestätigung durch DSB / juristische Beratung
**Verantwortliche Stelle:** sysTelios Klinik für Psychosomatik und Psychotherapie, Wald-Michelbach/Siedelsbrunn

## 1. Systembeschreibung und Zweckbestimmung

scriptTelios ist ein intern entwickeltes, KI-gestütztes Dokumentationswerkzeug. Es unterstützt Therapeut:innen bei der Erstellung von Entwürfen klinischer Dokumente aus Sitzungsaufnahmen, Transkripten und vorhandenen Quelldokumenten. Sechs Workflows: Gesprächsdokumentation (P1), Anamnese+Befund (P2), Akutantrag (P2b), Verlängerung (P3), Folgeverlängerung (P3b), Entlassbericht (P4) sowie ISM-Fragebogen (P6).

**Zweckbestimmung:** Erstellung von *Entwürfen* zur Dokumentationsunterstützung. Das System trifft keine diagnostischen, therapeutischen oder sonstigen Entscheidungen über Personen. Jeder Output wird von der behandelnden Person fachlich geprüft, ggf. bearbeitet und verantwortet, bevor er Teil der Krankenakte oder eines Antrags wird.

**Technik:** Vollständig lokale Inferenz (Ollama: Gemma 4 31B, Mistral Small 3.2; faster-whisper large-v3; pyannote). Keine externen KI-Dienste, kein Training/Finetuning der Modelle mit Klinikdaten.

## 2. Rollen nach AI Act

| Rolle | Einordnung |
|---|---|
| **Anbieter** | sysTelios Klinik — Eigenentwicklung und Einsatz unter eigenem Namen (Art. 3 Nr. 3). |
| **Betreiber** | sysTelios Klinik — Einsatz unter eigener Autorität im Rahmen beruflicher Tätigkeit (Art. 3 Nr. 4). |
| GPAI-Modell-Anbieter | Nicht die Klinik. Basismodelle (Gemma 4, Mistral Small 3.2, Apache 2.0) werden unverändert eingesetzt; Modellpflichten des Kapitels V liegen bei den Modellherstellern. |

## 3. Prüfung: Verbotene Praktiken (Art. 5)

Keine einschlägig. Insbesondere: keine unterschwellige Beeinflussung, keine Ausnutzung von Schutzbedürftigkeit, kein Social Scoring, keine biometrische Kategorisierung, keine Emotionserkennung am Arbeitsplatz/in Bildungseinrichtungen (die Sprecherdiarisierung ordnet lediglich Redeanteile zu, sie bewertet keine Emotionen und trifft keine Rückschlüsse über Personen).

## 4. Prüfung: Hochrisiko (Art. 6 i.V.m. Anhang I und III)

**Anhang III (eigenständige Hochrisiko-Bereiche):** Keine Kategorie einschlägig. scriptTelios führt keine Biometrie durch, steuert keine kritische Infrastruktur, bewertet keine Personen im Kontext Bildung/Beschäftigung, entscheidet nicht über den Zugang zu wesentlichen (Gesundheits-)Leistungen — die Kostenübernahme-*Entscheidung* trifft die Krankenversicherung; scriptTelios formuliert lediglich Antrags-*Entwürfe*, die von Therapeut:innen geprüft und verantwortet werden.

**Anhang I (harmonisierte Produktvorschriften, insb. MDR):** scriptTelios ist nach hiesiger Einschätzung **kein Medizinprodukt**: Die Zweckbestimmung ist Dokumentationsunterstützung, nicht Diagnose, Prävention, Überwachung, Vorhersage, Prognose, Behandlung oder Linderung (Art. 2 Nr. 1 MDR). Software, die der Dokumentation, Archivierung oder verlustfreien Kommunikation dient, fällt nach MDCG-Abgrenzungsleitlinien (MDCG 2019-11) nicht unter die MDR. Damit greift auch der Hochrisiko-Pfad über Art. 6 Abs. 1 nicht.

**Ergebnis: Kein Hochrisiko-KI-System.** *(Vorbehalt: juristische Bestätigung ausstehend; Neubewertung bei jeder Änderung der Zweckbestimmung, s. §7.)*

## 5. Einschlägige Pflichten und Umsetzungsstand

| Pflicht | Umsetzung |
|---|---|
| Art. 4 KI-Kompetenz (seit 02/2025) | Schulungspaket für Pilot-Nutzer:innen (separates Dokument), Teilnahmedokumentation |
| Art. 50 Abs. 1 Transparenz ggü. Nutzenden | Permanenter Systemhinweis in der UI („KI-gestütztes System — Entwürfe erfordern fachliche Prüfung…"); Nutzer:innen sind ausschließlich geschulte Fachkräfte |
| Art. 50 Abs. 4 Kennzeichnung generierter Texte | Nicht einschlägig: Krankenakte und Anträge an Kostenträger sind keine Veröffentlichung zur Information der Öffentlichkeit; zudem erfolgt stets menschliche Prüfung. Keine Kennzeichnung pro Dokument. |
| Transparenz ggü. Betroffenen (DSGVO Art. 13/14) | Einwilligungserklärung mit vollständiger Information über KI-Einsatz, Datenkategorien, Speicherfristen, Rechte |
| Basis-Dokumentation | Dieses Dokument, Datenschutzaudit v2, Eval-Reports, Systemdossier (in Konsolidierung) |
| Nachvollziehbarkeit | audit.log (ohne Inhalte), prompts.log/feedback.log via job_id verknüpfbar |
| Robustheit/Datenqualität (Grundsatz) | Source Gate (Abbruch statt Konfabulation), Quellentreue-QC, Coverage-Gap-Tracking, Eval-Suite mit Mindestscore |
| Menschliche Aufsicht (Grundsatz) | Entwurfs-Prinzip, editierbare Vorschau, fachliche Letztverantwortung dokumentiert |

## 6. Freiwillige Maßnahmen über die Pflicht hinaus

QC-Warnungen für Wir-Form und pathologisierende Sprache (Awareness), Feedback-Logging mit 10-Jahres-Aufbewahrung, regelmäßige Eval-Läufe mit dokumentierten Qualitätsscores.

## 7. Neubewertungs-Trigger

Diese Klassifizierung ist neu zu bewerten bei: (a) Änderung der Zweckbestimmung (z. B. diagnostische Hinweise, Risiko-Scores, Therapieempfehlungen), (b) Wegfall der verpflichtenden menschlichen Prüfung, (c) Einsatz für Entscheidungen über Leistungszugang, (d) wesentlichen Rechtsänderungen/Leitlinien der Kommission zu Anhang III. Review mindestens jährlich, dokumentiert.

## 8. Freigabe

| Rolle | Name | Datum | Unterschrift |
|---|---|---|---|
| Entwicklung | C. Wittenberg | | |
| Datenschutzbeauftragte:r | | | |
| Klinikleitung | | | |
