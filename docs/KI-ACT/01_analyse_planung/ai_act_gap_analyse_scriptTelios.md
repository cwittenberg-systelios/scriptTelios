# scriptTelios – AI-Act-Standortbestimmung (ab 50 Mitarbeitende)

**Stand:** 12.08.2026 · Basis: datenschutzaudit_scriptTelios_v2 (11.04.2026), einwilligung_scriptTelios v1.0, eval_report (22.04.2026), EQS-Guide + Checklisten, hardware_optionen_scriptTelios

**Wichtig zum Timing:** Der 2. August 2026 ist verstrichen. Die zentralen Betreiber-/Anbieterpflichten des AI Act **gelten bereits**, nicht erst „demnächst". Die KI-Kompetenz-Pflicht (Art. 4) gilt seit Februar 2025.

> Hinweis: Technische Einschätzung, keine Rechtsberatung. Die Risikoklassifizierung sollte juristisch/vom DSB bestätigt werden.

---

## 1. Einordnung unter dem AI Act

### Rollen
| Rolle | Trifft zu? | Begründung |
|---|---|---|
| **Anbieter** (des KI-Systems scriptTelios) | **Ja** | Eigenentwicklung, Betrieb unter eigenem Namen der Klinik. |
| **Betreiber** | **Ja** | Einsatz unter eigener Autorität im Klinikbetrieb. |
| GPAI-Modell-Anbieter | Nein | Gemma 4 / Mistral werden unverändert (kein Finetuning) eingesetzt; Modellpflichten liegen bei Google/Mistral. Apache 2.0. |

Doppelrolle Anbieter+Betreiber heißt: beide Pflichtenkataloge, aber in der Praxis überlappend.

### Risikoklasse (Arbeitshypothese)
**Voraussichtlich kein Hochrisiko-System:**
- Keine Annex-III-Kategorie einschlägig (keine Biometrie, kein Zugang zu Leistungen, keine Bewertung natürlicher Personen mit Rechtsfolgen).
- Kein Medizinprodukt i.S. MDR: reine Dokumentationsunterstützung ohne diagnostische/therapeutische Zweckbestimmung (MDCG-Abgrenzung: Dokumentation/Archivierung ≠ Medizinprodukt). Damit greift auch der Hochrisiko-Pfad über harmonisierte Produktvorschriften nicht.
- Vollständige menschliche Kontrolle: jeder Output ist Entwurf, fachliche Verantwortung explizit bei Therapeut:in (so auch in Einwilligung §1 verankert).

**Aber:** Diese Einstufung ist bisher nirgends **formal dokumentiert** — genau das verlangt der risikobasierte Ansatz als ersten Schritt (EQS-Checkliste Schritte 3–6). Ohne dokumentierte Klassifizierung ist jede „wir sind kein Hochrisiko"-Aussage angreifbar.

**Einschlägige Pflichten damit:**
- Art. 4 KI-Kompetenz (seit 02/2025)
- Art. 50 Transparenz (Interaktion mit KI, KI-generierte Inhalte). Die Kennzeichnungspflicht für veröffentlichte Texte entfällt bei menschlicher Prüfung — interne Klinikdokumentation ist ohnehin nicht „öffentlich" — Kennzeichnung ist hier dennoch Best Practice und Awareness-Instrument.
- Basis-Dokumentationspflicht
- DSGVO parallel (Art. 9, 25, 32, 35)

---

## 2. Was ist bereits da (Ist-Stand, gemappt auf AI-Act-Prinzipien)

| AI-Act-Prinzip | Vorhandene Umsetzung | Bewertung |
|---|---|---|
| **Menschliche Aufsicht** | Editierbare Previews in allen Workflows; fachliche Letztverantwortung dokumentiert (Einwilligung §1); QC-Warnungen (Wir-Form, pathologisierende Sprache) als Awareness-Instrument | ✅ stark |
| **Robustheit / Datenqualität** | Source Gate (Job-Abbruch statt Konfabulation); Quellentreue-QC; Coverage-Gap-Tracking mit CRITICAL-Issues; Testwerte nur aus Quelldokumenten | ✅ stark |
| **Nachvollziehbarkeit / Logging** | audit.log (JSON, ohne Inhalte, Rotation); prompts.log ↔ feedback.log via job_id verknüpfbar; Eval-Framework mit Scoring (12 Testfälle, Ø 85,3 %) | ✅ gut |
| **Transparenz ggü. Betroffenen** | Einwilligung v1.0: KI-Einsatz, Datenkategorien, Speicherfristen, Rechte, optionale Forschungsklausel mit Anonymisierungs-Negativliste | ✅ gut (ein Konsistenzproblem, s. Gap G3) |
| **Datenschutz-Grundlagen** | Audit v2: 9/13 Empfehlungen umgesetzt (HMAC-Auth, CORS, Rate-Limiting, Datenminimierung, Retention, Löschkonzept) | ✅ Code-Anteil erledigt |
| **Governance im System** | BASE_PROMPTS als nicht editierbarer Enforcement-Kernel; Modell-Routing dokumentiert | ✅ |
| **Qualitätssicherung** | Eval-Report mit Workflow-Scores, Min-Schwelle 70 % | ✅ Grundlage vorhanden, Prozess fehlt (G8) |

