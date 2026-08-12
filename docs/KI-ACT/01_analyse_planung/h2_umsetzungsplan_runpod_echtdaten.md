# H2-Umsetzungsplan — Echtdaten-Pilot auf RunPod

**Stand: 12.08.2026 · Ziel: rechtskonformer Pilotbetrieb mit Patientendaten auf dem Pod, ohne auf On-Prem-Hardware zu warten**

Reihenfolge ist Abhängigkeitsreihenfolge. Schritte 1–3 kannst du selbst anstoßen, 4–6 brauchen DSB/Jurist, 7 ist Technik (ich), 8 ist der Go-Live-Gate-Check.

---

## Schritt 1 — DPA abschließen (du + Klinikleitung, ~0,5 PT, Durchlauf wenige Tage)

1. DPA von https://www.runpod.io/legal/data-processing-agreement herunterladen. **Nicht verändern** — das DPA sagt ausdrücklich: jede inhaltliche Änderung durch den Kunden verhindert das Zustandekommen.
2. Signaturblock ausfüllen: Vertragspartei ist der Rechtsträger der Klinik (nicht du persönlich) — **Unterschrift durch Geschäftsführung/Klinikleitung**. Zugehöriger RunPod-Account muss auf die Klinik laufen (prüfen: läuft der Account auf dich privat? Dann vorher auf Klinik-Organisation umstellen, sonst passt die Vertragspartei nicht).
3. Unterschriebenes DPA per E-Mail an die im DPA genannte Adresse senden (auf der Website ist die Adresse durch Cloudflare-E-Mail-Schutz maskiert — im Browser geöffnet ist sie sichtbar). Eingangsbestätigung anfordern und ablegen.
4. Damit sind automatisch die SCCs (EU 2021/914, Modul 2, Recht: Irland) und die Attachments (TOMs, Sub-Processor-Liste) wirksam — keine separate SCC-Unterzeichnung nötig.

## Schritt 2 — Special-Categories-Notifikation (du, 0,1 PT, zusammen mit Schritt 1)

Das DPA verlangt **schriftliche Vorab-Mitteilung**, bevor Art.-9-Daten (Gesundheitsdaten) in den Verarbeitungsumfang aufgenommen werden. Entwurf (mit DPA einreichen oder direkt danach):

> Subject: Notification pursuant to Section 3/5 of the Runpod DPA — Special Categories of Personal Data
>
> Dear Runpod Team,
>
> further to our executed Data Processing Agreement dated [DATE], we hereby notify you in writing, pursuant to Sections 3 and 5 of the DPA, that the scope of processing under our account [ACCOUNT/ORG-ID] will include Special Categories of Personal Data within the meaning of Art. 9(1) GDPR, specifically health data (audio recordings and transcripts of therapy sessions, clinical documentation) processed on dedicated GPU pods in EU regions.
>
> We confirm that: (i) a valid legal basis under Art. 9(2)(a) GDPR (explicit consent) in conjunction with Art. 9(2)(h) and 9(3) GDPR has been identified and documented prior to any transmission; (ii) evidence of such legal basis will be provided upon request; (iii) processing occurs exclusively within self-managed workloads on dedicated pods — Runpod personnel have no content-level access.
>
> Please confirm receipt of this notification.
>
> [Klinik, Unterschrift Verantwortlicher/DSB]

## Schritt 3 — Pod-Konfiguration verifizieren (du, 0,5 PT)

Gate-Kriterien, die *vor* der TIA-Finalisierung feststehen müssen:

| # | Prüfpunkt | Soll |
|---|---|---|
| 3.1 | **Secure Cloud, nicht Community Cloud.** Community-Cloud-Pods laufen auf Hardware von Dritt-Hosts — für Patientendaten ausgeschlossen. | Pod in Secure Cloud (T3/T4-RZ, SOC-2-zertifiziert) |
| 3.2 | **EU-Region fest gewählt** + Security-&-Compliance-Filter bei Pod-Erstellung genutzt; Region dokumentieren (Screenshot) | EU-RZ, dokumentiert |
| 3.3 | Retention aktiv (Audio nach Transkription gelöscht, Uploads 24 h, Transkripte nach Abruf) — `retention_task`-Logs prüfen | ✓ vorhanden, Nachweis ablegen |
| 3.4 | Zugriff nur via HMAC-Auth + Cloudflare-Tunnel (TLS in Transit) | ✓ vorhanden, dokumentieren |
| 3.5 | **Verschlüsselung at rest auf dem Workspace-Volume**: prüfen, was die gewählte Secure-Cloud-Region bietet; falls keine Plattform-Verschlüsselung → applikationsseitige Härtung erwägen (z. B. verschlüsselter Container für Uploads). Ergebnis fließt als „zusätzliche Maßnahme" in die TIA. | klären |

