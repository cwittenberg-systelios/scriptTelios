# Transfer Impact Assessment (TIA) — scriptTelios auf RunPod

**Entwurf 1.0 · 12.08.2026 · Zur Prüfung, Ergänzung und Zeichnung durch DSB**
**Vorlage vorbefüllt durch Entwicklung; Bewertungsfelder [DSB] sind offen.**

## 1. Transferbeschreibung

| Feld | Inhalt |
|---|---|
| Datenexporteur | sysTelios Klinik (Verantwortlicher, DE) |
| Datenimporteur | Runpod Inc., 1181 Nixon Dr. #1158, Moorestown NJ 08057, USA (Auftragsverarbeiter) |
| Transfermechanismus | SCC 2021/914 Modul 2 (Controller→Processor), inkorporiert in Runpod-DPA Attachment 3; Recht: Irland |
| Datenkategorien | Gesundheitsdaten (Art. 9): Audioaufnahmen von Therapiesitzungen, Transkripte, klinische Dokumente/Entwürfe, Diagnosen (ICD-10); Identifikationsdaten (Name in Quelldokumenten, in Outputs nur Initialen) |
| Betroffene | Patient:innen der Klinik (Pilotgruppe), mittelbar Therapeut:innen (User-ID) |
| Verarbeitungsort | Dedizierter GPU-Pod, RunPod **Secure Cloud**, Region: **EU-RO-1** (Rumänien, EU) — bestätigt 12.08.2026 |
| Physischer Datenfluss | Upload via Cloudflare-Tunnel (TLS) → Verarbeitung im Pod (lokale Modelle, keine externen KI-APIs) → Abruf des Outputs → automatische Löschung (s. §4) |
| Regelmäßigkeit | Laufend während Pilotphase (~10 Nutzer:innen), zeitlich befristet bis On-Prem-Migration |

## 2. Rechtslage im Drittland (USA)

- **FISA 702:** RunPod ist als Cloud-/Hosting-Provider potenziell „electronic communication service provider". Relevanz: Anordnungen zielen typischerweise auf Kommunikationsdienste und bestimmte Selektoren; ein GPU-Vermieter ohne inhaltlichen Datenzugriff ist ein untypisches Ziel. [DSB: Bewertung]
- **CLOUD Act:** Herausgabepflicht für Daten „in possession, custody or control" von US-Unternehmen, auch bei EU-Speicherung. Entscheidend: Hat RunPod tatsächlich Zugriff? Laut DPA/ToS kein inhaltlicher Zugriff auf Pod-Daten; Administrationszugriff auf Infrastrukturebene besteht jedoch strukturell. [DSB: Bewertung]
- **EU-US Data Privacy Framework:** RunPod-Zertifizierung unter dem DPF: [prüfen unter dataprivacyframework.gov — falls aktiv zertifiziert, Angemessenheitsbeschluss als zusätzliche Absicherung neben SCCs vermerken]

## 3. Eintrittswahrscheinlichkeit für diesen Workload

Faktoren, die das praktische Zugriffsrisiko senken:
1. **Begrenzte Datenhaltung:** Im Produktivfluss transient (Audio nach Transkription gelöscht; Uploads max. 24 h; Transkripte nach Abruf gelöscht; Berichte verbleiben in der Klinik-Krankenakte, nicht im Pod). Zusätzlich werden Verarbeitungsartefakte (Transkripte, Prompts, generierte Entwürfe, Qualitäts-/Feedbackdaten) während der Entwicklungsphase **zeitlich begrenzt zu Auswertungs- und Analysezwecken** vorgehalten — keine dauerhafte Speicherung. Maximale Vorhaltefrist: [festlegen, Vorschlag: 90 Tage], danach Löschung oder Anonymisierung. [DSB: Frist bestätigen]
2. **Kein inhaltlicher Anbieterzugriff by design:** Verarbeitung ausschließlich in kundengesteuerten Containern; keine RunPod-seitige Analyse (ToS-Verbot für Hosts, DPA §3).
3. **Kein attraktives Zielprofil:** Psychotherapeutische Dokumentation einer deutschen Klinik ohne Bezug zu US-Ermittlungsinteressen. [DSB: Würdigung]
4. Historie: keine bekannten Herausgabeersuchen gegen RunPod. [DSB: ggf. Transparenzbericht anfragen]

## 4. Bestehende technische und organisatorische Maßnahmen

TLS in Transit (Cloudflare-Tunnel); HMAC-Authentifizierung mit Replay-Schutz; Rate-Limiting; Audit-Logging ohne Inhalte; Datenminimierung in Logs (Größenklassen statt Werte); Retention-Task (6-h-Zyklus); Initialen statt Klarnamen in Outputs; keine externen KI-Dienste; RunPod: SOC 2 Type II, unabhängig auditierte GDPR/HIPAA-Konformität (02/2026), Secure-Cloud-RZ T3/T4.

## 5. Zusätzliche Maßnahmen (Ergebnis Schritt 3.5 eintragen)

☐ Plattformseitige At-Rest-Verschlüsselung der gewählten Region: [ja/nein/Details]
☐ Falls nein: applikationsseitige Verschlüsselung des Upload-/Workspace-Bereichs [umgesetzt am: ___]
☐ Befristung: Transfer endet mit On-Prem-Migration (Ziel: [Datum eintragen])
☐ Vorhaltefrist für Entwicklungs-Analysedaten festgelegt und in Retention-Konfiguration technisch umgesetzt [Frist: ___]
☐ **Einwilligungsdeckung der Entwicklungs-Vorhaltung geprüft [DSB]:** Einwilligung §6 deckt die Verarbeitung ausschließlich zum Zweck der Erstellung der medizinischen Dokumentation — die zeitlich begrenzte Auswertung identifizierbarer Daten zur Systementwicklung ist ein eigener Zweck. **Entscheidung: Option (a).** Passus §6a (Qualitätssicherung Einführungsphase, max. [90 Tage], danach Löschung/Anonymisierung) liegt vor in g3a_einwilligung_v1_1_formulierung.md; DSB prüft Kopplungsverbots-Würdigung und bestätigt die Frist.

## 6. Gesamtbewertung [DSB]

☐ Transfer kann auf Grundlage der SCCs mit den unter §4/§5 genannten Maßnahmen durchgeführt werden.
☐ Auflagen: ___________
☐ Wiedervorlage bei: Änderung Sub-Processor-Liste, Regionwechsel, Rechtsänderung, spätestens: ___

| Rolle | Name | Datum | Unterschrift |
|---|---|---|---|
| Datenschutzbeauftragte:r | | | |
| Verantwortlicher (Klinikleitung) | | | |