**Fazit Ist-Stand:** Die *inhaltlichen* AI-Act-Prinzipien (Aufsicht, Robustheit, Transparenz) sind technisch überdurchschnittlich gut umgesetzt — besser als bei den meisten Copilot-Deployments, auf die die Checklisten im Anhang zielen. Was fehlt, ist fast ausschließlich **Formalisierung und Dokumentation**, kaum Code.

---

## 3. Gaps mit Priorisierung und Aufwand

### P0 — sofort (Pflichten gelten bereits)

| # | Gap | Maßnahme | Aufwand | Wer |
|---|---|---|---|---|
| **G1** | Risikoklassifizierung nicht dokumentiert | Klassifizierungsdokument: Zweckbestimmung, MDR-Abgrenzung, Annex-III-Prüfung, Rollen (Anbieter+Betreiber), Ergebnis „kein Hochrisiko" mit Begründung | 1–2 PT Entwurf + externe/DSB-Bestätigung | Cars10 + DSB/Jurist |
| **G2** | KI-Kompetenz-Nachweis fehlt (Art. 4, Pflicht seit 02/2025) | Schulungsunterlage für die 10 Pilot-Nutzer: Funktionsweise, Grenzen (Konfabulation, Coverage-Gaps), QC-Interpretation, Pflicht zur Prüfung; Teilnahme dokumentieren (Checkliste 2 im Anhang als Gerüst) | 1–2 PT Material + ~1 h/Nutzer | Cars10 + Klinik |
| **G3** | **Konsistenzproblem Einwilligung ↔ RunPod:** Einwilligung §1 verspricht „ausschließlich Server der sysTelios Klinik" — der Pilot läuft auf RunPod (US-Anbieter, Drittlandrisiko trotz EU-Region) | Entweder (a) Einwilligung v1.1 mit Entwicklungsphasen-Passus + AVV/SCC RunPod dokumentieren, oder (b) On-Prem-Migration **vor** Echtdaten-Pilot (löst G3 sauberer, s. Abschnitt 4) | 0,5–1 PT Text bzw. Hardware-Beschaffung | DSB + Cars10 |

### P1 — vor/mit dem Zehn-Nutzer-Pilot

| # | Gap | Maßnahme | Aufwand | Wer |
|---|---|---|---|---|
| **G4** | Formelle DSFA fehlt (Art. 35 DSGVO; Audit-Punkt O4) | DSFA auf Basis Audit v2 + Klassifizierungsdokument (G1) | 2–3 PT gemeinsam | DSB, Zuarbeit Cars10 |
| **G5** | Technische Systemdokumentation verstreut | Konsolidiertes Systemdossier: Architektur, Modelle+Versionen, Datenflüsse, bekannte Grenzen (aus Eval: z. B. 67-%-Fall schulangst-jugendliche), Retention, Auth | 2–3 PT (viel existiert bereits) | Cars10 |
| **G6** | Keine KI-Kennzeichnung im Output | Fußzeile in generierten Dokumenten: „KI-gestützt erstellt (scriptTelios), fachlich geprüft und verantwortet durch [Kürzel]" + UI-Hinweis. **Einziges echtes Code-Item** — kandidiert für einen Mini-Sprint | 0,5 PT inkl. Tests | Cars10 |
| **G7** | Incident-Prozess nicht formalisiert | Kurzes Prozessdokument: Fehlfunktion → Nutzung stoppen, Meldeweg (Telegram-Alerts existieren technisch bereits), Bewertung, ggf. Meldung | 0,5–1 PT | Cars10 + Klinik |

### P2 — mit On-Prem-Aufbau / laufendem Betrieb

