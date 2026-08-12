# F1 — Recherche: RunPod AVV / DSGVO-Status

**Stand: 12.08.2026 · Quellen: runpod.io/legal/data-processing-agreement, docs.runpod.io, RunPod-Pressemitteilung 02/2026, RunPod Help Center**

## Befund

**1. Ein Standard-DPA existiert, gilt aber NICHT automatisch.** RunPod veröffentlicht ein Standard-DPA (Art.-28-AVV mit inkorporierten EU-Standardvertragsklauseln 2021/914, Modul 2 Controller→Processor, UK-/Schweiz-Addenda). Entscheidend: Das DPA wird erst bindend, wenn der Kunde den Signaturblock ausfüllt und das unterschriebene Dokument per E-Mail an RunPod einreicht — es ist **nicht** automatisch Bestandteil der Nutzungsbedingungen. **Da kein extra Vertrag geschlossen wurde, besteht aktuell kein wirksamer AVV.**

**2. Konsequenz:** Verarbeitung *personenbezogener* Daten auf dem Pod wäre derzeit ein Verstoß gegen Art. 28 Abs. 3 DSGVO (Auftragsverarbeitung ohne Vertrag). Solange ausschließlich synthetische Test-/Eval-Daten verarbeitet werden (aktueller Zustand), ist das unkritisch — es markiert aber eine harte Grenze vor jedem Echtdaten-Einsatz.

**3. Besondere Kategorien (Art. 9, Gesundheitsdaten):** Das DPA erlaubt sie grundsätzlich, verlangt aber vom Kunden: (a) dokumentierte Rechtsgrundlage nach Art. 9 Abs. 2 *vor* Übermittlung, (b) Nachweis auf Anfrage, (c) **vorherige schriftliche Notifikation an RunPod**, bevor besondere Kategorien in den Verarbeitungsumfang aufgenommen werden.

**4. Drittlandtransfer:** SCCs sind im DPA als Attachment 3 enthalten und greifen mit dessen Abschluss. Ein Transfer-Impact-Assessment (TIA) bleibt Kür des Verantwortlichen (US-Mutterkonzern, CLOUD-Act-Restrisiko trotz EU-Region — Region-Pinning ist im DPA nur als „reasonable efforts" formuliert). Sub-Processor-Liste umfasst u. a. AWS, Google Cloud, Datadog.

**5. Compliance-Posture:** RunPod ist seit 02/2026 unabhängig auditiert (GDPR, HIPAA), SOC 2 Type II, Trust Center mit Dokumentation; Pods mit Security-&-Compliance-Filter.

**6. § 203 StGB (Schweigepflicht):** Für Berufsgeheimnisträger reicht ein DSGVO-AVV allein nicht; die Einbindung mitwirkender Dienstleister erfordert Verpflichtung auf den Geheimnisschutz (§ 203 Abs. 4 StGB). Das RunPod-DPA enthält Vertraulichkeitsverpflichtungen des Personals — ob diese den § 203-Anforderungen genügen, muss der DSB/Jurist bewerten. Erfahrungsgemäß ist genau dieser Punkt bei US-Anbietern der härteste.

## Handlungsoptionen

| Option | Inhalt | Aufwand | Bewertung |
|---|---|---|---|
| **H1** | Status quo festschreiben: Pod bleibt reine Entwicklungs-/Testumgebung mit synthetischen Daten, Echtdaten erst on-prem. Kein DPA nötig. §1a der Einwilligung v1.1 entfällt. | 0 | **Empfohlen.** Sauberster Pfad, deckt sich mit geplanter On-Prem-Migration (Case 0) und vermeidet die § 203-/TIA-Baustelle vollständig. |
| **H2** | DPA unterschreiben + einreichen, Special-Categories-Notifikation an RunPod, TIA erstellen, § 203-Bewertung durch DSB → Echtdaten-Pilot auf dem Pod unter Einwilligung v1.1 §1a. | ~2–4 PT (DSB/Jurist) + Restrisiko | Nur sinnvoll, wenn On-Prem sich um Monate verzögert. |
| **H3** | Hybrid: DPA vorsorglich abschließen (kostet nichts), Echtdaten trotzdem erst on-prem. | ~0,5 PT | Pragmatische Absicherung gegen versehentliche personenbezogene Daten (z. B. echte Namen in Testdokumenten). |

**Konkrete Empfehlung: H3.** DPA formal abschließen als Sicherheitsnetz, betrieblich aber H1 leben (Echtdaten erst on-prem). In G3a-Dokument: DSB-Punkte 1/2 mit diesem Befund beantworten; §1a nur aktivieren, falls doch H2 gewählt wird.