## Schritt 4 — TIA finalisieren (DSB, mit meiner Vorlage, 0,5–1 PT)

Vorbefülltes Grundgerüst liegt bei (`h2_tia_entwurf_runpod.md`): Transfer-Beschreibung, US-Rechtslage (FISA 702 / CLOUD Act), Eintrittswahrscheinlichkeit für diesen konkreten Workload, bestehende + zusätzliche Maßnahmen. DSB prüft, ergänzt, zeichnet.

## Schritt 5 — § 203 StGB-Bewertung (DSB/Jurist, 0,5–1 PT) — **kritischster Punkt**

RunPod wird keine deutsche Verpflichtungserklärung nach § 203 Abs. 4 StGB unterzeichnen. Die Bewertung muss daher begründen, ob die DPA-Vertraulichkeitsverpflichtungen des RunPod-Personals plus der Umstand, dass RunPod/Hosts laut ToS und DPA keinen inhaltlichen Zugriff auf Pod-Daten haben („Runpod does not have these rights"), als „sonstige mitwirkende Person"-Konstellation tragfähig sind — oder ob die Position vertreten wird, dass RunPod mangels Zugriffs auf Klartext-Geheimnisse gar kein Offenbaren i. S. v. § 203 vorliegt. **Das ist eine juristische Wertung, die ich nicht treffen kann; ohne positives Votum hier kein Echtdaten-Go.**

## Schritt 6 — DSFA + Einwilligung v1.1 (DSB, 2–3 PT)

- DSFA (Art. 35) auf Basis Datenschutzaudit v2 + G1-Klassifizierung + TIA. Bei Cloud-Zwischenlösung ist die DSFA nicht mehr aufschiebbar (G4 zieht von P1 auf P0).
- Einwilligung v1.1 finalisieren: geschärfter §1 + **§1a aktiv** (Formulierung liegt vor in `g3a_einwilligung_v1_1_formulierung.md`), DSB-Kontaktdaten einsetzen, drucken. Pilot-Patient:innen unterschreiben v1.1 — v1.0-Unterschriften decken den Pod-Betrieb **nicht**.
- Verarbeitungsverzeichnis (Art. 30) um Eintrag scriptTelios/RunPod inkl. Drittlandtransfer-Mechanismus ergänzen.

## Schritt 7 — Technik-Resterledigung (ich, nach 3.5-Ergebnis)

Falls 3.5 applikationsseitige Härtung ergibt: eigener Mini-Sprint (Sprintplan zuerst, wie üblich). Sonst keine Codeänderung nötig.

## Schritt 8 — Go-Live-Gate (gemeinsam, 0,5 PT)

Checkliste vor dem ersten Echtdaten-Job:
☐ DPA-Eingangsbestätigung liegt vor · ☐ Special-Categories-Notifikation bestätigt · ☐ Pod = Secure Cloud EU, dokumentiert · ☐ TIA gezeichnet · ☐ § 203-Votum positiv · ☐ DSFA abgeschlossen · ☐ Einwilligung v1.1 im Einsatz, unterschrieben für jede:n Pilot-Patient:in · ☐ Schulung aller 10 Nutzer:innen dokumentiert (G2) · ☐ Verarbeitungsverzeichnis aktualisiert

**Realistische Gesamtdauer: 1–2 Wochen** (kritischer Pfad: § 203-Votum und DSFA beim DSB), gegenüber Monaten für Hardware.

---

## Wichtig — Exit bleibt geplant

H2 ist die Brücke, nicht das Ziel. Bei On-Prem-Migration: §1a entfällt für Neue (v1.2), TIA/DPA werden obsolet, DSFA wird um den Infrastrukturwechsel fortgeschrieben. Die On-Prem-Beschaffung (Case 0) sollte parallel weiterlaufen.