| # | Gap | Maßnahme | Aufwand | Wer |
|---|---|---|---|---|
| **G8** | QS-Prozess nicht verstetigt | Eval-Suite als Regelprozess definieren (z. B. vor jedem Release + quartalsweise), Ergebnisse ablegen | 0,5 PT Prozess, läuft dann mit | Cars10 |
| **G9** | Audit-Infrastruktur-Reste K2/E3/E5 | TLS (Klinik-CA statt Cloudflare-Tunnel), LUKS-Disk-Encryption, Off-Site-Backup — beim On-Prem-Setup | 1–2 PT im Hardware-Setup | Klinik-IT + Cars10 |
| **G10** | Klinikweites KI-Inventar/Policy fehlt (Schatten-KI, Copilot etc.) | Bestandsaufnahme + KI-Policy — betrifft die Klinik als Ganzes, nicht scriptTelios | Org-Aufgabe | DSB/Klinikleitung |
| **G11** | Log-Aufbewahrung nur bedingt AI-Act-fest | Falls Klassifizierung doch Hochrisiko ergibt: Logs ≥ 6 Monate (audit.log aktuell 90 Tage). Nur RETENTION-Dict-Änderung | < 0,5 PT, nur bei Bedarf | Cars10 |

**Gesamtaufwand P0+P1: ~8–12 PT**, davon **nur ~0,5 PT Code** (G6). Der Rest ist Dokumentation und Organisation.

---

## 4. RunPod-Status vs. interne Lösung

### RunPod heute (Entwicklungs-/Pilotumgebung)
| Aspekt | Status |
|---|---|
| Code-Härtung | ✅ v12+-Stand: HMAC-Auth, CORS, Audit-Log, Retention, Rate-Limiting, Datenminimierung |
| TLS | ⚠️ Cloudflare Named Tunnel (K2 formal offen) |
| Disk-Encryption | ❌ nicht steuerbar (E3) |
| Off-Site-Backup | ❌ (E5) |
| Drittlandproblematik | ⚠️ US-Anbieter (CLOUD Act) — auch mit EU-Region ein Restrisiko; kollidiert mit Einwilligungs-Zusage „ausschließlich Klinik-Server" (G3) |

**Bewertung:** Vertretbar für Entwicklung und Tests mit synthetischen/Testdaten. **Nicht deckungsfähig für Echtbetrieb mit Patientendaten** unter der aktuellen Einwilligung v1.0.

### Ausblick On-Prem
- **Case 0** (1× RTX PRO 4500 Blackwell 32 GB, ~3.900–4.200 € Gesamteinstieg) ist die 1:1-Migration des heutigen Pod-Betriebszustands: gleiche GPU, `cuda_v13` bleibt gültig, gleicher Single-LLM-Betrieb mit Modellwechsel.
- Löst **G3 vollständig** (Einwilligungszusage wird wahr), ermöglicht **G9** (LUKS beim Setup, TLS via Klinik-CA, Backup in Klinik-Infrastruktur).
- Ausbaupfad auf 96 GB ohne Plattformwechsel dokumentiert (2./3. Karte à ~2.500 €).

**Empfehlung:** On-Prem Case 0 **vor** dem Echtdaten-Pilot beschaffen. Damit fällt G3(a) (Einwilligungs-Umformulierung + RunPod-AVV-Konstrukt) komplett weg — der sauberere und langfristig günstigere Weg als juristische Absicherung einer US-Cloud-Zwischenlösung.

---

## 5. Vorgeschlagene Reihenfolge

1. **G1** Klassifizierungsdokument (schaltet G4/G5 frei)
2. **G6** Kennzeichnungs-Mini-Sprint (einziges Code-Item, schnell sichtbar)
3. **G2** Schulungspaket Pilot-Nutzer
4. Hardware-Entscheid On-Prem (Case 0) → parallel **G9**
5. **G4** DSFA + **G5** Systemdossier
6. **G7/G8** Prozesse, dann Pilot-Start

---

## Entscheidungen

- **D1:** G6 (KI-Kennzeichnung im Output) als nächsten Mini-Sprint umsetzen — Sprintplan + Patch auf `v19_QA_v02`?
- **D2:** G1-Klassifizierungsdokument als Entwurf erstellen (md, zur DSB-Vorlage)?
- **D3:** G2-Schulungsunterlage für die 10 Pilot-Nutzer entwerfen (auf Basis Checkliste 2 aus dem Anhang)?
- **D4:** Erst Hardware-Entscheid Case 0 finalisieren, Rest danach?
