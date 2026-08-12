# scriptTelios — AI-Act/DSGVO-Sprint: Gesamtzusammenstellung

**Zeitraum: 12.08.2026 · Branch-Basis aller Patches: `v19_QA_v02` @ `b9deb0f`**

---

## 1. Ablauf der Arbeitsschritte

| # | Schritt | Ergebnis |
|---|---|---|
| 1 | AI-Act-Gap-Analyse (Betreiber+Anbieter-Doppelrolle, Arbeitshypothese „kein Hochrisiko", 11 Gaps G1–G11 priorisiert, ~8–12 PT davon ~1 PT Code) | `ai_act_gap_analyse_scriptTelios.md` |
| 2 | D1 geklärt: Art. 50 verlangt keine Kennzeichnung pro Dokument (Krankenakte/Kassenanträge ≠ Veröffentlichung; menschliche Prüfung); UI-Hinweis genügt | Begründung in Sprintplan G6 + G1-Dokument §5 |
| 3 | **G6-Sprint:** Transparenzhinweis in Sidebar, per F4 gekürzt auf „KI-Entwürfe — fachliche Prüfung erforderlich." Frontend-Suite 48/48, Build + Bundle-Marker verifiziert | Patch v2 + systelios.js |
| 4 | G1-Klassifizierungsdokument (Art. 5/6-Prüfung, MDR-Abgrenzung, Pflichtenkatalog, Neubewertungs-Trigger; F2: Diarisierung ≠ Emotionserkennung bestätigt) | zur DSB-Zeichnung |
| 5 | G2-Schulungspaket (Art. 4 KI-Kompetenz, 60 Min., Teilnahmebestätigung) + Übungsfall „03-depression-verlaengerung" mit 3 eingebauten Fehlern und Lösungsblatt (F3; vollständig fiktiv) | 2 Dokumente |
| 6 | G3a: Einwilligung v1.1 — §1 geschärft („dedizierte, ausschließlich von der Klinik kontrollierte Server in der EU"), §1a Einführungsphasen-Transparenz, §6a Qualitätssicherung 90 Tage (TIA-Option a); §7 (wissenschaftliche Weiterverwendung) vorerst gestrichen, kommt in Produktivphase (v1.2) | Formulierungsdokument |
| 7 | F1-Recherche: RunPod-Standard-DPA existiert, gilt aber erst nach unterschriebener Einreichung → **aktuell kein AVV**; Special-Categories-Notifikationspflicht; SCCs im DPA inkorporiert | Recherchedokument |
| 8 | Entscheidung **H2** (Echtdaten-Pilot auf RunPod statt Warten auf Hardware): 8-Schritte-Plan mit Go-Live-Gate; TIA-Entwurf vorbefüllt | 2 Dokumente |
| 9 | DPA-Ausfertigung: druckbares Signatur-Beiblatt (PDF); Druckweg (DevTools-Snippet gegen Sticky-Header) und Trust-Center-Weg geklärt | PDF |
| 10 | Pod bestätigt: **Secure Cloud EU-RO-1** → in TIA eingetragen (H2-Checkpunkte 3.1/3.2 erfüllt) | TIA aktualisiert |
| 11 | **Retention-Sprint §6a:** automatische 90-Tage-Löschung (Jobs-DB, Recordings-DB inkl. Transkripte + Dateireste, feedback.log 3650→90; prompts.log bereits 14 Tage konform) | Patch + Drop-ins |

## 2. Dokumente (alle in dieser Unterhaltung ausgeliefert)

**Analyse & Planung**
- `ai_act_gap_analyse_scriptTelios.md` — Gap-Analyse mit Priorisierung/Aufwand, RunPod-vs-On-Prem-Bewertung
- `h2_umsetzungsplan_runpod_echtdaten.md` — 8 Schritte bis Echtdaten-Go-Live inkl. Notifikations-Entwurf (EN) und Gate-Checkliste
- `f1_runpod_avv_recherche.md` — DPA-/AVV-Befund, Optionen H1–H3

**Compliance-Dokumente (zur DSB-/Leitungs-Zeichnung)**
- `g1_ai_act_klassifizierung_scriptTelios.md` — Risikoklassifizierung mit Freigabeblock
- `h2_tia_entwurf_runpod.md` — Transfer Impact Assessment, vorbefüllt, Region EU-RO-1 eingetragen; offene [DSB]-Felder: FISA/CLOUD-Act-Würdigung, DPF-Zertifizierungscheck, Vorhaltefrist-Bestätigung, Kopplungsverbots-Würdigung §6a
- `g3a_einwilligung_v1_1_formulierung.md` — v1.1-Änderungen: §1 neu, §1a (Einführungsphase/AVV), §6a (QS 90 Tage) inkl. Fallback-Variante, §7 zurückgestellt bis Produktivphase
- `runpod_dpa_signaturblatt.pdf` — druckbares Ausfertigungs-/Signaturblatt zum DPA

**Schulung (Art. 4)**
- `g2_schulung_pilotnutzer_scriptTelios.md` — Schulungsunterlage mit Teilnahmebestätigung
- `g2_uebungsfall_teil_e.md` — fiktiver Übungsfall + Lösungsblatt

**Sprintpläne**
- `sprintplan_g6_ki_kennzeichnung.md`
- `sprintplan_retention_90d.md`

## 3. Patches & Code-Auslieferungen

| Patch | Inhalt | Dateien | Verifikation |
|---|---|---|---|
| `g6_ki_transparenzhinweis_v2.patch` *(ersetzt v1 — nicht beide anwenden)* | Sidebar-Transparenzhinweis inkl. Begründungskommentar | `frontend/klinische-dokumentation.jsx` (+12) | `git apply --check` gegen b9deb0f ✓, esbuild ✓, Vite-Build ✓, Bundle-Marker ✓, Jest 48/48 ✓ |
| `retention_90d_par6a.patch` | RETENTION-Key `qa_artifacts` (90 d) + `cleanup_jobs_db()` + `cleanup_recordings_db()` + Aufnahme in `retention_task`; feedback.log 90 d; Recording-Docstring | `backend/app/services/retention.py` (+79), `backend/app/api/feedback.py`, `backend/app/models/db.py`, `backend/tests/unit/test_retention_db.py` (neu, +158) | `git apply --check` gegen **zweiten frischen Clone** ✓, +-Zeilen-Content-Verifikation 0 fehlend ✓, Retention-Tests 20/20 ✓ (SQLite) |

**Drop-ins (`__`-Pfadkodierung):** `backend__static__systelios.js` (G6-Stand), `frontend__klinische-dokumentation.jsx`, `backend__app__services__retention.py`, `backend__app__api__feedback.py`, `backend__app__models__db.py`, `backend__tests__unit__test_retention_db.py`

Beide Patches sind unabhängig und gegen `b9deb0f` geschnitten; Anwendung in beliebiger Reihenfolge.

## 4. Testlage

- Frontend: Jest 3 Suites / 48 Tests grün (nach G6 v1 und v2).
- Backend gezielt: `test_retention.py` + neu `test_retention_db.py` → 20/20 grün (SQLite via `DATABASE_URL=sqlite+aiosqlite:...`).
- Volle Backend-Unit-Suite in der Sandbox nur eingeschränkt aussagefähig: **`backend/tests/conftest.py` fehlt im Repo** (wird von `tests/integration/conftest.py` importiert → Integrationstests aus frischem Clone nicht startbar; vermutlich untracked auf der Dev-Maschine — bitte einchecken). Ebenso ist `eval_data.tar.gz` ein 43-Byte-Fehlertext-Platzhalter, kein Archiv. Beides vorbestehend, keine Regression dieses Sprints. **Empfehlung: volle Suite einmal auf der Dev-Maschine laufen lassen.**

## 5. Offene Punkte (Verantwortliche)

| # | Punkt | Wer |
|---|---|---|
| O1 | DPA unterschreiben + einreichen (Signaturblatt), Eingangsbestätigung ablegen; Account auf Klinik-Rechtsträger prüfen | Klinikleitung / Cars10 |
| O2 | Special-Categories-Notifikation senden (Entwurf im H2-Plan) | Cars10/DSB |
| O3 | H2-Checkpunkt 3.5: At-Rest-Verschlüsselung EU-RO-1 klären → ggf. Härtungs-Mini-Sprint | Cars10 (+ ich) |
| O4 | TIA zeichnen (inkl. DPF-Check, § 203-Votum als härtester Punkt), DSFA erstellen, Einwilligung v1.1 finalisieren/drucken | DSB/Jurist |
| O5 | Schulung der 10 Pilot-Nutzer durchführen + Teilnahme dokumentieren | Klinik |
| O6 | Go-Live-Gate-Checkliste (H2 Schritt 8) abhaken vor erstem Echtdaten-Job | gemeinsam |
| O7 | `tests/conftest.py` und valides `eval_data.tar.gz` einchecken | Cars10 |
| O8 | G5 Systemdossier (2–3 PT, kann ich weitgehend allein) · G4 DSFA-Zuarbeit · On-Prem Case 0 parallel weiterverfolgen | Cars10 / DSB |
